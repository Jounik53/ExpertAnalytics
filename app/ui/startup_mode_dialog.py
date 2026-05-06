from __future__ import annotations

import tkinter as tk

from app.application.helpers.modes import MODE_LABELS, MODE_ORDER, mode_label


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
