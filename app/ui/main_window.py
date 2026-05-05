from __future__ import annotations

import json
import os
import re
import threading
import ctypes
import time
import webbrowser
from ctypes import wintypes
import tkinter as tk
from urllib.parse import quote_plus
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import psutil

from app.application.module_registry import ModuleRegistry
from app.application.services.action_service import ActionService
from app.application.services.exe_build_service import ExeBuildService
from app.application.services.heuristics_service import HeuristicEngine
from app.application.services.log_analyzer_service import LogAnalyzerService
from app.application.services.report_insight_service import ReportInsightService
from app.application.services.scan_service import ScanService
from app.application.services.settings_service import AppSettings, SettingsService
from app.domain.entities import ProcessRecord, ServiceRecord, StartupEntry
from app.domain.ports import ReportRepositoryPort
from app.ui.event_store import UIEventStore
from app.ui.i18n import I18n
from app.ui.language import Language
from app.ui.notifications import EventLevel, UINotifier


MAX_LIVE_REPORT_LINES = 1200


class MainWindow:
    _THEME_DARK = {
        "bg": "#1b1f24",
        "panel": "#242a31",
        "panel_alt": "#2c3440",
        "text": "#e9edf2",
        "muted_text": "#b7c0cc",
        "accent": "#3f7fd6",
        "border": "#3a4452",
        "input_bg": "#1f252d",
        "select_bg": "#2d5f9e",
        "select_fg": "#ffffff",
        "danger": "#7e3434",
        "warn": "#85722b",
        "ok": "#2f6245",
    }

    _THEME_LIGHT = {
        "bg": "#f1f4f8",
        "panel": "#ffffff",
        "panel_alt": "#e9eef5",
        "text": "#1f2933",
        "muted_text": "#4b5a6a",
        "accent": "#2d6cca",
        "border": "#c9d3e0",
        "input_bg": "#ffffff",
        "select_bg": "#2d6cca",
        "select_fg": "#ffffff",
        "danger": "#ffd7d7",
        "warn": "#fff5c6",
        "ok": "#e9f7e9",
    }

    def __init__(
        self,
        scan_service: ScanService,
        settings_service: SettingsService,
        action_service: ActionService,
        reports_repo: ReportRepositoryPort,
        module_registry: ModuleRegistry,
        i18n: I18n,
        report_insight_service: ReportInsightService,
        log_analyzer_service: LogAnalyzerService,
        event_store: UIEventStore,
    ) -> None:
        self._scan_service = scan_service
        self._settings_service = settings_service
        self._action_service = action_service
        self._reports_repo = reports_repo
        self._module_registry = module_registry
        self._i18n = i18n
        self._insight = report_insight_service
        self._log_analyzer = log_analyzer_service
        self._event_store = event_store

        self._settings = self._settings_service.load()
        self._exe_builder = ExeBuildService()
        self._heuristics = HeuristicEngine()
        self._notifier = UINotifier()

        self._busy = False
        self._build_busy = False
        self._cancel_scan = False
        self._scan_pause_event = threading.Event()
        self._scan_pause_event.set()

        self._row_index: dict[str, ProcessRecord] = {}
        self._startup_index: dict[str, StartupEntry] = {}
        self._service_index: dict[str, ServiceRecord] = {}
        self._overview_index: dict[str, tuple[str, object]] = {}
        self._report_paths: dict[str, str] = {}
        self._all_processes: list[ProcessRecord] = []
        self._all_startup: list[StartupEntry] = []
        self._all_services: list[ServiceRecord] = []
        self._folder_index: dict[str, ProcessRecord] = {}
        self._folder_origin: dict[str, str] = {}
        self._live_report_lines: list[str] = []
        self._last_snapshot = None
        self._sort_reverse: dict[tuple[int, str], bool] = {}
        self._active_sort: dict[int, tuple[str, bool]] = {}
        self._status_anim_after_id: str | None = None
        self._status_anim_base: str = ""
        self._status_anim_step: int = 0
        self._metrics_after_id: str | None = None
        self._gpu_metric_cache: float | None = None
        self._gpu_metric_ts: float = 0.0
        self._vmem_metric_cache: float | None = None
        self._vmem_metric_ts: float = 0.0

        self.root = tk.Tk()
        self.root.title(self._i18n.t("app.title"))
        self.root.geometry("1620x980")
        self.root.minsize(1200, 760)
        self._is_maximized = False
        self._normal_geometry = self.root.geometry()
        self._drag_origin: tuple[int, int, int, int] | None = None
        self.root.overrideredirect(True)
        self.root.bind("<Map>", self._on_root_map, add="+")
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._build_style()
        self._init_variables()
        self._build()

    def _build_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 10, "bold"))
        self._apply_theme(self._settings.ui_theme)

    @staticmethod
    def _normalize_theme(theme: str | None) -> str:
        value = (theme or "dark").strip().lower()
        return "light" if value == "light" else "dark"

    def _theme_colors(self) -> dict[str, str]:
        return self._THEME_LIGHT if self._normalize_theme(getattr(self, "_current_theme", "dark")) == "light" else self._THEME_DARK

    def _apply_theme(self, theme: str) -> None:
        self._current_theme = self._normalize_theme(theme)
        c = self._theme_colors()
        style = ttk.Style()

        self.root.configure(background=c["bg"])

        style.configure(".",
                        background=c["bg"],
                        foreground=c["text"],
                        fieldbackground=c["input_bg"],
                        bordercolor=c["border"],
                        lightcolor=c["border"],
                        darkcolor=c["border"],
                        troughcolor=c["panel_alt"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Titlebar.TFrame", background=c["panel_alt"], bordercolor=c["border"])
        style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
        style.configure("TNotebook.Tab", background=c["panel_alt"], foreground=c["text"], padding=(10, 6))
        style.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["text"]), ("!selected", c["muted_text"])])

        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Titlebar.TLabel", background=c["panel_alt"], foreground=c["text"], font=("Segoe UI", 10, "bold"))
        style.configure("Statusbar.TFrame", background=c["panel_alt"], bordercolor=c["border"])
        style.configure("Status.TLabel", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"])
        style.configure("Header.TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10, "bold"))
        style.configure("TLabelframe", background=c["bg"], foreground=c["text"], bordercolor=c["border"])
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["text"])

        style.configure("TButton", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"], focusthickness=1, focuscolor=c["accent"])
        style.map("TButton",
                  background=[("active", c["panel"]), ("pressed", c["panel"])],
                  foreground=[("disabled", c["muted_text"]), ("!disabled", c["text"])])
        style.configure("Titlebar.TButton", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"], padding=(8, 4), focusthickness=0)
        style.map("Titlebar.TButton", background=[("active", c["panel"]), ("pressed", c["panel"]), ("!active", c["panel_alt"])], foreground=[("!disabled", c["text"])])
        style.configure("TitlebarClose.TButton", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"], padding=(8, 4), focusthickness=0)
        style.map("TitlebarClose.TButton", background=[("active", "#b63d4a"), ("pressed", "#9f2f3b"), ("!active", c["panel_alt"])], foreground=[("!disabled", "#ffffff")])

        style.configure("TEntry", fieldbackground=c["input_bg"], foreground=c["text"], bordercolor=c["border"], insertcolor=c["text"])
        style.configure("TCombobox", fieldbackground=c["input_bg"], background=c["input_bg"], foreground=c["text"], arrowcolor=c["text"], bordercolor=c["border"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["input_bg"])],
                  foreground=[("readonly", c["text"])],
                  selectbackground=[("readonly", c["select_bg"])],
                  selectforeground=[("readonly", c["select_fg"])])

        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"])
        style.map("TCheckbutton", foreground=[("disabled", c["muted_text"]), ("!disabled", c["text"])])

        style.configure("TProgressbar", background=c["accent"], troughcolor=c["panel_alt"], bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])

        style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["text"], bordercolor=c["border"], rowheight=22)
        style.configure("Treeview.Heading", background=c["panel_alt"], foreground=c["text"], bordercolor=c["border"])
        style.map("Treeview", background=[("selected", c["select_bg"])], foreground=[("selected", c["select_fg"])])
        style.map("Treeview.Heading", background=[("active", c["panel"])], foreground=[("active", c["text"])])

        style.configure("Vertical.TScrollbar", background=c["panel_alt"], troughcolor=c["panel"], bordercolor=c["border"], arrowcolor=c["text"])
        style.configure("Horizontal.TScrollbar", background=c["panel_alt"], troughcolor=c["panel"], bordercolor=c["border"], arrowcolor=c["text"])

        for text_widget_name in [
            "overview_text",
            "process_details",
            "report_details",
            "log_text",
            "diag_text",
        ]:
            widget = getattr(self, text_widget_name, None)
            if widget is not None:
                try:
                    widget.configure(background=c["panel"], foreground=c["text"], insertbackground=c["text"], selectbackground=c["select_bg"], selectforeground=c["select_fg"], highlightbackground=c["border"], highlightcolor=c["accent"])
                except tk.TclError:
                    pass

        if hasattr(self, "module_list"):
            try:
                self.module_list.configure(background=c["panel"], foreground=c["text"], selectbackground=c["select_bg"], selectforeground=c["select_fg"], highlightbackground=c["border"], highlightcolor=c["accent"])
            except tk.TclError:
                pass

        for tree_name in [
            "overview_tree",
            "process_tree",
            "startup_tree",
            "service_tree",
            "report_tree",
            "folder_process_tree",
            "events_tree",
            "sampling_tree",
        ]:
            tree = getattr(self, tree_name, None)
            if tree is not None:
                self._configure_memory_tags(tree)

        if hasattr(self, "status_label"):
            try:
                self.status_label.configure(style="Status.TLabel")
            except tk.TclError:
                pass

        if hasattr(self, "status_metrics"):
            try:
                self.status_metrics.configure(style="Statusbar.TFrame")
            except tk.TclError:
                pass
            for lbl_name in ["metric_ram", "metric_vmem", "metric_vram", "metric_cpu"]:
                lbl = getattr(self, lbl_name, None)
                if lbl is not None:
                    try:
                        lbl.configure(bg=c["panel_alt"], highlightbackground=c["border"], highlightcolor=c["border"])
                    except tk.TclError:
                        pass
            try:
                self._refresh_system_metrics()
            except Exception:
                pass

        if hasattr(self, "status_var"):
            self.root.update_idletasks()
        self._apply_rounded_corners()

    def _init_variables(self) -> None:
        self.var_high = tk.IntVar(value=self._settings.process_high_mb)
        self.var_medium = tk.IntVar(value=self._settings.process_medium_mb)
        self.var_vt_enabled = tk.BooleanVar(value=self._settings.enable_vt_lookup)
        self.var_vt_key = tk.StringVar(value=self._settings.vt_api_key)
        self.var_mod_process = tk.BooleanVar(value=self._settings.enable_process_module)
        self.var_mod_startup = tk.BooleanVar(value=self._settings.enable_startup_module)
        self.var_mod_services = tk.BooleanVar(value=self._settings.enable_services_module)
        self.var_sampling_enabled = tk.BooleanVar(value=self._settings.sampling_enabled)
        self.var_sampling_points = tk.IntVar(value=self._settings.sampling_points)
        self.var_sampling_interval = tk.IntVar(value=self._settings.sampling_interval_sec)
        self.var_dry_run = tk.BooleanVar(value=self._settings.dry_run_actions)
        self.var_locale = tk.StringVar(value=self._settings.locale_code)
        self.var_theme = tk.StringVar(value=self._normalize_theme(self._settings.ui_theme))
        self.var_cpu_limit = tk.IntVar(value=self._settings.cpu_limit_percent)
        self.var_heuristics_enabled = tk.BooleanVar(value=self._settings.heuristics_enabled)
        self.var_show_disabled_services = tk.BooleanVar(value=True)
        self.var_show_disabled_startup = tk.BooleanVar(value=True)

        self.var_search = tk.StringVar(value="")
        self.var_level = tk.StringVar(value="all")
        self.var_folder_path = tk.StringVar(value=r"C:\ProgramData")

        self.scan_progress = tk.IntVar(value=0)
        self.build_progress = tk.IntVar(value=0)
        self.scan_status = tk.StringVar(value="Ожидание сканирования")
        self.build_status = tk.StringVar(value="Ожидание сборки")

    def _build(self) -> None:
        self._build_custom_titlebar()
        self._build_header_controls()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.frame_dashboard = ttk.Frame(self.notebook)
        self.frame_processes = ttk.Frame(self.notebook)
        self.frame_startup = ttk.Frame(self.notebook)
        self.frame_services = ttk.Frame(self.notebook)
        self.frame_reports = ttk.Frame(self.notebook)
        self.frame_logs = ttk.Frame(self.notebook)
        self.frame_folder_processes = ttk.Frame(self.notebook)
        self.frame_settings = ttk.Frame(self.notebook)
        self.frame_events = ttk.Frame(self.notebook)
        self.frame_diagnostics = ttk.Frame(self.notebook)
        self.frame_sampling = ttk.Frame(self.notebook)

        self.notebook.add(self.frame_dashboard, text="Обзор")
        self.notebook.add(self.frame_processes, text="Процессы")
        self.notebook.add(self.frame_startup, text="Автозагрузка")
        self.notebook.add(self.frame_services, text="Службы")
        self.notebook.add(self.frame_reports, text="Отчеты")
        self.notebook.add(self.frame_logs, text="Анализ логов")
        self.notebook.add(self.frame_folder_processes, text="Процессы в папках")
        self.notebook.add(self.frame_settings, text="Настройки")
        self.notebook.add(self.frame_events, text="События")
        self.notebook.add(self.frame_diagnostics, text="Диагностика")
        self.notebook.add(self.frame_sampling, text="Сэмплинг")

        self._build_dashboard_tab()
        self._build_processes_tab()
        self._build_startup_tab()
        self._build_services_tab()
        self._build_reports_tab()
        self._build_logs_tab()
        self._build_folder_processes_tab()
        self._build_settings_tab()
        self._build_events_tab()
        self._build_diagnostics_tab()
        self._build_sampling_tab()

        self.status_var = tk.StringVar(value=self._i18n.t("status.ready"))
        self.status_bar = ttk.Frame(self.root, style="Statusbar.TFrame")
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_label = ttk.Label(self.status_bar, textvariable=self.status_var, style="Status.TLabel", anchor=tk.W, padding=(8, 4))
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_metrics = ttk.Frame(self.status_bar, style="Statusbar.TFrame")
        self.status_metrics.place(relx=0.5, rely=0.5, anchor="center")

        self.metric_ram = tk.Label(self.status_metrics, text="RAM: -", font=("Segoe UI", 9, "bold"), padx=8, pady=1, bd=1, relief=tk.SOLID)
        self.metric_vmem = tk.Label(self.status_metrics, text="Вирт: -", font=("Segoe UI", 9, "bold"), padx=8, pady=1, bd=1, relief=tk.SOLID)
        self.metric_vram = tk.Label(self.status_metrics, text="VRAM: -", font=("Segoe UI", 9, "bold"), padx=8, pady=1, bd=1, relief=tk.SOLID)
        self.metric_cpu = tk.Label(self.status_metrics, text="CPU: -", font=("Segoe UI", 9, "bold"), padx=8, pady=1, bd=1, relief=tk.SOLID)
        self.metric_ram.pack(side=tk.LEFT)
        self.metric_vmem.pack(side=tk.LEFT)
        self.metric_vram.pack(side=tk.LEFT)
        self.metric_cpu.pack(side=tk.LEFT)

        self._apply_theme(self._current_theme)
        self._start_metrics_updater()

        self.load_reports()
        self._sync_module_info()
        self._restore_events()
        self.root.after(150, self._warmup_on_start)

    def _start_metrics_updater(self) -> None:
        psutil.cpu_percent(interval=None)

        def _tick() -> None:
            self._refresh_system_metrics()
            self._metrics_after_id = self.root.after(1500, _tick)

        _tick()

    def _refresh_system_metrics(self) -> None:
        try:
            ram = psutil.virtual_memory().percent
        except Exception:
            ram = None
        vmem = self._virtual_memory_percent_cached()
        try:
            cpu = psutil.cpu_percent(interval=None)
        except Exception:
            cpu = None
        vram = self._gpu_vram_percent_cached()

        self._set_metric(self.metric_ram, "RAM", ram)
        self._set_metric(self.metric_vmem, "Вирт", vmem)
        self._set_metric(self.metric_vram, "VRAM", vram)
        self._set_metric(self.metric_cpu, "CPU", cpu)

    def _set_metric(self, label: tk.Label, title: str, value: float | None) -> None:
        c = self._theme_colors()
        if value is None:
            label.configure(text=f"{title}: н/д", fg=c["muted_text"], bg=c["panel_alt"], highlightbackground=c["border"], highlightcolor=c["border"])
            return
        pct = max(0.0, min(100.0, float(value)))
        bg, fg = self._metric_palette(pct)
        label.configure(text=f"{title}: {pct:.0f}%", fg=fg, bg=bg, highlightbackground=c["border"], highlightcolor=c["border"])

    def _metric_color(self, value: float) -> str:
        dark = self._normalize_theme(self._current_theme) == "dark"
        if value >= 80:
            return "#ff8a8a" if dark else "#c62b2b"
        if value >= 50:
            return "#ffd86b" if dark else "#9a7a00"
        if value >= 20:
            return "#8ee6b0" if dark else "#1e7d47"
        return self._theme_colors()["text"]

    def _metric_palette(self, value: float) -> tuple[str, str]:
        dark = self._normalize_theme(self._current_theme) == "dark"
        if value >= 80:
            return ("#7e3434", "#fff1f1") if dark else ("#ffd7d7", "#5f1212")
        if value >= 50:
            return ("#85722b", "#fff9e6") if dark else ("#fff5c6", "#5c4b00")
        if value >= 20:
            return ("#2f6245", "#f4fff8") if dark else ("#e9f7e9", "#114d2d")
        return (self._theme_colors()["panel_alt"], self._theme_colors()["text"])

    def _virtual_memory_percent_cached(self) -> float | None:
        now = time.monotonic()
        if self._vmem_metric_cache is not None and (now - self._vmem_metric_ts) < 8.0:
            return self._vmem_metric_cache
        self._vmem_metric_ts = now
        self._vmem_metric_cache = self._query_virtual_memory_percent()
        return self._vmem_metric_cache

    @staticmethod
    def _query_virtual_memory_percent() -> float | None:
        script = "(Get-Counter '\\Memory\\% Committed Bytes In Use' -ErrorAction SilentlyContinue).CounterSamples | Select-Object -ExpandProperty CookedValue"
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                shell=False,
                timeout=1800,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        out = (proc.stdout or "").strip().replace(",", ".")
        match = re.search(r"-?\d+(?:\.\d+)?", out)
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    def _gpu_vram_percent_cached(self) -> float | None:
        now = time.monotonic()
        if self._gpu_metric_cache is not None and (now - self._gpu_metric_ts) < 8.0:
            return self._gpu_metric_cache
        self._gpu_metric_ts = now
        self._gpu_metric_cache = self._query_gpu_vram_percent()
        return self._gpu_metric_cache

    @staticmethod
    def _query_gpu_vram_percent() -> float | None:
        script = (
            "$used = 0;"
            "$ctr = Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue;"
            "if (-not $ctr) { $ctr = Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue };"
            "if ($ctr) { $used = ($ctr.CounterSamples | Measure-Object -Property CookedValue -Sum).Sum };"
            "$total = (Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Measure-Object -Property AdapterRAM -Sum).Sum;"
            "if ($total -gt 0) { [math]::Round(($used / $total) * 100, 1) }"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                shell=False,
                timeout=2500,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        out = (proc.stdout or "").strip().replace(",", ".")
        if not out:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", out)
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    def _start_status_animation(self, base_text: str) -> None:
        self._stop_status_animation()
        self._status_anim_base = base_text
        self._status_anim_step = 0

        def _tick() -> None:
            dots = "." * ((self._status_anim_step % 4) + 1)
            self.status_var.set(f"{self._status_anim_base}{dots}")
            self._status_anim_step += 1
            self._status_anim_after_id = self.root.after(350, _tick)

        _tick()

    def _stop_status_animation(self, final_text: str | None = None) -> None:
        if self._status_anim_after_id is not None:
            try:
                self.root.after_cancel(self._status_anim_after_id)
            except tk.TclError:
                pass
            self._status_anim_after_id = None
        if final_text is not None:
            self.status_var.set(final_text)

    def _build_custom_titlebar(self) -> None:
        self.titlebar = ttk.Frame(self.root, style="Titlebar.TFrame", padding=(10, 6))
        self.titlebar.pack(fill=tk.X, side=tk.TOP)

        self.title_label = ttk.Label(self.titlebar, text=self._i18n.t("app.title"), style="Titlebar.TLabel")
        self.title_label.pack(side=tk.LEFT)

        btns = ttk.Frame(self.titlebar, style="Titlebar.TFrame")
        btns.pack(side=tk.RIGHT)

        self.btn_min = ttk.Button(btns, text="_", width=3, style="Titlebar.TButton", command=self._minimize_window)
        self.btn_max = ttk.Button(btns, text="[]", width=3, style="Titlebar.TButton", command=self._toggle_maximize)
        self.btn_close = ttk.Button(btns, text="X", width=3, style="TitlebarClose.TButton", command=self._close_window)
        self.btn_min.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_max.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_close.pack(side=tk.LEFT)

        for widget in (self.titlebar, self.title_label):
            widget.bind("<ButtonPress-1>", self._start_window_drag)
            widget.bind("<B1-Motion>", self._do_window_drag)
            widget.bind("<Double-Button-1>", lambda _e: self._toggle_maximize())

    def _start_window_drag(self, event) -> None:
        if self._is_maximized:
            return
        self._drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _do_window_drag(self, event) -> None:
        if self._drag_origin is None or self._is_maximized:
            return
        start_x, start_y, win_x, win_y = self._drag_origin
        dx = event.x_root - start_x
        dy = event.y_root - start_y
        self.root.geometry(f"+{win_x + dx}+{win_y + dy}")

    def _toggle_maximize(self) -> None:
        if self._is_maximized:
            self._restore_window()
        else:
            self._maximize_window()

    def _maximize_window(self) -> None:
        self._normal_geometry = self.root.geometry()
        left, top, right, bottom = self._work_area()
        width = max(800, right - left)
        height = max(600, bottom - top)
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self._is_maximized = True
        self._update_maximize_button()
        self._apply_rounded_corners()

    def _restore_window(self) -> None:
        self.root.geometry(self._normal_geometry)
        self._is_maximized = False
        self._update_maximize_button()
        self._apply_rounded_corners()

    def _update_maximize_button(self) -> None:
        if hasattr(self, "btn_max"):
            self.btn_max.configure(text="<>" if self._is_maximized else "[]")

    def _minimize_window(self) -> None:
        self.root.overrideredirect(False)
        self.root.iconify()

    def _close_window(self) -> None:
        if self._metrics_after_id is not None:
            try:
                self.root.after_cancel(self._metrics_after_id)
            except tk.TclError:
                pass
            self._metrics_after_id = None
        self._stop_status_animation()
        self.root.destroy()

    def _on_root_map(self, _event) -> None:
        if self.root.state() != "iconic":
            self.root.overrideredirect(True)
            self._apply_rounded_corners()

    def _on_root_configure(self, _event) -> None:
        if not self._is_maximized:
            self._normal_geometry = self.root.geometry()
        self._apply_rounded_corners()

    @staticmethod
    def _work_area() -> tuple[int, int, int, int]:
        if os.name != "nt":
            return 0, 0, 1600, 900
        try:
            rect = wintypes.RECT()
            SPI_GETWORKAREA = 48
            ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
            return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            return 0, 0, 1600, 900

    def _apply_rounded_corners(self) -> None:
        if os.name != "nt":
            return
        try:
            self.root.update_idletasks()
            width = max(1, self.root.winfo_width())
            height = max(1, self.root.winfo_height())
            radius = 0 if self._is_maximized else 16
            hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
            ctypes.windll.user32.SetWindowRgn(self.root.winfo_id(), hrgn, True)
        except Exception:
            return

    def _warmup_on_start(self) -> None:
        settings = self._collect_settings()
        self._start_status_animation("Фоновое обновление данных")
        threading.Thread(target=self._warmup_worker, args=(settings.process_high_mb, settings.process_medium_mb), daemon=True).start()

    def _warmup_worker(self, high_mb: int, medium_mb: int) -> None:
        try:
            processes = self._scan_service.scan_processes_only(high_mb, medium_mb)
            services = self._scan_service.scan_services_only()
            startup = self._scan_service.scan_startup_only()

            def _apply() -> None:
                self._all_processes = list(processes)
                self._all_services = list(services)
                self._all_startup = list(startup)

                proc_snapshot = type("Snapshot", (), {"process_records": self._all_processes, "service_records": self._all_services, "startup_entries": self._all_startup})
                self._apply_process_filters()
                self._fill_services(proc_snapshot)
                self._fill_startup(proc_snapshot)

            self.root.after(0, _apply)
        except Exception as exc:
            self.root.after(0, lambda: self._handle_error("Ошибка фонового обновления", str(exc)))
        finally:
            self.root.after(0, lambda: self._stop_status_animation("Готово"))

    def _attach_scrollbars(self, widget) -> None:
        parent = widget.master
        if parent is None:
            return

        ysb = ttk.Scrollbar(parent, orient=tk.VERTICAL)
        xsb = ttk.Scrollbar(parent, orient=tk.HORIZONTAL)

        y_visible = {"shown": False}
        x_visible = {"shown": False}

        def _toggle_y(first: str, last: str) -> None:
            ysb.set(first, last)
            try:
                f = float(first)
                l = float(last)
            except (TypeError, ValueError):
                f, l = 0.0, 1.0
            need = not (f <= 0.0 and l >= 1.0)
            if need and not y_visible["shown"]:
                ysb.place(in_=widget, relx=1.0, rely=0.0, relheight=1.0, anchor="ne")
                y_visible["shown"] = True
            elif not need and y_visible["shown"]:
                ysb.place_forget()
                y_visible["shown"] = False

        def _toggle_x(first: str, last: str) -> None:
            xsb.set(first, last)
            try:
                f = float(first)
                l = float(last)
            except (TypeError, ValueError):
                f, l = 0.0, 1.0
            need = not (f <= 0.0 and l >= 1.0)
            if need and not x_visible["shown"]:
                xsb.place(in_=widget, relx=0.0, rely=1.0, relwidth=1.0, anchor="sw")
                x_visible["shown"] = True
            elif not need and x_visible["shown"]:
                xsb.place_forget()
                x_visible["shown"] = False

        try:
            widget.configure(yscrollcommand=_toggle_y, xscrollcommand=_toggle_x)
            ysb.configure(command=widget.yview)
            xsb.configure(command=widget.xview)
            widget.update_idletasks()
            first_y, last_y = widget.yview()
            first_x, last_x = widget.xview()
            _toggle_y(str(first_y), str(last_y))
            _toggle_x(str(first_x), str(last_x))
        except tk.TclError:
            return

    def _enable_tree_sorting(self, tree: ttk.Treeview, columns: list[str]) -> None:
        for col in columns:
            tree.heading(col, command=lambda c=col, t=tree: self._sort_tree_by_column(t, c))

    def _sort_tree_by_column(self, tree: ttk.Treeview, column: str) -> None:
        key = (id(tree), column)
        reverse = self._sort_reverse.get(key, False)
        self._apply_tree_sort(tree, column, reverse=reverse, remember=True)
        self._sort_reverse[key] = not reverse

    def _apply_tree_sort(self, tree: ttk.Treeview, column: str, reverse: bool, remember: bool = False) -> None:
        items = [(self._sort_key(tree.set(iid, column)), iid) for iid in tree.get_children("")]
        items.sort(key=lambda x: x[0], reverse=reverse)
        for idx, (_, iid) in enumerate(items):
            tree.move(iid, "", idx)
        if remember:
            self._active_sort[id(tree)] = (column, reverse)

    def _restore_tree_sort(self, tree: ttk.Treeview) -> None:
        state = self._active_sort.get(id(tree))
        if state is None:
            return
        column, reverse = state
        self._apply_tree_sort(tree, column, reverse=reverse)

    @staticmethod
    def _sort_key(value: str):
        text = (value or "").strip().lower()
        if text in {"-", "", "н/д", "none", "unknown", "нет"}:
            return (2, "")

        if text in {"да", "yes", "true", "включено", "включена"}:
            return (1, 1)
        if text in {"нет", "no", "false", "отключено", "отключена"}:
            return (1, 0)

        num_match = re.search(r"-?\d+(?:[\.,]\d+)?", text)
        if num_match:
            raw = num_match.group(0).replace(",", ".")
            try:
                return (0, float(raw))
            except ValueError:
                pass

        return (3, text)

    def _build_header_controls(self) -> None:
        head = ttk.Frame(self.root)
        head.pack(fill=tk.X, padx=10, pady=(10, 4))

        scan_box = ttk.LabelFrame(head, text="Сканирование")
        scan_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(scan_box, text="Запустить сканирование", command=self.scan).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(scan_box, text="Пауза/Продолжить", command=self.toggle_scan_pause).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(scan_box, text="Стоп", command=self.stop_scan).grid(row=0, column=2, padx=4, pady=4)

        self.scan_progressbar = ttk.Progressbar(scan_box, maximum=100, variable=self.scan_progress)
        self.scan_progressbar.grid(row=1, column=0, columnspan=4, sticky="ew", padx=4)
        ttk.Label(scan_box, textvariable=self.scan_status).grid(row=2, column=0, columnspan=4, sticky=tk.W, padx=4, pady=(2, 4))

        build_box = ttk.LabelFrame(head, text="Сборка EXE")
        build_box.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(build_box, text="Собрать EXE", command=self.build_exe).grid(row=0, column=0, padx=4, pady=4)
        ttk.Progressbar(build_box, maximum=100, variable=self.build_progress).grid(row=1, column=0, sticky="ew", padx=4)
        ttk.Label(build_box, textvariable=self.build_status).grid(row=2, column=0, sticky=tk.W, padx=4, pady=(2, 4))

        scan_box.columnconfigure(3, weight=1)
        build_box.columnconfigure(0, weight=1)

    def _build_dashboard_tab(self) -> None:
        top = ttk.Frame(self.frame_dashboard)
        top.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.overview_tree = ttk.Treeview(
            top,
            columns=("file", "kind", "instances", "memory", "autorun", "risk", "vt", "details"),
            show="headings",
        )
        headers = {
            "file": "Исполняемый файл",
            "kind": "Источники",
            "instances": "Экземпляры",
            "memory": "Память",
            "autorun": "Автозагрузка",
            "risk": "Риск",
            "vt": "VirusTotal",
            "details": "Детали",
        }
        widths = {
            "file": 470,
            "kind": 140,
            "instances": 100,
            "memory": 130,
            "autorun": 120,
            "risk": 170,
            "vt": 170,
            "details": 420,
        }
        for c in headers:
            self.overview_tree.heading(c, text=headers[c])
            self.overview_tree.column(c, width=widths[c], anchor=tk.W)
        self._enable_tree_sorting(self.overview_tree, list(headers.keys()))
        self.overview_tree.pack(fill=tk.BOTH, expand=True)
        self._attach_scrollbars(self.overview_tree)
        self._configure_memory_tags(self.overview_tree)
        self.overview_tree.bind("<Button-3>", self._open_overview_menu)
        self.overview_tree.bind("<Double-1>", self._open_overview_details)

        actions = ttk.Frame(self.frame_dashboard)
        actions.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Button(actions, text="Открыть в Проводнике", command=self.action_open_overview_path).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Сохранить отчет (.txt)", command=self.save_overview_report_txt).pack(side=tk.RIGHT, padx=2)
        ttk.Button(actions, text="Выгрузить отчет (.json)", command=self.export_overview_report_json).pack(side=tk.RIGHT, padx=2)

        low = ttk.LabelFrame(self.frame_dashboard, text="Итоговая информация")
        low.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.overview_text = tk.Text(low, height=14)
        self.overview_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._attach_scrollbars(self.overview_text)

    def _build_processes_tab(self) -> None:
        filter_row = ttk.Frame(self.frame_processes)
        filter_row.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(filter_row, text="Поиск:").pack(side=tk.LEFT)
        ttk.Entry(filter_row, textvariable=self.var_search, width=36).pack(side=tk.LEFT, padx=4)
        ttk.Label(filter_row, text="Уровень:").pack(side=tk.LEFT)
        ttk.Combobox(
            filter_row,
            textvariable=self.var_level,
            values=["all", "high", "medium", "low"],
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(filter_row, text="Применить", command=self._apply_process_filters).pack(side=tk.LEFT, padx=4)
        ttk.Button(filter_row, text="Обновить", command=self.action_refresh_processes).pack(side=tk.LEFT, padx=4)

        self.process_tree = ttk.Treeview(
            self.frame_processes,
            columns=("pid", "name", "memory", "level", "path", "autorun", "heur", "vt"),
            show="headings",
        )
        headers = {
            "pid": "PID",
            "name": "Имя процесса",
            "memory": "Память",
            "level": "Уровень",
            "path": "Путь",
            "autorun": "В автозагрузке",
            "heur": "Эвристика",
            "vt": "VirusTotal",
        }
        widths = {
            "pid": 90,
            "name": 240,
            "memory": 120,
            "level": 120,
            "path": 620,
            "autorun": 140,
            "heur": 170,
            "vt": 170,
        }
        for c in headers:
            self.process_tree.heading(c, text=headers[c])
            self.process_tree.column(c, width=widths[c], anchor=tk.W)
        self._enable_tree_sorting(self.process_tree, list(headers.keys()))
        self.process_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self._attach_scrollbars(self.process_tree)
        self._configure_memory_tags(self.process_tree)
        self.process_tree.bind("<<TreeviewSelect>>", self._on_process_select)
        self.process_tree.bind("<Button-3>", self._open_process_menu)

        b = ttk.Frame(self.frame_processes)
        b.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(b, text="Обновить", command=self.action_refresh_processes).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Открыть в Проводнике", command=self.action_open_selected_path).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Завершить", command=self.action_terminate_process).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Проверить VirusTotal", command=self.action_vt_lookup).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Карантин", command=self.action_quarantine_exe).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Удалить файл", command=self.action_delete_exe).pack(side=tk.LEFT, padx=4)

        self.process_details = tk.Text(self.frame_processes, height=9)
        self.process_details.pack(fill=tk.X, padx=10, pady=6)
        self._attach_scrollbars(self.process_details)

    def _build_startup_tab(self) -> None:
        startup_grid = ttk.Frame(self.frame_startup)
        startup_grid.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.startup_tree = ttk.Treeview(
            startup_grid,
            columns=("category", "name", "command", "enabled", "location"),
            show="headings",
        )
        headers = {
            "category": "Категория",
            "name": "Имя",
            "command": "Команда",
            "enabled": "Включено",
            "location": "Расположение",
        }
        widths = {
            "category": 130,
            "name": 250,
            "command": 720,
            "enabled": 100,
            "location": 260,
        }
        for c in headers:
            self.startup_tree.heading(c, text=headers[c])
            self.startup_tree.column(c, width=widths[c], anchor=tk.W)
        self._enable_tree_sorting(self.startup_tree, list(headers.keys()))
        self.startup_tree.pack(fill=tk.BOTH, expand=True)
        self._attach_scrollbars(self.startup_tree)
        self.startup_tree.bind("<Button-3>", self._open_startup_menu)

        b = ttk.Frame(self.frame_startup)
        b.pack(fill=tk.X, padx=10, pady=(0, 8))
        ttk.Checkbutton(
            b,
            text="Показывать отключенные",
            variable=self.var_show_disabled_startup,
            command=self._refresh_startup_view,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Обновить", command=self.action_refresh_startup).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Отключить", command=self.action_disable_startup).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Удалить", command=self.action_remove_startup).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Открыть в Проводнике", command=self.action_open_startup_path).pack(side=tk.LEFT, padx=4)

    def _build_services_tab(self) -> None:
        self.service_tree = ttk.Treeview(
            self.frame_services,
            columns=("name", "status", "start", "pid", "memory", "risk", "reason", "path"),
            show="headings",
        )
        headers = {
            "name": "Имя",
            "status": "Статус",
            "start": "Запуск",
            "pid": "PID",
            "memory": "Память",
            "risk": "Риск",
            "reason": "Причина",
            "path": "Путь",
        }
        widths = {
            "name": 190,
            "status": 100,
            "start": 100,
            "pid": 80,
            "memory": 110,
            "risk": 70,
            "reason": 280,
            "path": 600,
        }
        for c in headers:
            self.service_tree.heading(c, text=headers[c])
            self.service_tree.column(c, width=widths[c], anchor=tk.W)
        self._enable_tree_sorting(self.service_tree, list(headers.keys()))
        self.service_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._attach_scrollbars(self.service_tree)
        self.service_tree.bind("<Button-3>", self._open_service_menu)

        b = ttk.Frame(self.frame_services)
        b.pack(fill=tk.X, padx=10, pady=(0, 8))
        ttk.Checkbutton(
            b,
            text="Показывать отключенные",
            variable=self.var_show_disabled_services,
            command=self._refresh_services_view,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Обновить", command=self.action_refresh_services).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Остановить службу", command=self.action_stop_service).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Отключить службу", command=self.action_disable_service).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Открыть в Проводнике", command=self.action_open_service_path).pack(side=tk.LEFT, padx=4)

    def _build_reports_tab(self) -> None:
        self.report_tree = ttk.Treeview(self.frame_reports, columns=("created", "path"), show="headings")
        self.report_tree.heading("created", text="Создан")
        self.report_tree.heading("path", text="Путь")
        self.report_tree.column("created", width=220)
        self.report_tree.column("path", width=1120)
        self._enable_tree_sorting(self.report_tree, ["created", "path"])
        self.report_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self._attach_scrollbars(self.report_tree)

        btn = ttk.Frame(self.frame_reports)
        btn.pack(fill=tk.X, padx=10)
        ttk.Button(btn, text="Открыть отчет", command=self.open_report).pack(side=tk.LEFT, pady=4)

        self.report_details = tk.Text(self.frame_reports, height=14)
        self.report_details.pack(fill=tk.BOTH, expand=False, padx=10, pady=6)
        self._attach_scrollbars(self.report_details)

    def _build_logs_tab(self) -> None:
        ttk.Button(self.frame_logs, text="Анализировать лог", command=self.analyze_logs).pack(anchor=tk.W, padx=10, pady=6)
        self.log_text = tk.Text(self.frame_logs)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._attach_scrollbars(self.log_text)

    def _build_folder_processes_tab(self) -> None:
        top = ttk.Frame(self.frame_folder_processes)
        top.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(top, text="Путь:").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.var_folder_path, width=70).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Обзор", command=self.pick_folder_path).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Сканировать", command=self.scan_processes_in_folder).pack(side=tk.LEFT, padx=4)

        self.folder_process_tree = ttk.Treeview(
            self.frame_folder_processes,
            columns=("pid", "name", "path", "origin", "memory", "cpu", "started"),
            show="headings",
        )
        headers = {
            "pid": "PID",
            "name": "Имя",
            "path": "Полный путь",
            "origin": "Источник",
            "memory": "Память",
            "cpu": "CPU",
            "started": "Время старта",
        }
        widths = {
            "pid": 90,
            "name": 200,
            "path": 650,
            "origin": 180,
            "memory": 120,
            "cpu": 80,
            "started": 200,
        }
        for c in headers:
            self.folder_process_tree.heading(c, text=headers[c])
            self.folder_process_tree.column(c, width=widths[c], anchor=tk.W)
        self._enable_tree_sorting(self.folder_process_tree, list(headers.keys()))
        self.folder_process_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self._attach_scrollbars(self.folder_process_tree)
        self._configure_memory_tags(self.folder_process_tree)
        self.folder_process_tree.bind("<Button-3>", self._open_folder_process_menu)

        b = ttk.Frame(self.frame_folder_processes)
        b.pack(fill=tk.X, padx=10, pady=(0, 8))
        ttk.Button(b, text="Открыть в Проводнике", command=self.action_open_folder_process_path).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Завершить процесс", command=self.action_terminate_folder_process).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Проверить в VirusTotal", command=self.action_vt_folder_process).pack(side=tk.LEFT, padx=4)

    def _build_settings_tab(self) -> None:
        pane = ttk.Notebook(self.frame_settings)
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        tab_general = ttk.Frame(pane)
        tab_modules = ttk.Frame(pane)
        tab_heur = ttk.Frame(pane)
        pane.add(tab_general, text="Общее")
        pane.add(tab_modules, text="Модули")
        pane.add(tab_heur, text="Эвристика")

        row = 0
        ttk.Label(tab_general, text="Язык интерфейса").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Combobox(tab_general, textvariable=self.var_locale, values=[Language.RU.value, Language.EN.value], state="readonly", width=10).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        ttk.Label(tab_general, text="Тема интерфейса").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        theme_combo = ttk.Combobox(tab_general, textvariable=self.var_theme, values=["dark", "light"], state="readonly", width=12)
        theme_combo.grid(row=row, column=1, sticky=tk.W, padx=6)
        theme_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_theme(self.var_theme.get()))
        row += 1

        ttk.Checkbutton(tab_general, text="Включить проверку VirusTotal", variable=self.var_vt_enabled).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=6, pady=4)
        row += 1
        ttk.Label(tab_general, text="Ключ VirusTotal API").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_vt_key, width=64).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        ttk.Label(tab_general, text="Порог среднего потребления памяти, МБ").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_medium, width=12).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1
        ttk.Label(tab_general, text="Порог высокого потребления памяти, МБ").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_high, width=12).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        ttk.Label(tab_general, text="Ограничение использования CPU, %").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_cpu_limit, width=12).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        ttk.Checkbutton(tab_general, text="Тестовый режим действий (dry-run)", variable=self.var_dry_run).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=6, pady=4)
        row += 1
        ttk.Checkbutton(tab_general, text="Включить сэмплинг", variable=self.var_sampling_enabled).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=6, pady=4)
        row += 1

        ttk.Label(tab_general, text="Количество точек сэмплинга").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_sampling_points, width=12).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        ttk.Label(tab_general, text="Интервал сэмплинга, сек").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_sampling_interval, width=12).grid(row=row, column=1, sticky=tk.W, padx=6)

        ttk.Checkbutton(tab_modules, text="Сканировать процессы", variable=self.var_mod_process).pack(anchor=tk.W, padx=8, pady=6)
        ttk.Checkbutton(tab_modules, text="Сканировать автозагрузку", variable=self.var_mod_startup).pack(anchor=tk.W, padx=8, pady=6)
        ttk.Checkbutton(tab_modules, text="Сканировать службы", variable=self.var_mod_services).pack(anchor=tk.W, padx=8, pady=6)

        self.heuristic_vars: dict[str, tk.BooleanVar] = {}
        ttk.Checkbutton(tab_heur, text="Включить эвристику", variable=self.var_heuristics_enabled).pack(anchor=tk.W, padx=8, pady=6)
        defaults = self._settings.heuristic_rules or self._heuristics.schema()
        for rule in self._heuristics.rules:
            val = bool(defaults.get(rule.rule_id, True))
            var = tk.BooleanVar(value=val)
            self.heuristic_vars[rule.rule_id] = var
            ttk.Checkbutton(tab_heur, text=f"{rule.title} ({rule.rule_id})", variable=var).pack(anchor=tk.W, padx=8, pady=2)

        bottom = ttk.Frame(self.frame_settings)
        bottom.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(bottom, text="Сохранить настройки", command=self.save_settings).pack(side=tk.LEFT)

        self.module_list = tk.Listbox(self.frame_settings, height=8)
        self.module_list.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self._attach_scrollbars(self.module_list)

    def _build_events_tab(self) -> None:
        top = ttk.Frame(self.frame_events)
        top.pack(fill=tk.X, padx=10, pady=(10, 0))
        ttk.Button(top, text="Очистить события", command=self.clear_events).pack(side=tk.LEFT)

        self.events_tree = ttk.Treeview(self.frame_events, columns=("time", "level", "title", "message"), show="headings")
        for c in ("time", "level", "title", "message"):
            self.events_tree.heading(c, text=c)
            self.events_tree.column(c, width=180 if c != "message" else 980, anchor=tk.W)
        self._enable_tree_sorting(self.events_tree, ["time", "level", "title", "message"])
        self.events_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._attach_scrollbars(self.events_tree)

    def _build_diagnostics_tab(self) -> None:
        self.diag_text = tk.Text(self.frame_diagnostics)
        self.diag_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._attach_scrollbars(self.diag_text)

    def _build_sampling_tab(self) -> None:
        self.sampling_tree = ttk.Treeview(self.frame_sampling, columns=("time", "used", "top"), show="headings")
        self.sampling_tree.heading("time", text="Время")
        self.sampling_tree.heading("used", text="RAM, ГБ")
        self.sampling_tree.heading("top", text="Топ процесс")
        self.sampling_tree.column("time", width=280, anchor=tk.W)
        self.sampling_tree.column("used", width=140, anchor=tk.W)
        self.sampling_tree.column("top", width=980, anchor=tk.W)
        self._enable_tree_sorting(self.sampling_tree, ["time", "used", "top"])
        self.sampling_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._attach_scrollbars(self.sampling_tree)

    def _collect_settings(self) -> AppSettings:
        cpu_limit = max(10, min(100, int(self.var_cpu_limit.get() or 60)))
        return AppSettings(
            vt_api_key=self.var_vt_key.get().strip(),
            process_high_mb=int(self.var_high.get() or 900),
            process_medium_mb=int(self.var_medium.get() or 300),
            enable_vt_lookup=bool(self.var_vt_enabled.get()),
            enable_process_module=bool(self.var_mod_process.get()),
            enable_startup_module=bool(self.var_mod_startup.get()),
            enable_services_module=bool(self.var_mod_services.get()),
            sampling_enabled=bool(self.var_sampling_enabled.get()),
            sampling_points=int(self.var_sampling_points.get() or 5),
            sampling_interval_sec=int(self.var_sampling_interval.get() or 3),
            dry_run_actions=bool(self.var_dry_run.get()),
            locale_code=self.var_locale.get() or Language.RU.value,
            ui_theme=self._normalize_theme(self.var_theme.get()),
            cpu_limit_percent=cpu_limit,
            heuristics_enabled=bool(self.var_heuristics_enabled.get()),
            heuristic_rules={k: bool(v.get()) for k, v in getattr(self, "heuristic_vars", {}).items()},
        )

    def save_settings(self) -> None:
        settings = self._collect_settings()
        self._settings_service.save(settings)
        self._settings = settings
        self._apply_theme(settings.ui_theme)
        self.status_var.set("Настройки сохранены")
        self._emit_event(EventLevel.INFO, "Информация", "Настройки сохранены")

    def scan(self) -> None:
        if self._busy:
            self._stop_status_animation(self._i18n.t("status.busy"))
            return

        self.scan_progress.set(0)
        self.scan_status.set("Запуск сканирования")
        self.scan_progressbar.configure(maximum=100)
        self._live_report_lines = ["Запуск сканирования..."]
        self.overview_text.delete("1.0", tk.END)
        self.overview_text.insert(tk.END, "\n".join(self._live_report_lines))
        self._cancel_scan = False
        self._scan_pause_event.set()

        def _run() -> None:
            self._busy = True
            settings = self._collect_settings()
            self._action_service.set_dry_run(settings.dry_run_actions)
            cpu_count = os.cpu_count() or 4
            workers = max(1, int(cpu_count * (settings.cpu_limit_percent / 100.0)))

            try:
                snapshot, report = self._scan_service.full_scan(
                    high_mb=settings.process_high_mb,
                    medium_mb=settings.process_medium_mb,
                    with_vt=settings.enable_vt_lookup,
                    enable_process_module=settings.enable_process_module,
                    enable_startup_module=settings.enable_startup_module,
                    enable_services_module=settings.enable_services_module,
                    sampling_points=settings.sampling_points if settings.sampling_enabled else 0,
                    sampling_interval_sec=settings.sampling_interval_sec,
                    max_workers=workers,
                    cancel_check=lambda: self._cancel_scan,
                    pause_check=lambda: self._scan_pause_event.wait(),
                    progress_callback=self._scan_progress_callback,
                    report_callback=self._scan_report_callback,
                    process_record_callback=self._stream_process_row,
                    heuristics_enabled=settings.heuristics_enabled,
                    heuristic_rules=settings.heuristic_rules or {},
                )
                self.root.after(0, lambda: self._after_scan(snapshot, report))
            except Exception as exc:
                self.root.after(0, lambda: self._handle_error("Ошибка сканирования", str(exc)))
            finally:
                self.root.after(0, self._clear_busy)

        threading.Thread(target=_run, daemon=True).start()

    def _scan_progress_callback(self, current: int, total: int, message: str) -> None:
        def _update() -> None:
            self.scan_progressbar.configure(maximum=max(1, total))
            self.scan_progress.set(max(0, min(current, total)))
            self.scan_status.set(f"{message} ({current}/{total})")

        self.root.after(0, _update)

    def _scan_report_callback(self, message: str) -> None:
        def _append() -> None:
            self._live_report_lines.append(message)
            if len(self._live_report_lines) > MAX_LIVE_REPORT_LINES:
                self._live_report_lines = self._live_report_lines[-MAX_LIVE_REPORT_LINES:]
            self.overview_text.delete("1.0", tk.END)
            self.overview_text.insert(tk.END, "\n".join(self._live_report_lines))
            self.overview_text.see(tk.END)

        self.root.after(0, _append)

    def _stream_process_row(self, rec: ProcessRecord) -> None:
        def _append() -> None:
            vals = (
                rec.pid,
                rec.name,
                f"{rec.memory_mb:.1f} МБ",
                self._level_ru(rec.memory_level.value),
                rec.exe_path,
                "н/д",
                self._heur_summary(rec),
                self._vt_summary(rec),
            )
            iid = self.process_tree.insert("", tk.END, values=vals, tags=(self._memory_tag(rec.memory_mb),))
            self._row_index[iid] = rec

        self.root.after(0, _append)

    def toggle_scan_pause(self) -> None:
        if not self._busy:
            return
        if self._scan_pause_event.is_set():
            self._scan_pause_event.clear()
            self.scan_status.set("Сканирование на паузе")
            self._emit_event(EventLevel.WARNING, "Предупреждение", "Сканирование поставлено на паузу")
        else:
            self._scan_pause_event.set()
            self.scan_status.set("Сканирование продолжено")
            self._emit_event(EventLevel.INFO, "Информация", "Сканирование продолжено")

    def stop_scan(self) -> None:
        if not self._busy:
            return
        self._cancel_scan = True
        self._scan_pause_event.set()
        self.scan_status.set("Остановка сканирования")
        self._emit_event(EventLevel.WARNING, "Предупреждение", "Запрошена остановка сканирования")

    def _after_scan(self, snapshot, report: str) -> None:
        self._stop_status_animation()
        self._all_processes = list(snapshot.process_records)
        self._all_startup = list(snapshot.startup_entries)
        self._all_services = list(snapshot.service_records)

        self._apply_process_filters()
        self._fill_startup(snapshot)
        self._fill_services(snapshot)
        self._fill_overview(snapshot)

        self._fill_diagnostics(snapshot)
        self._fill_sampling(snapshot)
        self._fill_overview_report(snapshot)

        self.load_reports()
        self.scan_status.set("Сканирование завершено")
        self.status_var.set(f"Сканирование завершено: {report}")
        self._last_snapshot = snapshot
        self._emit_event(EventLevel.INFO, "Информация", f"Сканирование завершено: {report}")

    def _clear_busy(self) -> None:
        self._busy = False

    def _handle_error(self, title: str, message: str) -> None:
        self._stop_status_animation()
        self.status_var.set(f"{title}: {message}")
        self.scan_status.set(title)
        self._emit_event(EventLevel.ERROR, "Ошибка", message)
        messagebox.showerror(title, message)

    def _configure_memory_tags(self, tree: ttk.Treeview) -> None:
        c = self._theme_colors()
        if self._normalize_theme(self._current_theme) == "light":
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
    def _memory_tag(memory_mb: float) -> str:
        if memory_mb >= 900:
            return "mem_red"
        if memory_mb >= 500:
            return "mem_yellow"
        if memory_mb >= 100 and memory_mb <= 300:
            return "mem_green"
        return "mem_white"

    @staticmethod
    def _level_ru(level: str) -> str:
        mapping = {"high": "высокий", "medium": "средний", "low": "низкий"}
        return mapping.get(level, level)

    def _autorun_tokens(self) -> set[str]:
        tokens: set[str] = set()
        for entry in self._all_startup:
            if entry.name:
                tokens.add(entry.name.lower())
            cmd = (entry.command or "").strip().strip('"')
            if cmd:
                first = cmd.split(" ")[0].strip('"')
                tokens.add(Path(first).name.lower())
        return tokens

    def _process_autorun_state(self, rec: ProcessRecord) -> str:
        tokens = self._autorun_tokens()
        name = (rec.name or "").lower()
        exe = Path(rec.exe_path).name.lower() if rec.exe_path else ""
        return "Да" if name in tokens or exe in tokens else "Нет"

    def _vt_summary(self, rec: ProcessRecord) -> str:
        vt = rec.vt_result
        if vt is None:
            return "нет данных"
        if not vt.available:
            return vt.summary or "недоступно"
        return f"M:{vt.malicious} S:{vt.suspicious} H:{vt.harmless}"

    def _heur_summary(self, rec: ProcessRecord) -> str:
        if rec.heuristic_score <= 0:
            return "0"
        if rec.heuristic_hits:
            return f"{rec.heuristic_score} ({', '.join(rec.heuristic_hits[:2])})"
        return str(rec.heuristic_score)

    def _apply_process_filters(self) -> None:
        self.process_tree.delete(*self.process_tree.get_children())
        self._row_index.clear()
        autorun_tokens = self._autorun_tokens()

        q = self.var_search.get().lower().strip()
        lvl = self.var_level.get().lower().strip() or "all"
        for rec in self._all_processes:
            if lvl != "all" and rec.memory_level.value != lvl:
                continue
            if q and q not in rec.name.lower() and q not in rec.exe_path.lower() and q not in str(rec.pid):
                continue
            vals = (
                rec.pid,
                rec.name,
                f"{rec.memory_mb:.1f} МБ",
                self._level_ru(rec.memory_level.value),
                rec.exe_path,
                ("Да" if (rec.name or "").lower() in autorun_tokens or (Path(rec.exe_path).name.lower() if rec.exe_path else "") in autorun_tokens else "Нет"),
                self._heur_summary(rec),
                self._vt_summary(rec),
            )
            iid = self.process_tree.insert("", tk.END, values=vals, tags=(self._memory_tag(rec.memory_mb),))
            self._row_index[iid] = rec
        self._restore_tree_sort(self.process_tree)

    def _fill_startup(self, snapshot) -> None:
        self._all_startup = list(snapshot.startup_entries)
        self._refresh_startup_view()

    def _refresh_startup_view(self) -> None:
        self.startup_tree.delete(*self.startup_tree.get_children())
        self._startup_index.clear()
        grouped = defaultdict(list)
        show_disabled = bool(self.var_show_disabled_startup.get())
        for rec in self._all_startup:
            if not show_disabled and not rec.enabled:
                continue
            grouped[rec.category].append(rec)

        category_map = {
            "Service": "Служба",
            "Application": "Приложение",
            "DelayedTask": "Отложенная задача",
        }
        for category in ["Service", "Application", "DelayedTask", "WMI"]:
            for rec in grouped.get(category, []):
                vals = (
                    category_map.get(rec.category, rec.category),
                    rec.name,
                    rec.command,
                    "Да" if rec.enabled else "Нет",
                    rec.location,
                )
                iid = self.startup_tree.insert("", tk.END, values=vals)
                self._startup_index[iid] = rec
        self._restore_tree_sort(self.startup_tree)

    def _fill_services(self, snapshot) -> None:
        self._all_services = list(snapshot.service_records)
        self._refresh_services_view()

    def _refresh_services_view(self) -> None:
        self.service_tree.delete(*self.service_tree.get_children())
        self._service_index.clear()
        mem_by_pid = {p.pid: p.memory_mb for p in self._all_processes}

        status_map = {
            "running": "выполняется",
            "stopped": "остановлена",
            "paused": "на паузе",
        }
        start_map = {
            "auto": "авто",
            "automatic": "авто",
            "manual": "вручную",
            "disabled": "отключена",
        }
        show_disabled = bool(self.var_show_disabled_services.get())
        for rec in self._all_services:
            start_type_l = str(rec.start_type).lower()
            if not show_disabled and start_type_l in {"disabled", "отключена"}:
                continue
            mem = mem_by_pid.get(rec.pid or -1)
            vals = (
                rec.name,
                status_map.get(str(rec.status).lower(), rec.status),
                start_map.get(str(rec.start_type).lower(), rec.start_type),
                rec.pid or "-",
                f"{mem:.1f} МБ" if mem is not None else "-",
                rec.risk_score,
                rec.risk_reason,
                rec.executable_path,
            )
            iid = self.service_tree.insert("", tk.END, values=vals)
            self._service_index[iid] = rec
        self._restore_tree_sort(self.service_tree)

    def _fill_overview(self, snapshot) -> None:
        self.overview_tree.delete(*self.overview_tree.get_children())
        self._overview_index.clear()

        grouped: dict[str, dict[str, object]] = {}

        def _group_key(path: str, fallback: str) -> str:
            cleaned = (path or "").strip()
            if cleaned:
                return cleaned.lower()
            return f"name::{(fallback or '').strip().lower()}"

        for rec in snapshot.process_records:
            display_path = (rec.exe_path or "").strip() or rec.name
            key = _group_key(rec.exe_path, rec.name)
            item = grouped.setdefault(
                key,
                {
                    "display_path": display_path,
                    "processes": [],
                    "services": [],
                    "startup": [],
                    "memory": 0.0,
                    "autorun": False,
                    "heur_max": 0,
                    "vt_max": 0,
                },
            )
            item["processes"].append(rec)
            item["memory"] = float(item["memory"]) + float(rec.memory_mb)
            item["heur_max"] = max(int(item["heur_max"]), int(rec.heuristic_score))
            vt = rec.vt_result
            vt_mal = int(vt.malicious) if vt and vt.available else 0
            item["vt_max"] = max(int(item["vt_max"]), vt_mal)
            if self._process_autorun_state(rec) == "Да":
                item["autorun"] = True

        for svc in snapshot.service_records:
            display_path = (svc.executable_path or "").strip() or svc.name
            key = _group_key(svc.executable_path, svc.name)
            item = grouped.setdefault(
                key,
                {
                    "display_path": display_path,
                    "processes": [],
                    "services": [],
                    "startup": [],
                    "memory": 0.0,
                    "autorun": False,
                    "heur_max": 0,
                    "vt_max": 0,
                },
            )
            item["services"].append(svc)

        for st in snapshot.startup_entries:
            cmd = (st.command or "").strip().strip('"')
            exe_candidate = cmd.split(" ")[0].strip('"') if cmd else ""
            if exe_candidate.lower().startswith(("runas=", "script:", "commandline:")):
                exe_candidate = ""
            display_path = exe_candidate or st.command or st.name
            key = _group_key(exe_candidate, st.name)
            item = grouped.setdefault(
                key,
                {
                    "display_path": display_path,
                    "processes": [],
                    "services": [],
                    "startup": [],
                    "memory": 0.0,
                    "autorun": False,
                    "heur_max": 0,
                    "vt_max": 0,
                },
            )
            item["startup"].append(st)
            if st.enabled:
                item["autorun"] = True

        rows = []
        for item in grouped.values():
            procs = item["processes"]
            svcs = item["services"]
            starts = item["startup"]
            kinds = []
            if procs:
                kinds.append("Процессы")
            if svcs:
                kinds.append("Службы")
            if starts:
                kinds.append("Автозагрузка")

            heur_max = int(item["heur_max"])
            vt_max = int(item["vt_max"])
            risk_chunks = []
            if heur_max > 0:
                risk_chunks.append(f"Эвристика {heur_max}")
            if vt_max > 0:
                risk_chunks.append(f"VT M:{vt_max}")
            if not risk_chunks:
                risk_chunks.append("Нет")

            svc_risk = [s for s in svcs if getattr(s, "risk_score", 0) > 0]
            detail_chunks = [
                f"PIDs: {', '.join(str(p.pid) for p in procs[:5])}" if procs else "PIDs: -",
                f"Службы: {', '.join(s.name for s in svcs[:3])}" if svcs else "Службы: -",
                f"Автозапуск: {', '.join(s.name for s in starts[:2])}" if starts else "Автозапуск: -",
            ]
            if svc_risk:
                detail_chunks.append(f"Риск служб: {max(s.risk_score for s in svc_risk)}")

            vals = (
                str(item["display_path"]),
                ", ".join(kinds) if kinds else "-",
                str(len(procs)),
                f"{float(item['memory']):.1f} МБ" if procs else "-",
                "Да" if bool(item["autorun"]) else "Нет",
                " | ".join(risk_chunks),
                f"M:{vt_max}" if vt_max > 0 else "нет данных",
                " | ".join(detail_chunks),
            )
            rows.append((vals, item))

        rows.sort(key=lambda x: str(x[0][0]).lower())
        for vals, item in rows:
            mem_val = float(item["memory"])
            tag = self._memory_tag(mem_val) if mem_val > 0 else "mem_white"
            iid = self.overview_tree.insert("", tk.END, values=vals, tags=(tag,))
            self._overview_index[iid] = ("overview_file", item)
        self._restore_tree_sort(self.overview_tree)

    def _fill_overview_report(self, snapshot) -> None:
        dangerous = []
        heavy = []
        for rec in snapshot.process_records:
            vt_mal = rec.vt_result.malicious if rec.vt_result and rec.vt_result.available else 0
            if rec.heuristic_score >= 60 or vt_mal > 0:
                dangerous.append(rec)
            if rec.memory_mb >= 500:
                heavy.append(rec)

        lines = [
            f"Сканирование: {snapshot.created_at.isoformat()}",
            f"RAM: {snapshot.used_ram_gb:.2f} / {snapshot.total_ram_gb:.2f} ГБ",
            f"Процессов: {len(snapshot.process_records)} | Автозагрузка: {len(snapshot.startup_entries)} | Службы: {len(snapshot.service_records)}",
            "",
            "Опасные процессы:",
        ]
        if dangerous:
            for rec in dangerous:
                vt = self._vt_summary(rec)
                lines.append(f"- {rec.name} (PID {rec.pid}), память {rec.memory_mb:.1f} МБ, эвристика {rec.heuristic_score}, VT: {vt}")
        else:
            lines.append("- Не обнаружены")

        lines.append("")
        lines.append("Ресурсоемкие процессы (>= 500 МБ):")
        if heavy:
            for rec in heavy:
                lines.append(f"- {rec.name} (PID {rec.pid}) - {rec.memory_mb:.1f} МБ, путь: {rec.exe_path}")
        else:
            lines.append("- Не обнаружены")

        lines.append("")
        lines.append("Краткая диагностика:")
        for d in snapshot.diagnostics:
            lines.append(f"- {d}")

        lines.append("")
        lines.append("Службы с PID и потреблением памяти:")
        mem_by_pid = {p.pid: p.memory_mb for p in snapshot.process_records}
        service_with_mem = 0
        for svc in snapshot.service_records:
            if not svc.pid:
                continue
            mem = mem_by_pid.get(svc.pid)
            if mem is None:
                continue
            service_with_mem += 1
            lines.append(f"- {svc.name} (PID {svc.pid}) - {mem:.1f} МБ, риск {svc.risk_score}, путь: {svc.executable_path}")
        if service_with_mem == 0:
            lines.append("- Нет служб с доступными метриками памяти")

        lines.append("")
        lines.append("Автозагрузка (состояние):")
        if snapshot.startup_entries:
            for s in snapshot.startup_entries[:100]:
                state = "включена" if s.enabled else "отключена"
                lines.append(f"- {s.name} | {state} | {s.location}")
        else:
            lines.append("- Не обнаружена")

        current = self._report_text()
        summary = "\n".join(lines)
        if current:
            self.overview_text.delete("1.0", tk.END)
            self.overview_text.insert(tk.END, current + "\n\n===== ИТОГ =====\n" + summary)
        else:
            self.overview_text.delete("1.0", tk.END)
            self.overview_text.insert(tk.END, summary)

    def _report_text(self) -> str:
        return self.overview_text.get("1.0", tk.END).strip()

    def save_overview_report_txt(self) -> None:
        content = self._report_text()
        if not content:
            self.status_var.set("Нет данных отчета для сохранения")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить отчет",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
            initialfile=f"overview_report_{Path(__file__).stem}.txt",
        )
        if not path:
            return
        Path(path).write_text(content, encoding="utf-8")
        self.status_var.set(f"Отчет сохранен: {path}")

    def export_overview_report_json(self) -> None:
        if self._last_snapshot is None:
            self.status_var.set("Нет данных сканирования для выгрузки")
            return
        payload = self._scan_service.snapshot_json(self._last_snapshot)
        path = filedialog.asksaveasfilename(
            title="Выгрузить отчет JSON",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialfile=f"overview_report_{Path(__file__).stem}.json",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_var.set(f"JSON выгружен: {path}")

    def _on_process_select(self, _event) -> None:
        rec = self._selected_process(silent=True)
        if rec is None:
            return
        self.process_details.delete("1.0", tk.END)
        self.process_details.insert(
            tk.END,
            json.dumps(
                {
                    "pid": rec.pid,
                    "name": rec.name,
                    "path": rec.exe_path,
                    "cmd": rec.command_line,
                    "memory_mb": rec.memory_mb,
                    "heuristic_score": rec.heuristic_score,
                    "heuristic_hits": rec.heuristic_hits,
                    "vt": self._vt_summary(rec),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def _fill_diagnostics(self, snapshot) -> None:
        self.diag_text.delete("1.0", tk.END)
        ds = snapshot.diagnostics_snapshot
        if ds is None:
            self.diag_text.insert(tk.END, "Диагностика недоступна")
            return
        self.diag_text.insert(tk.END, json.dumps(ds.__dict__, ensure_ascii=False, indent=2))

    def _fill_sampling(self, snapshot) -> None:
        self.sampling_tree.delete(*self.sampling_tree.get_children())
        for p in snapshot.sampling_points:
            top = p.top_processes[0]["name"] if p.top_processes else ""
            self.sampling_tree.insert("", tk.END, values=(p.timestamp, f"{p.used_ram_gb:.2f}", top))
        self._restore_tree_sort(self.sampling_tree)

    def load_reports(self) -> None:
        self.report_tree.delete(*self.report_tree.get_children())
        self._report_paths.clear()
        for report in self._reports_repo.list_reports():
            created = report.split("report_")[-1].replace(".json", "") if "report_" in report else "unknown"
            iid = self.report_tree.insert("", tk.END, values=(created, report))
            self._report_paths[iid] = report
        self._restore_tree_sort(self.report_tree)

    def open_report(self) -> None:
        selection = self.report_tree.selection()
        if not selection:
            return
        payload = self._reports_repo.read_report(self._report_paths[selection[0]])
        insight = self._insight.build(payload)
        text = (
            "Причины:\n"
            + "\n".join(f"- {x}" for x in insight.cause_summary)
            + "\n\nПриоритетные шаги:\n"
            + "\n".join(f"- {x}" for x in insight.priority_steps)
            + "\n\nТаймлайн:\n"
            + "\n".join(f"- {x}" for x in insight.timeline)
        )
        self.report_details.delete("1.0", tk.END)
        self.report_details.insert(tk.END, text)

    def analyze_logs(self) -> None:
        log_path = Path(__file__).resolve().parents[1] / "data" / "expert_analytics.log"
        analysis = self._log_analyzer.analyze_log(log_path)
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, f"Лог: {log_path}\n")
        self.log_text.insert(
            tk.END,
            f"Строк: {analysis.total_lines}\nERROR: {analysis.errors}\nWARNING: {analysis.warnings}\nCRITICAL: {analysis.critical}\n\n",
        )
        for report in self._reports_repo.list_reports()[:5]:
            payload = self._reports_repo.read_report(report)
            self.log_text.insert(tk.END, f"[{report}]\n{self._log_analyzer.summarize_report(payload)}\n")

    def _sync_module_info(self) -> None:
        self.module_list.delete(0, tk.END)
        for module_id, schema in self._module_registry.schema_map().items():
            self.module_list.insert(tk.END, f"{module_id}: {schema['module_name']} defaults={schema['defaults']}")

    def _selected_process(self, silent: bool = False) -> ProcessRecord | None:
        selection = self.process_tree.selection()
        if not selection:
            selection = self.overview_tree.selection()
            if selection:
                data = self._overview_index.get(selection[0])
                if data and data[0] == "process":
                    return data[1]  # type: ignore[return-value]
        if not selection:
            if not silent:
                self.status_var.set("Выберите процесс")
            return None
        return self._row_index.get(selection[0])

    def _selected_startup(self) -> StartupEntry | None:
        selection = self.startup_tree.selection()
        if selection:
            return self._startup_index.get(selection[0])
        selection = self.overview_tree.selection()
        if selection:
            data = self._overview_index.get(selection[0])
            if data and data[0] == "startup":
                return data[1]  # type: ignore[return-value]
        return None

    def _selected_service(self) -> ServiceRecord | None:
        selection = self.service_tree.selection()
        if selection:
            return self._service_index.get(selection[0])
        selection = self.overview_tree.selection()
        if selection:
            data = self._overview_index.get(selection[0])
            if data and data[0] == "service":
                return data[1]  # type: ignore[return-value]
        return None

    def _selected_overview_path(self) -> str:
        selection = self.overview_tree.selection()
        if not selection:
            return ""
        data = self._overview_index.get(selection[0])
        if not data or data[0] != "overview_file":
            return ""
        payload = data[1]
        if not isinstance(payload, dict):
            return ""
        value = str(payload.get("display_path") or "").strip()
        return value

    def _selected_overview_payload(self) -> dict[str, object] | None:
        selection = self.overview_tree.selection()
        if not selection:
            return None
        data = self._overview_index.get(selection[0])
        if not data or data[0] != "overview_file":
            return None
        payload = data[1]
        if isinstance(payload, dict):
            return payload
        return None

    def action_search_process_online(self) -> None:
        rec = self._selected_process(silent=True)
        if rec is None:
            self.status_var.set("Выберите процесс")
            return
        query = rec.name or rec.exe_path or str(rec.pid)
        self._search_online(query)

    def action_search_startup_online(self) -> None:
        rec = self._selected_startup()
        if rec is None:
            self.status_var.set("Выберите запись автозагрузки")
            return
        query = rec.name or rec.command or rec.location
        self._search_online(query)

    def action_search_service_online(self) -> None:
        rec = self._selected_service()
        if rec is None:
            self.status_var.set("Выберите службу")
            return
        query = rec.name or rec.display_name or rec.executable_path
        self._search_online(query)

    def action_search_overview_online(self) -> None:
        payload = self._selected_overview_payload()
        if payload is None:
            self.status_var.set("Выберите запись в обзоре")
            return
        query = str(payload.get("display_path") or payload.get("kind") or "").strip()
        self._search_online(query)

    def _search_online(self, query: str) -> None:
        text = (query or "").strip()
        if not text:
            self.status_var.set("Нет данных для поиска")
            return
        url = f"https://www.google.com/search?q={quote_plus(text)}"
        try:
            webbrowser.open_new_tab(url)
            self.status_var.set(f"Открыт поиск в Google: {text}")
        except Exception as exc:
            self.status_var.set(f"Не удалось открыть браузер: {exc}")

    def action_search_folder_process_online(self) -> None:
        rec = self._selected_folder_process()
        if rec is None:
            return
        query = rec.name or rec.exe_path or str(rec.pid)
        self._search_online(query)

    def _show_action_result(self, result) -> None:
        self.status_var.set(result.message)
        if "администратора" in result.message.lower() and "запустите" in result.message.lower():
            self.status_var.set(result.message + " | Текущий запуск без прав администратора")
        if result.ok:
            self._emit_event(EventLevel.INFO, "Информация", result.message)
            messagebox.showinfo("Действие", result.message)
        else:
            self._emit_event(EventLevel.ERROR, "Ошибка", result.message)
            messagebox.showerror("Действие", result.message)

    def action_terminate_process(self) -> None:
        rec = self._selected_process()
        if rec:
            self._show_action_result(self._action_service.terminate_process(rec.pid))

    def action_quarantine_exe(self) -> None:
        rec = self._selected_process()
        if rec and rec.exe_path:
            self._show_action_result(self._action_service.quarantine_file(rec.exe_path))

    def action_delete_exe(self) -> None:
        rec = self._selected_process()
        if rec and rec.exe_path:
            self._show_action_result(self._action_service.delete_executable(rec.exe_path))

    def action_open_selected_path(self) -> None:
        rec = self._selected_process()
        if rec and rec.exe_path:
            self._show_action_result(self._action_service.open_in_explorer(rec.exe_path))

    def action_open_overview_path(self) -> None:
        path = self._selected_overview_path()
        if not path:
            self.status_var.set("Выберите запись в таблице обзора")
            return
        self._show_action_result(self._action_service.open_in_explorer(path))

    def _open_overview_details(self, _event=None) -> None:
        payload = self._selected_overview_payload()
        if payload is None:
            return

        win = tk.Toplevel(self.root)
        win.title("Детали записи обзора")
        win.geometry("920x540")
        win.transient(self.root)
        try:
            win.grab_set()
        except tk.TclError:
            pass

        top = ttk.Frame(win)
        top.pack(fill=tk.X, padx=10, pady=(10, 6))
        ttk.Label(top, text="Детали записи", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Button(top, text="Закрыть", command=win.destroy).pack(side=tk.RIGHT)

        details = tk.Text(win, wrap=tk.WORD)
        details.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self._attach_scrollbars(details)

        path = str(payload.get("display_path") or "").strip()
        processes = payload.get("processes") if isinstance(payload.get("processes"), list) else []
        services = payload.get("services") if isinstance(payload.get("services"), list) else []
        startup = payload.get("startup") if isinstance(payload.get("startup"), list) else []
        heur = int(payload.get("heur_max") or 0)
        vtmax = int(payload.get("vt_max") or 0)
        mem = float(payload.get("memory") or 0.0)

        lines = [
            f"Путь: {path or '-'}",
            f"Экземпляров процессов: {len(processes)}",
            f"Служб: {len(services)}",
            f"Записей автозагрузки: {len(startup)}",
            f"Суммарная память: {mem:.1f} МБ" if mem > 0 else "Суммарная память: -",
            f"Эвристика (max): {heur}",
            f"VirusTotal M (max): {vtmax}",
            "",
            "Процессы:",
        ]
        if processes:
            for rec in processes:
                lines.append(f"- {rec.name} (PID {rec.pid}) | {rec.memory_mb:.1f} МБ | {rec.exe_path}")
        else:
            lines.append("- Нет")

        lines.append("")
        lines.append("Службы:")
        if services:
            for rec in services:
                lines.append(f"- {rec.name} | статус: {rec.status} | запуск: {rec.start_type} | путь: {rec.executable_path}")
        else:
            lines.append("- Нет")

        lines.append("")
        lines.append("Автозагрузка:")
        if startup:
            for rec in startup:
                lines.append(f"- {rec.name} | {'включена' if rec.enabled else 'отключена'} | {rec.location}")
        else:
            lines.append("- Нет")

        details.insert(tk.END, "\n".join(lines))
        details.configure(state=tk.DISABLED)

        actions = ttk.Frame(win)
        actions.pack(fill=tk.X, padx=10, pady=(0, 10))
        def _open_selected_path() -> None:
            if path:
                self._show_action_result(self._action_service.open_in_explorer(path))
            else:
                self.status_var.set("Путь для открытия не найден")

        ttk.Button(actions, text="Открыть в Проводнике", command=_open_selected_path).pack(side=tk.LEFT, padx=4)

        def _close_one_process() -> None:
            if not processes:
                self.status_var.set("Нет процессов для завершения")
                return
            proc = processes[0]
            self._show_action_result(self._action_service.terminate_process(proc.pid))

        ttk.Button(actions, text="Закрыть процесс", command=_close_one_process).pack(side=tk.LEFT, padx=4)

        if startup:
            def _remove_from_startup() -> None:
                rec = startup[0]
                self._show_action_result(self._action_service.remove_startup(rec))
                self.action_refresh_startup(silent=True)

            ttk.Button(actions, text="Убрать из автозагрузки", command=_remove_from_startup).pack(side=tk.LEFT, padx=4)

    def action_vt_lookup(self) -> None:
        rec = self._selected_process()
        if rec is None:
            return
        if not rec.file_sha256:
            self.status_var.set("У процесса нет SHA-256 для проверки")
            return
        try:
            result = self._scan_service.lookup_sha256(rec.file_sha256)
            rec.vt_result = result
            self._apply_process_filters()
            self.status_var.set(f"VirusTotal: {self._vt_summary(rec)}")
            self._emit_event(EventLevel.INFO, "Информация", f"VirusTotal для {rec.name}: {self._vt_summary(rec)}")
        except Exception as exc:
            self._show_action_result(type("Result", (), {"ok": False, "message": f"Ошибка VirusTotal: {exc}"})())

    def action_refresh_processes(self, silent: bool = False) -> None:
        try:
            settings = self._collect_settings()
            records = self._scan_service.scan_processes_only(settings.process_high_mb, settings.process_medium_mb)
            self._all_processes = list(records)
            self._apply_process_filters()
            if not silent:
                self.status_var.set("Список процессов обновлен")
                self._emit_event(EventLevel.INFO, "Информация", "Список процессов обновлен")
        except Exception as exc:
            self._handle_error("Ошибка обновления процессов", str(exc))

    def action_disable_startup(self) -> None:
        rec = self._selected_startup()
        if rec:
            result = self._action_service.disable_startup(rec)
            self._show_action_result(result)
            if result.ok:
                self.action_refresh_startup()

    def action_remove_startup(self) -> None:
        rec = self._selected_startup()
        if rec:
            result = self._action_service.remove_startup(rec)
            self._show_action_result(result)
            if result.ok:
                self.action_refresh_startup()

    def action_refresh_startup(self, silent: bool = False) -> None:
        try:
            entries = self._scan_service.scan_startup_only()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "startup_entries": entries,
                    "process_records": self._all_processes,
                    "service_records": self._all_services,
                },
            )
            self._all_startup = list(entries)
            self._fill_startup(snapshot)
            if not silent:
                self.status_var.set("Автозагрузка обновлена")
                self._emit_event(EventLevel.INFO, "Информация", "Автозагрузка обновлена")
        except Exception as exc:
            self._handle_error("Ошибка обновления автозагрузки", str(exc))

    def action_refresh_services(self, silent: bool = False) -> None:
        try:
            services = self._scan_service.scan_services_only()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "process_records": self._all_processes,
                    "service_records": services,
                    "startup_entries": self._all_startup,
                },
            )
            self._all_services = list(services)
            self._fill_services(snapshot)
            if not silent:
                self.status_var.set("Список служб обновлен")
                self._emit_event(EventLevel.INFO, "Информация", "Список служб обновлен")
        except Exception as exc:
            self._handle_error("Ошибка обновления служб", str(exc))

    def action_open_startup_path(self) -> None:
        rec = self._selected_startup()
        if rec is None:
            return
        if rec.location.startswith("StartupFolder:"):
            folder = rec.location.split(":", 1)[1].strip()
            if folder:
                target = Path(folder) / rec.name
                self._show_action_result(self._action_service.open_in_explorer(str(target)))
                return
        cmd = (rec.command or "").strip().strip('"')
        if not cmd:
            self.status_var.set("Путь для записи автозагрузки не найден")
            return
        self._show_action_result(self._action_service.open_in_explorer(cmd))

    def action_stop_service(self) -> None:
        rec = self._selected_service()
        if rec:
            result = self._action_service.stop_service(rec)
            self._show_action_result(result)
            if result.ok:
                self.action_refresh_services(silent=True)

    def action_disable_service(self) -> None:
        rec = self._selected_service()
        if rec:
            result = self._action_service.disable_service(rec)
            self._show_action_result(result)
            if result.ok:
                self.action_refresh_services(silent=True)

    def action_open_service_path(self) -> None:
        rec = self._selected_service()
        if rec and rec.executable_path:
            self._show_action_result(self._action_service.open_in_explorer(rec.executable_path))

    def pick_folder_path(self) -> None:
        path = filedialog.askdirectory(title="Выберите папку для поиска процессов")
        if path:
            self.var_folder_path.set(path)

    def scan_processes_in_folder(self) -> None:
        folder = self.var_folder_path.get().strip().strip('"')
        if not folder:
            self.status_var.set("Укажите путь к папке")
            return
        root = Path(folder)
        if not root.exists() or not root.is_dir():
            self.status_var.set("Папка не существует")
            return

        root_norm = str(root.resolve()).lower()
        self.folder_process_tree.delete(*self.folder_process_tree.get_children())
        self._folder_index.clear()
        self._folder_origin.clear()

        all_recs = self._scan_service.scan_processes_only(
            self._collect_settings().process_high_mb,
            self._collect_settings().process_medium_mb,
        )

        matched = 0
        for rec in all_recs:
            exe = str(rec.exe_path or "").lower()
            in_folder = bool(exe) and exe.startswith(root_norm)
            uses_folder = False
            if not in_folder and rec.pid:
                uses_folder = self._process_uses_folder_modules(rec.pid, root_norm)
            if not in_folder and not uses_folder:
                continue
            origin = "Запущен из папки" if in_folder else "Использует файлы папки"
            vals = (
                rec.pid,
                rec.name,
                rec.exe_path,
                origin,
                f"{rec.memory_mb:.1f} МБ",
                f"{rec.cpu_percent:.1f}%",
                rec.create_time,
            )
            iid = self.folder_process_tree.insert("", tk.END, values=vals, tags=(self._memory_tag(rec.memory_mb),))
            self._folder_index[iid] = rec
            self._folder_origin[iid] = origin
            matched += 1

        self.status_var.set(f"Процессы в папке: найдено {matched}")
        self._emit_event(EventLevel.INFO, "Информация", f"Поиск процессов по папке '{root}': найдено {matched}")

    @staticmethod
    def _process_uses_folder_modules(pid: int, root_norm: str) -> bool:
        try:
            proc = psutil.Process(pid)
            for m in proc.memory_maps(grouped=False):
                path = str(getattr(m, "path", "") or "").lower()
                if path.startswith(root_norm):
                    return True
            return False
        except Exception:
            return False

    def _selected_folder_process(self) -> ProcessRecord | None:
        sel = self.folder_process_tree.selection()
        if not sel:
            self.status_var.set("Выберите процесс в таблице папки")
            return None
        return self._folder_index.get(sel[0])

    def action_open_folder_process_path(self) -> None:
        rec = self._selected_folder_process()
        if rec and rec.exe_path:
            self._show_action_result(self._action_service.open_in_explorer(rec.exe_path))

    def action_terminate_folder_process(self) -> None:
        rec = self._selected_folder_process()
        if rec:
            self._show_action_result(self._action_service.terminate_process(rec.pid))

    def action_vt_folder_process(self) -> None:
        rec = self._selected_folder_process()
        if rec is None:
            return
        if not rec.file_sha256:
            self.status_var.set("У процесса нет SHA-256 для проверки")
            return
        result = self._scan_service.lookup_sha256(rec.file_sha256)
        rec.vt_result = result
        self.status_var.set(f"VirusTotal: {self._vt_summary(rec)}")

    def _open_folder_process_menu(self, event) -> None:
        self._menu_target(event, self.folder_process_tree)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Открыть в Проводнике", command=self.action_open_folder_process_path)
        m.add_command(label="Завершить процесс", command=self.action_terminate_folder_process)
        m.add_command(label="Проверить в VirusTotal", command=self.action_vt_folder_process)
        m.add_separator()
        m.add_command(label="Искать в интернете", command=self.action_search_folder_process_online)
        m.add_command(label="Копировать запись", command=lambda: self._copy_tree_row(self.folder_process_tree))
        m.tk_popup(event.x_root, event.y_root)

    def build_exe(self) -> None:
        if self._build_busy:
            return
        self._build_busy = True
        self.build_progress.set(0)
        self.build_status.set("Подготовка сборки")
        self._emit_event(EventLevel.INFO, "Информация", "Сборка EXE запущена")

        def _progress(percent: int, message: str) -> None:
            self.root.after(0, lambda: (self.build_progress.set(percent), self.build_status.set(message)))

        def _run() -> None:
            ok, msg = self._exe_builder.build(Path(__file__).resolve().parents[2], progress_callback=_progress)

            def _done() -> None:
                self._build_busy = False
                if ok:
                    self._emit_event(EventLevel.INFO, "Информация", f"Сборка EXE завершена: {msg}")
                    messagebox.showinfo("EXE", f"Сборка EXE завершена: {msg}")
                else:
                    self._emit_event(EventLevel.ERROR, "Ошибка", msg)
                    messagebox.showerror("EXE", msg)

            self.root.after(0, _done)

        threading.Thread(target=_run, daemon=True).start()

    def _menu_target(self, event, tree: ttk.Treeview) -> None:
        iid = tree.identify_row(event.y)
        if iid:
            tree.selection_set(iid)

    def _copy_tree_row(self, tree: ttk.Treeview) -> None:
        selection = tree.selection()
        if not selection:
            self.status_var.set("Нет выделенной записи для копирования")
            return
        values = tree.item(selection[0], "values")
        text = "\t".join(str(v) for v in values)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            self.status_var.set("Запись скопирована")
        except tk.TclError:
            self.status_var.set("Не удалось скопировать запись")

    def _open_process_menu(self, event) -> None:
        self._menu_target(event, self.process_tree)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Открыть в Проводнике", command=self.action_open_selected_path)
        m.add_command(label="Завершить процесс", command=self.action_terminate_process)
        m.add_command(label="Проверить в VirusTotal", command=self.action_vt_lookup)
        m.add_separator()
        m.add_command(label="Искать в интернете", command=self.action_search_process_online)
        m.add_command(label="Копировать запись", command=lambda: self._copy_tree_row(self.process_tree))
        m.tk_popup(event.x_root, event.y_root)

    def _open_startup_menu(self, event) -> None:
        self._menu_target(event, self.startup_tree)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Отключить", command=self.action_disable_startup)
        m.add_command(label="Удалить", command=self.action_remove_startup)
        m.add_command(label="Открыть в Проводнике", command=self.action_open_startup_path)
        m.add_separator()
        m.add_command(label="Искать в интернете", command=self.action_search_startup_online)
        m.add_command(label="Копировать запись", command=lambda: self._copy_tree_row(self.startup_tree))
        m.tk_popup(event.x_root, event.y_root)

    def _open_service_menu(self, event) -> None:
        self._menu_target(event, self.service_tree)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Остановить службу", command=self.action_stop_service)
        m.add_command(label="Отключить службу", command=self.action_disable_service)
        m.add_command(label="Открыть в Проводнике", command=self.action_open_service_path)
        m.add_separator()
        m.add_command(label="Искать в интернете", command=self.action_search_service_online)
        m.add_command(label="Копировать запись", command=lambda: self._copy_tree_row(self.service_tree))
        m.tk_popup(event.x_root, event.y_root)

    def _open_overview_menu(self, event) -> None:
        self._menu_target(event, self.overview_tree)
        selection = self.overview_tree.selection()
        if not selection:
            return
        row = self._overview_index.get(selection[0])
        if row is None:
            return
        kind = row[0]
        m = tk.Menu(self.root, tearoff=0)
        if kind == "overview_file":
            m.add_command(label="Открыть в Проводнике", command=self.action_open_overview_path)
            m.add_command(label="Открыть детали", command=self._open_overview_details)
            m.add_command(label="Искать в интернете", command=self.action_search_overview_online)
            m.add_command(label="Копировать запись", command=lambda: self._copy_tree_row(self.overview_tree))
        m.tk_popup(event.x_root, event.y_root)

    def _restore_events(self) -> None:
        events = self._event_store.load()
        self._notifier.load_events(events)
        self.events_tree.delete(*self.events_tree.get_children())
        for event in self._notifier.list_events():
            self.events_tree.insert("", tk.END, values=(event.timestamp, event.level.value, event.title, event.message))

    def clear_events(self) -> None:
        self._notifier.load_events([])
        self.events_tree.delete(*self.events_tree.get_children())
        self._event_store.save([])
        self.status_var.set("События очищены")

    def _emit_event(self, level: EventLevel, title: str, message: str) -> None:
        event = self._notifier.emit(level=level, title=title, message=message)
        self.events_tree.insert("", tk.END, values=(event.timestamp, event.level.value, event.title, event.message))
        self._event_store.save(self._notifier.list_events())

    def run(self) -> None:
        self.root.mainloop()
