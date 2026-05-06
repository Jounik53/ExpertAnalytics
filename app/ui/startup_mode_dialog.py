from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import threading
from typing import Callable, Any

from app.application.helpers.modes import MODE_LABELS, MODE_ORDER, mode_label
from app.ui.services.theme_service import ThemeService


def choose_start_mode(default_mode: str) -> str:
    selected = {"mode": default_mode}

    root = tk.Tk()
    root.title("Выбор режима")
    root.geometry("430x420")
    root.resizable(False, False)

    pad = {"padx": 14, "pady": 6}
    default_label = mode_label(default_mode)
    tk.Label(root, text="Выберите режим запуска", font=("Segoe UI", 12, "bold")).pack(pady=(14, 8))
    tk.Label(root, text=f"Режим из настроек: {default_label}").pack(pady=(0, 8))

    def _pick(mode: str) -> None:
        selected["mode"] = mode
        root.destroy()

    for mode in MODE_ORDER:
        tk.Button(root, text=MODE_LABELS[mode], command=lambda m=mode: _pick(m)).pack(fill=tk.X, **pad)

    root.protocol("WM_DELETE_WINDOW", lambda: _pick(default_mode))
    root.mainloop()
    return str(selected["mode"])


def show_startup_loader(
    theme: str,
    load_steps: list[tuple[str, Callable[[], Any]]],
) -> dict[str, object] | None:
    result: dict[str, object] = {}
    total = max(1, len(load_steps))

    root = tk.Tk()
    root.title("Запуск ExpertAnalytics")
    root.geometry("520x180")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    state = {"done": False, "error": None, "cancelled": False}

    def _cancel() -> None:
        state["cancelled"] = True
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _cancel)

    style = ttk.Style(root)
    style.theme_use("clam")
    theme_service = ThemeService()
    current_theme = theme_service.apply(root, theme)
    colors = theme_service.colors(current_theme)

    frame = ttk.Frame(root)
    frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

    title = ttk.Label(frame, text="Подготовка данных перед запуском", style="Header.TLabel")
    title.pack(anchor=tk.W, pady=(0, 8))

    status_var = tk.StringVar(value="Инициализация...")
    status = ttk.Label(frame, textvariable=status_var)
    status.pack(anchor=tk.W, pady=(0, 8))

    progress_var = tk.IntVar(value=0)
    progress = ttk.Progressbar(frame, maximum=100, variable=progress_var, mode="determinate")
    progress.pack(fill=tk.X)

    details = tk.Text(frame, height=4, relief=tk.SOLID, bd=1)
    details.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
    details.configure(
        background=colors["panel"],
        foreground=colors["text"],
        insertbackground=colors["text"],
        selectbackground=colors["select_bg"],
        selectforeground=colors["select_fg"],
        highlightbackground=colors["border"],
        highlightcolor=colors["accent"],
    )

    def _worker() -> None:
        try:
            for idx, (label, fn) in enumerate(load_steps, start=1):
                if state["cancelled"]:
                    return
                status_var.set(f"{label}...")
                details.insert(tk.END, f"[~] {label}\n")
                details.see(tk.END)
                root.update_idletasks()
                result[label] = fn()
                percent = int((idx / total) * 100)
                progress_var.set(max(0, min(100, percent)))
                details.insert(tk.END, f"[ok] {label}\n")
                details.see(tk.END)
                root.update_idletasks()
            status_var.set("Подготовка завершена")
            progress_var.set(100)
            state["done"] = True
        except Exception as exc:
            state["error"] = exc
        finally:
            root.after(300, root.destroy)

    threading.Thread(target=_worker, daemon=True).start()
    root.mainloop()

    if state["cancelled"]:
        return None
    if state["error"] is not None:
        raise RuntimeError(f"Ошибка загрузки на старте: {state['error']}")
    return result


def choose_start_mode_themed(default_mode: str, theme: str) -> str | None:
    selected = {"mode": default_mode, "picked": False}
    root = tk.Tk()
    root.title("Выбор режима")
    root.geometry("430x420")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    style = ttk.Style(root)
    style.theme_use("clam")
    theme_service = ThemeService()
    theme_service.apply(root, theme)

    pad = {"padx": 14, "pady": 6}
    default_label = mode_label(default_mode)
    ttk.Label(root, text="Выберите режим запуска", style="Header.TLabel").pack(pady=(14, 8))
    ttk.Label(root, text=f"Режим из настроек: {default_label}").pack(pady=(0, 8))

    def _pick(mode: str) -> None:
        selected["mode"] = mode
        selected["picked"] = True
        root.destroy()

    for mode in MODE_ORDER:
        ttk.Button(root, text=MODE_LABELS[mode], command=lambda m=mode: _pick(m)).pack(fill=tk.X, **pad)

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    if not bool(selected.get("picked")):
        return None
    return str(selected["mode"])
