from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui.styles.theme_palette import DARK_THEME, LIGHT_THEME


class ThemeService:
    @staticmethod
    def normalize_theme(theme: str | None) -> str:
        value = (theme or "dark").strip().lower()
        return "light" if value == "light" else "dark"

    def colors(self, theme: str | None) -> dict[str, str]:
        return LIGHT_THEME if self.normalize_theme(theme) == "light" else DARK_THEME

    def apply(self, root: tk.Misc, theme: str, widgets: dict[str, object] | None = None) -> str:
        current = self.normalize_theme(theme)
        c = self.colors(current)
        style = ttk.Style()

        root.configure(background=c["bg"])

        style.configure(
            ".",
            background=c["bg"],
            foreground=c["text"],
            fieldbackground=c["input_bg"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            troughcolor=c["panel_alt"],
        )
        style.configure("TFrame", background=c["bg"])
        style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
        style.configure("TNotebook.Tab", background=c["panel_alt"], foreground=c["text"], padding=(10, 6))
        style.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["text"]), ("!selected", c["muted_text"])])

        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Statusbar.TFrame", background=c["panel_alt"], bordercolor=c["border"])
        style.configure("Status.TLabel", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"])
        style.configure("Header.TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10, "bold"))
        style.configure("TLabelframe", background=c["bg"], foreground=c["text"], bordercolor=c["border"])
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["text"])

        style.configure("TButton", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"], focusthickness=1, focuscolor=c["accent"])
        style.map("TButton", background=[("active", c["panel"]), ("pressed", c["panel"])], foreground=[("disabled", c["muted_text"]), ("!disabled", c["text"])])
        style.configure("TEntry", fieldbackground=c["input_bg"], foreground=c["text"], bordercolor=c["border"], insertcolor=c["text"])
        style.configure("TCombobox", fieldbackground=c["input_bg"], background=c["input_bg"], foreground=c["text"], arrowcolor=c["text"], bordercolor=c["border"])
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["input_bg"])],
            foreground=[("readonly", c["text"])],
            selectbackground=[("readonly", c["select_bg"])],
            selectforeground=[("readonly", c["select_fg"])],
        )

        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"])
        style.map("TCheckbutton", foreground=[("disabled", c["muted_text"]), ("!disabled", c["text"])])

        style.configure("TProgressbar", background=c["accent"], troughcolor=c["panel_alt"], bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])

        style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["text"], bordercolor=c["border"], rowheight=22)
        style.configure("Treeview.Heading", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"])
        style.map("Treeview", background=[("selected", c["select_bg"])], foreground=[("selected", c["select_fg"])])
        style.map("Treeview.Heading", background=[("active", c["panel"])], foreground=[("active", c["text"])])

        style.configure("Vertical.TScrollbar", background=c["panel_alt"], troughcolor=c["panel"], bordercolor=c["border"], arrowcolor=c["text"])
        style.configure("Horizontal.TScrollbar", background=c["panel_alt"], troughcolor=c["panel"], bordercolor=c["border"], arrowcolor=c["text"])

        if widgets:
            self._apply_widget_colors(c, widgets)
        return current

    def metric_palette(self, theme: str | None, value: float) -> tuple[str, str]:
        dark = self.normalize_theme(theme) == "dark"
        if value >= 80:
            return ("#7e3434", "#fff1f1") if dark else ("#ffd7d7", "#5f1212")
        if value >= 50:
            return ("#85722b", "#fff9e6") if dark else ("#fff5c6", "#5c4b00")
        if value >= 20:
            return ("#2f6245", "#f4fff8") if dark else ("#e9f7e9", "#114d2d")
        colors = self.colors(theme)
        return colors["panel_alt"], colors["text"]

    def apply_memory_tags(self, tree: ttk.Treeview, theme: str | None) -> None:
        c = self.colors(theme)
        if self.normalize_theme(theme) == "light":
            tree.tag_configure("mem_white", background=c["panel"], foreground=c["text"])
            tree.tag_configure("mem_green", background=c["ok"], foreground=c["text"])
            tree.tag_configure("mem_yellow", background=c["warn"], foreground=c["text"])
            tree.tag_configure("mem_red", background=c["danger"], foreground=c["text"])
            return
        tree.tag_configure("mem_white", background=c["panel"], foreground=c["text"])
        tree.tag_configure("mem_green", background="#2f6245", foreground="#f4fff8")
        tree.tag_configure("mem_yellow", background="#85722b", foreground="#fff9e6")
        tree.tag_configure("mem_red", background="#7e3434", foreground="#fff1f1")

    @staticmethod
    def _apply_widget_colors(c: dict[str, str], widgets: dict[str, object]) -> None:
        for text_widget_name in ["overview_text", "process_details", "report_details", "log_text", "diag_text"]:
            widget = widgets.get(text_widget_name)
            if widget is not None:
                try:
                    widget.configure(
                        background=c["panel"],
                        foreground=c["text"],
                        insertbackground=c["text"],
                        selectbackground=c["select_bg"],
                        selectforeground=c["select_fg"],
                        highlightbackground=c["border"],
                        highlightcolor=c["accent"],
                    )
                except tk.TclError:
                    pass

        module_list = widgets.get("module_list")
        if module_list is not None:
            try:
                module_list.configure(
                    background=c["panel"],
                    foreground=c["text"],
                    selectbackground=c["select_bg"],
                    selectforeground=c["select_fg"],
                    highlightbackground=c["border"],
                    highlightcolor=c["accent"],
                )
            except tk.TclError:
                pass

        status_label = widgets.get("status_label")
        if status_label is not None:
            try:
                status_label.configure(style="Status.TLabel")
            except tk.TclError:
                pass

        status_metrics = widgets.get("status_metrics")
        if status_metrics is not None:
            try:
                status_metrics.configure(style="Statusbar.TFrame")
            except tk.TclError:
                pass

        for lbl_name in ["metric_ram", "metric_cpu"]:
            lbl = widgets.get(lbl_name)
            if lbl is not None:
                try:
                    lbl.configure(bg=c["panel_alt"], highlightbackground=c["border"], highlightcolor=c["border"])
                except tk.TclError:
                    pass
