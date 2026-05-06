from app.bootstrap import build_app
from app.adapters.persistence.settings_repo import JsonSettingsRepository
from app.application.services.settings_service import SettingsService
import ctypes
import os
import sys
import tkinter as tk
from pathlib import Path


MODE_LABELS = {
    "general": "Общий",
    "scanning": "Сканирование",
    "processes": "Процессы",
    "startup": "Автозагрузка",
    "services": "Службы",
    "drivers": "Драйверы",
    "heuristics": "Эвристика",
}
MODE_ORDER = ["general", "scanning", "processes", "startup", "services", "drivers", "heuristics"]


def main() -> None:
    if os.name == "nt" and not _is_admin():
        if _relaunch_as_admin():
            return
    current_mode = _choose_start_mode(_load_default_startup_mode())
    pending_mode: dict[str, str | None] = {"next": None}

    while True:
        pending_mode["next"] = None
        app = build_app(mode=current_mode)
        app.set_mode_switch_callback(lambda next_mode: pending_mode.__setitem__("next", next_mode))
        app.run()
        if pending_mode["next"]:
            current_mode = str(pending_mode["next"])
            continue
        break


def _load_default_startup_mode() -> str:
    data_dir = Path(__file__).resolve().parent / "app" / "data"
    settings = SettingsService(JsonSettingsRepository(data_dir / "settings.json")).load()
    mode = str(getattr(settings, "startup_mode", "general") or "general").strip().lower()
    return mode if mode in MODE_LABELS else "general"


def _choose_start_mode(default_mode: str) -> str:
    selected = {"mode": default_mode}

    root = tk.Tk()
    root.title("Выбор режима")
    root.geometry("430x420")
    root.resizable(False, False)

    pad = {"padx": 14, "pady": 6}
    tk.Label(root, text="Выберите режим запуска", font=("Segoe UI", 12, "bold")).pack(pady=(14, 8))
    tk.Label(root, text=f"По умолчанию: {MODE_LABELS.get(default_mode, MODE_LABELS['general'])}").pack(pady=(0, 8))

    def _pick(mode: str) -> None:
        selected["mode"] = mode
        root.destroy()

    tk.Button(
        root,
        text=f"Открыть режим по умолчанию ({MODE_LABELS.get(default_mode, MODE_LABELS['general'])})",
        command=lambda: _pick(default_mode),
    ).pack(fill=tk.X, **pad)

    for mode in MODE_ORDER:
        tk.Button(root, text=MODE_LABELS[mode], command=lambda m=mode: _pick(m)).pack(fill=tk.X, **pad)

    root.protocol("WM_DELETE_WINDOW", lambda: _pick(default_mode))
    root.mainloop()
    return str(selected["mode"])


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin() -> bool:
    try:
        params = " ".join(f'"{arg}"' for arg in sys.argv)
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return int(rc) > 32
    except Exception:
        return False


if __name__ == "__main__":
    main()
