from app.bootstrap import build_app
from app.adapters.persistence.settings_repo import JsonSettingsRepository
from app.application.helpers.modes import normalize_mode
from app.application.helpers.windows_elevation import is_admin, relaunch_as_admin
from app.application.services.settings_service import SettingsService, AppSettings
from app.adapters.system.process_scanner import PsutilProcessScanner
from app.adapters.system.startup_scanner import RegistryStartupScanner
from app.adapters.system.service_scanner import PsutilServiceScanner
from app.adapters.system.driver_scanner import WindowsDriverScanner
import os
from pathlib import Path
from app.ui.startup_mode_dialog import choose_start_mode_themed, show_startup_loader


def main() -> None:
    if os.name == "nt" and not is_admin():
        if relaunch_as_admin():
            return
    settings = _load_settings()
    default_mode = normalize_mode(getattr(settings, "startup_mode", "general"))
    open_selected_immediately = bool(getattr(settings, "open_selected_mode_on_startup", False))

    preload = show_startup_loader(
        theme=settings.ui_theme,
        load_steps=[
            ("Получение процессов", lambda: PsutilProcessScanner().scan_processes(settings.process_high_mb, settings.process_medium_mb)),
            ("Получение автозагрузки", lambda: RegistryStartupScanner().scan_startup_entries()),
            ("Получение служб", lambda: PsutilServiceScanner().scan_services()),
            ("Получение драйверов", lambda: WindowsDriverScanner().scan_drivers()),
        ],
    )
    if preload is None:
        return

    selected_mode = default_mode if open_selected_immediately else choose_start_mode_themed(default_mode, settings.ui_theme)
    if selected_mode is None:
        return
    current_mode = selected_mode
    pending_mode: dict[str, str | None] = {"next": None}

    while True:
        pending_mode["next"] = None
        app = build_app(mode=current_mode, initial_raw={
            "processes": preload.get("Получение процессов", []),
            "startup": preload.get("Получение автозагрузки", []),
            "services": preload.get("Получение служб", []),
            "drivers": preload.get("Получение драйверов", []),
        }, skip_warmup=True)
        app.set_mode_switch_callback(lambda next_mode: pending_mode.__setitem__("next", next_mode))
        app.run()
        if pending_mode["next"]:
            current_mode = str(pending_mode["next"])
            continue
        break


def _load_settings() -> AppSettings:
    data_dir = Path(__file__).resolve().parent / "app" / "data"
    return SettingsService(JsonSettingsRepository(data_dir / "settings.json")).load()


if __name__ == "__main__":
    main()
