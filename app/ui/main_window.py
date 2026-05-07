from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from queue import Queue, Empty
import webbrowser
import tkinter as tk
from urllib.parse import quote_plus
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import psutil

from app.application.module_registry import ModuleRegistry
from app.application.helpers.modes import MODE_LABELS, MODE_ORDER, mode_id_from_label, mode_label, normalize_mode
from app.application.services.action_service import ActionService
from app.application.services.exe_build_service import ExeBuildService
from app.application.services.heuristics_service import HeuristicEngine
from app.application.services.log_analyzer_service import LogAnalyzerService
from app.application.services.report_insight_service import ReportInsightService
from app.application.services.scan_service import ScanService
from app.application.services.settings_service import AppSettings, SettingsService
from app.domain.entities import ProcessRecord, ServiceRecord, StartupEntry, DriverRecord
from app.domain.ports import ReportRepositoryPort
from app.ui.event_store import UIEventStore
from app.ui.i18n import I18n
from app.ui.language import Language
from app.ui.notifications import EventLevel, UINotifier
from app.ui.services.theme_service import ThemeService


MAX_LIVE_REPORT_LINES = 1200
APP_VERSION = os.getenv("EXPERT_ANALYTICS_VERSION", "0.1.0")


class MainWindow:
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
        mode: str = "general",
        initial_raw: dict[str, object] | None = None,
        skip_warmup: bool = False,
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
        self._mode = normalize_mode(mode)
        self._initial_raw = dict(initial_raw or {})
        self._skip_warmup = bool(skip_warmup)
        self._theme_service = ThemeService()

        self._settings = self._settings_service.load()
        self._exe_builder = ExeBuildService()
        self._heuristics = HeuristicEngine(custom_rules=self._settings.heuristic_rule_definitions or [])
        self._scan_service.configure_heuristics(self._settings.heuristic_rule_definitions or [])
        self._notifier = UINotifier()

        self._busy = False
        self._build_busy = False
        self._cancel_scan = False
        self._scan_pause_event = threading.Event()
        self._scan_pause_event.set()

        self._row_index: dict[str, ProcessRecord] = {}
        self._startup_index: dict[str, StartupEntry] = {}
        self._service_index: dict[str, ServiceRecord] = {}
        self._driver_index: dict[str, DriverRecord] = {}
        self._overview_index: dict[str, tuple[str, object]] = {}
        self._report_paths: dict[str, str] = {}
        self._all_processes: list[ProcessRecord] = []
        self._all_startup: list[StartupEntry] = []
        self._all_services: list[ServiceRecord] = []
        self._all_drivers: list[DriverRecord] = []
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
        self._switch_mode_callback = None
        self._warmup_busy = False
        self._proc_refresh_version = 0
        self._proc_row_by_pid: dict[int, str] = {}
        self._proc_enrich_pending: dict[int, int] = {}
        self._proc_patch_queue: Queue[tuple[int, str, dict[int, object]]] = Queue()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(self._i18n.t("app.title"))
        self.root.minsize(900, 560)
        self._place_window_top(1080, 653)
        self._build_style()
        self._init_variables()
        self._build()
        self.root.deiconify()

    def _place_window_top(self, width: int, height: int) -> None:
        sw = max(1, int(self.root.winfo_screenwidth()))
        sh = max(1, int(self.root.winfo_screenheight()))
        w = min(int(width), sw)
        h = min(int(height), sh)
        x = max(0, (sw - w) // 2)
        y = 0
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def set_mode_switch_callback(self, callback) -> None:
        self._switch_mode_callback = callback

    def _build_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 10, "bold"))
        self._apply_theme(self._settings.ui_theme)

    def _theme_colors(self) -> dict[str, str]:
        return self._theme_service.colors(getattr(self, "_current_theme", "dark"))

    def _apply_theme(self, theme: str) -> None:
        self._current_theme = self._theme_service.apply(
            self.root,
            theme,
            widgets={
                "overview_text": getattr(self, "overview_text", None),
                "process_details": getattr(self, "process_details", None),
                "report_details": getattr(self, "report_details", None),
                "log_text": getattr(self, "log_text", None),
                "diag_text": getattr(self, "diag_text", None),
                "module_list": getattr(self, "module_list", None),
                "status_label": getattr(self, "status_label", None),
                "status_metrics": getattr(self, "status_metrics", None),
                "metric_ram": getattr(self, "metric_ram", None),
                "metric_cpu": getattr(self, "metric_cpu", None),
            },
        )

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

        if hasattr(self, "status_metrics"):
            try:
                self._refresh_system_metrics()
            except Exception:
                pass

        if hasattr(self, "status_var"):
            self.root.update_idletasks()

    def _init_variables(self) -> None:
        self.var_high = tk.IntVar(value=self._settings.process_high_mb)
        self.var_medium = tk.IntVar(value=self._settings.process_medium_mb)
        self.var_vt_enabled = tk.BooleanVar(value=self._settings.enable_vt_lookup)
        self.var_vt_key = tk.StringVar(value=self._settings.vt_api_key)
        self.var_mod_process = tk.BooleanVar(value=self._settings.enable_process_module)
        self.var_mod_startup = tk.BooleanVar(value=self._settings.enable_startup_module)
        self.var_mod_services = tk.BooleanVar(value=self._settings.enable_services_module)
        self.var_mod_drivers = tk.BooleanVar(value=self._settings.enable_driver_module)
        self.var_sampling_enabled = tk.BooleanVar(value=self._settings.sampling_enabled)
        self.var_sampling_points = tk.IntVar(value=self._settings.sampling_points)
        self.var_sampling_interval = tk.IntVar(value=self._settings.sampling_interval_sec)
        self.var_dry_run = tk.BooleanVar(value=self._settings.dry_run_actions)
        self.var_locale = tk.StringVar(value=self._settings.locale_code)
        self.var_theme = tk.StringVar(value=self._theme_service.normalize_theme(self._settings.ui_theme))
        self.var_cpu_limit = tk.IntVar(value=self._settings.cpu_limit_percent)
        self.var_heuristics_enabled = tk.BooleanVar(value=self._settings.heuristics_enabled)
        self.var_startup_mode = tk.StringVar(value=mode_label(self._settings.startup_mode))
        self.var_open_selected_mode_on_startup = tk.BooleanVar(value=self._settings.open_selected_mode_on_startup)
        self.var_exe_build_enabled = tk.BooleanVar(value=self._settings.exe_build_enabled)
        self.var_show_disabled_services = tk.BooleanVar(value=True)
        self.var_show_disabled_startup = tk.BooleanVar(value=True)
        self.var_heur_mode = tk.StringVar(value=self._settings.heuristic_default_mode or "scan")
        self.var_heur_pack = tk.StringVar(value=self._settings.heuristic_last_selected_pack or "all")

        self.var_search = tk.StringVar(value="")
        self.var_level = tk.StringVar(value="all")
        self.var_folder_path = tk.StringVar(value=r"C:\ProgramData")

        self.scan_progress = tk.IntVar(value=0)
        self.build_progress = tk.IntVar(value=0)
        self.scan_status = tk.StringVar(value="Ожидание сканирования")
        self.build_status = tk.StringVar(value="Ожидание сборки")

    def _build(self) -> None:
        self._build_main_menu()
        self._build_header_controls()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.frame_dashboard = ttk.Frame(self.notebook)
        self.frame_processes = ttk.Frame(self.notebook)
        self.frame_startup = ttk.Frame(self.notebook)
        self.frame_services = ttk.Frame(self.notebook)
        self.frame_drivers = ttk.Frame(self.notebook)
        self.frame_reports = ttk.Frame(self.notebook)
        self.frame_logs = ttk.Frame(self.notebook)
        self.frame_folder_processes = ttk.Frame(self.notebook)
        self.frame_settings = ttk.Frame(self.notebook)
        self.frame_events = ttk.Frame(self.notebook)
        self.frame_diagnostics = ttk.Frame(self.notebook)
        self.frame_sampling = ttk.Frame(self.notebook)

        self.notebook.add(self.frame_dashboard, text="Сканирование")
        self.notebook.add(self.frame_processes, text="Процессы")
        self.notebook.add(self.frame_startup, text="Автозагрузка")
        self.notebook.add(self.frame_services, text="Службы")
        self.notebook.add(self.frame_drivers, text="Драйверы")
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
        self._build_drivers_tab()
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
        self.metric_cpu = tk.Label(self.status_metrics, text="CPU: -", font=("Segoe UI", 9, "bold"), padx=8, pady=1, bd=1, relief=tk.SOLID)
        self.metric_ram.pack(side=tk.LEFT)
        self.metric_cpu.pack(side=tk.LEFT)

        self.version_label = ttk.Label(self.status_bar, text=f"v{APP_VERSION}", style="Status.TLabel", anchor=tk.E, padding=(8, 4))
        self.version_label.pack(side=tk.RIGHT)

        self._apply_theme(self._current_theme)
        self._start_metrics_updater()
        self._apply_mode_layout()

        if self._mode == "general":
            self.load_reports()
            self._sync_module_info()
            self._restore_events()
        else:
            self._restore_events()
        if self._initial_raw:
            self._apply_initial_raw_data()
        if not self._skip_warmup:
            self.root.after(150, self._warmup_on_start)

    def _apply_initial_raw_data(self) -> None:
        processes = self._initial_raw.get("processes")
        startup = self._initial_raw.get("startup")
        services = self._initial_raw.get("services")
        drivers = self._initial_raw.get("drivers")

        if isinstance(processes, list):
            self._all_processes = list(processes)
            self._apply_process_filters()
        if isinstance(startup, list):
            self._all_startup = list(startup)
            self._refresh_startup_view()
        if isinstance(services, list):
            self._all_services = list(services)
            self._refresh_services_view()
        if isinstance(drivers, list):
            self._all_drivers = list(drivers)
            self._refresh_drivers_view()

    def _build_main_menu(self) -> None:
        menu_bar = tk.Menu(self.root, tearoff=0)

        app_menu = tk.Menu(menu_bar, tearoff=0)
        app_menu.add_command(label="Закрыть приложение", command=self._close_window)
        menu_bar.add_cascade(label="Меню", menu=app_menu)

        modes_menu = tk.Menu(menu_bar, tearoff=0)
        for mode in MODE_ORDER:
            modes_menu.add_command(label=MODE_LABELS[mode], command=lambda m=mode: self._open_mode_from_menu(m))
        menu_bar.add_cascade(label="Режимы", menu=modes_menu)

        if self._mode != "general":
            settings_menu = tk.Menu(menu_bar, tearoff=0)
            settings_menu.add_command(label="Открыть настройки", command=self._open_settings_from_menu)
            menu_bar.add_cascade(label="Настройки", menu=settings_menu)

        self.root.configure(menu=menu_bar)

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
        try:
            cpu = psutil.cpu_percent(interval=None)
        except Exception:
            cpu = None

        self._set_metric(self.metric_ram, "RAM", ram)
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
        dark = self._theme_service.normalize_theme(self._current_theme) == "dark"
        if value >= 80:
            return "#ff8a8a" if dark else "#c62b2b"
        if value >= 50:
            return "#ffd86b" if dark else "#9a7a00"
        if value >= 20:
            return "#8ee6b0" if dark else "#1e7d47"
        return self._theme_colors()["text"]

    def _metric_palette(self, value: float) -> tuple[str, str]:
        return self._theme_service.metric_palette(self._current_theme, value)

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
                encoding="utf-8",
                errors="replace",
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
                encoding="utf-8",
                errors="replace",
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

    def _close_window(self) -> None:
        if self._metrics_after_id is not None:
            try:
                self.root.after_cancel(self._metrics_after_id)
            except tk.TclError:
                pass
            self._metrics_after_id = None
        self._stop_status_animation()
        self.root.destroy()

    def _warmup_on_start(self) -> None:
        if self._warmup_busy:
            return
        settings = self._collect_settings()
        self._warmup_busy = True
        self._start_status_animation("Фоновое обновление данных")
        threading.Thread(
            target=self._warmup_worker,
            args=(settings.process_high_mb, settings.process_medium_mb, self._mode),
            daemon=True,
        ).start()

    def _warmup_worker(self, high_mb: int, medium_mb: int, mode: str) -> None:
        try:
            processes = []
            services = []
            startup = []
            drivers = []

            if mode in {"general", "scanning", "processes", "heuristics"}:
                processes = self._scan_service.scan_processes_only(high_mb, medium_mb)
                self.root.after(0, lambda: self._start_status_animation("Фоновое обновление: процессы/цвета"))
            if mode in {"general", "services"}:
                self.root.after(0, lambda: self._start_status_animation("Фоновое обновление: службы"))
                services = self._scan_service.scan_services_only()
            if mode in {"general", "startup"}:
                self.root.after(0, lambda: self._start_status_animation("Фоновое обновление: автозагрузка"))
                startup = self._scan_service.scan_startup_only()
            if mode in {"general", "drivers"}:
                self.root.after(0, lambda: self._start_status_animation("Фоновое обновление: драйверы"))
                drivers = self._scan_service.scan_drivers_only()

            def _apply() -> None:
                self._all_processes = list(processes)
                self._all_services = list(services)
                self._all_startup = list(startup)
                self._all_drivers = list(drivers)

                proc_snapshot = type("Snapshot", (), {"process_records": self._all_processes, "service_records": self._all_services, "startup_entries": self._all_startup, "driver_records": self._all_drivers})
                if mode in {"general", "scanning", "processes", "heuristics"}:
                    self._apply_process_filters()
                if mode in {"general", "services"}:
                    self._fill_services(proc_snapshot)
                if mode in {"general", "startup"}:
                    self._fill_startup(proc_snapshot)
                if mode in {"general", "drivers"}:
                    self._fill_drivers(proc_snapshot)

            self.root.after(0, _apply)
        except Exception as exc:
            self.root.after(0, lambda: self._handle_error("Ошибка фонового обновления", str(exc)))
        finally:
            self.root.after(0, self._finish_warmup)

    def _finish_warmup(self) -> None:
        self._warmup_busy = False
        self._stop_status_animation("Готово")

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

    def _active_mode_label(self) -> str:
        return mode_label(self._mode)

    def _mode_to_tab_frame(self):
        mapping = {
            "scanning": getattr(self, "frame_dashboard", None),
            "processes": getattr(self, "frame_processes", None),
            "startup": getattr(self, "frame_startup", None),
            "services": getattr(self, "frame_services", None),
            "drivers": getattr(self, "frame_drivers", None),
            "heuristics": getattr(self, "frame_processes", None),
            "general": getattr(self, "frame_dashboard", None),
        }
        return mapping.get(self._mode)

    def _apply_mode_layout(self) -> None:
        if self._mode == "general":
            return

        tab_count = self.notebook.index("end")
        for idx in range(tab_count - 1, -1, -1):
            self.notebook.forget(idx)

        tab = self._mode_to_tab_frame()
        if tab is not None:
            self.notebook.add(tab, text=self._active_mode_label())

    def _open_mode_from_menu(self, mode: str) -> None:
        normalized = normalize_mode(mode)
        if normalized == self._mode:
            return
        if callable(self._switch_mode_callback):
            self._switch_mode_callback(normalized)
            self._close_window()

    def _open_settings_from_menu(self) -> None:
        if self._mode == "general":
            return
        try:
            idx = self.notebook.index(self.frame_settings)
        except Exception:
            self.notebook.add(self.frame_settings, text="Настройки")
            idx = self.notebook.index(self.frame_settings)
        self.notebook.select(idx)

    def _build_header_controls(self) -> None:
        head = ttk.Frame(self.root)
        head.pack(fill=tk.X, padx=10, pady=(10, 4))

        scan_box = ttk.LabelFrame(head, text="Сканирование")
        if self._mode in {"general", "scanning"}:
            scan_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

            ttk.Button(scan_box, text="Запустить сканирование", command=self.scan).grid(row=0, column=0, padx=4, pady=4)
            ttk.Button(scan_box, text="Пауза/Продолжить", command=self.toggle_scan_pause).grid(row=0, column=1, padx=4, pady=4)
            ttk.Button(scan_box, text="Стоп", command=self.stop_scan).grid(row=0, column=2, padx=4, pady=4)

            self.scan_progressbar = ttk.Progressbar(scan_box, maximum=100, variable=self.scan_progress)
            self.scan_progressbar.grid(row=1, column=0, columnspan=4, sticky="ew", padx=4)
            ttk.Label(scan_box, textvariable=self.scan_status).grid(row=2, column=0, columnspan=4, sticky=tk.W, padx=4, pady=(2, 4))

        build_box = ttk.LabelFrame(head, text="Сборка EXE")
        if self._settings.exe_build_enabled and self._mode == "general":
            build_box.pack(side=tk.LEFT, fill=tk.X, expand=True)

            ttk.Button(build_box, text="Собрать EXE", command=self.build_exe).grid(row=0, column=0, padx=4, pady=4)
            ttk.Progressbar(build_box, maximum=100, variable=self.build_progress).grid(row=1, column=0, sticky="ew", padx=4)
            ttk.Label(build_box, textvariable=self.build_status).grid(row=2, column=0, sticky=tk.W, padx=4, pady=(2, 4))

        scan_box.columnconfigure(3, weight=1)
        build_box.columnconfigure(0, weight=1)

    def _warmup_is_running(self, action_label: str) -> bool:
        if self._warmup_busy:
            self.status_var.set(f"{action_label} недоступно: идет фоновая загрузка")
            return True
        return False

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
            columns=("name", "status", "start", "pid", "memory", "trust", "risk", "reason", "path"),
            show="headings",
        )
        headers = {
            "name": "Имя",
            "status": "Статус",
            "start": "Запуск",
            "pid": "PID",
            "memory": "Память",
            "trust": "Доверие",
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
            "trust": 110,
            "risk": 70,
            "reason": 320,
            "path": 520,
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

    def _build_drivers_tab(self) -> None:
        self.driver_tree = ttk.Treeview(
            self.frame_drivers,
            columns=("name", "state", "start", "size", "risk", "resource", "path"),
            show="headings",
        )
        headers = {
            "name": "Имя",
            "state": "Статус",
            "start": "Запуск",
            "size": "Размер",
            "risk": "Риск",
            "resource": "Нагрузка",
            "path": "Путь",
        }
        widths = {
            "name": 220,
            "state": 110,
            "start": 110,
            "size": 100,
            "risk": 90,
            "resource": 100,
            "path": 720,
        }
        for c in headers:
            self.driver_tree.heading(c, text=headers[c])
            self.driver_tree.column(c, width=widths[c], anchor=tk.W)
        self._enable_tree_sorting(self.driver_tree, list(headers.keys()))
        self.driver_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._attach_scrollbars(self.driver_tree)
        self.driver_tree.bind("<Button-3>", self._open_driver_menu)

        b = ttk.Frame(self.frame_drivers)
        b.pack(fill=tk.X, padx=10, pady=(0, 8))
        ttk.Button(b, text="Обновить", command=self.action_refresh_drivers).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Открыть в Проводнике", command=self.action_open_driver_path).pack(side=tk.LEFT, padx=4)

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

        ttk.Label(tab_general, text="Режим запуска по умолчанию").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        startup_combo = ttk.Combobox(
            tab_general,
            textvariable=self.var_startup_mode,
            values=[MODE_LABELS[m] for m in MODE_ORDER],
            state="readonly",
            width=16,
        )
        startup_combo.grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        ttk.Checkbutton(
            tab_general,
            text="Открывать сразу выбранный",
            variable=self.var_open_selected_mode_on_startup,
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=6, pady=4)
        row += 1

        ttk.Checkbutton(tab_general, text="Разрешить сборку EXE", variable=self.var_exe_build_enabled).grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=6, pady=4)
        row += 1

        ttk.Label(tab_general, text="Количество точек сэмплинга").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_sampling_points, width=12).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        ttk.Label(tab_general, text="Интервал сэмплинга, сек").grid(row=row, column=0, sticky=tk.W, padx=6, pady=6)
        ttk.Entry(tab_general, textvariable=self.var_sampling_interval, width=12).grid(row=row, column=1, sticky=tk.W, padx=6)
        row += 1

        author = ttk.LabelFrame(tab_general, text="Об авторе")
        author.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=8)
        ttk.Label(author, text="Евгений Поляков").pack(anchor=tk.W, padx=8, pady=(6, 2))
        ttk.Label(author, text="@jounik53").pack(anchor=tk.W, padx=8, pady=(0, 6))

        ttk.Checkbutton(tab_modules, text="Сканировать процессы", variable=self.var_mod_process).pack(anchor=tk.W, padx=8, pady=6)
        ttk.Checkbutton(tab_modules, text="Сканировать автозагрузку", variable=self.var_mod_startup).pack(anchor=tk.W, padx=8, pady=6)
        ttk.Checkbutton(tab_modules, text="Сканировать службы", variable=self.var_mod_services).pack(anchor=tk.W, padx=8, pady=6)
        ttk.Checkbutton(tab_modules, text="Сканировать драйверы", variable=self.var_mod_drivers).pack(anchor=tk.W, padx=8, pady=6)

        self.heuristic_vars: dict[str, tk.BooleanVar] = {}
        ttk.Checkbutton(tab_heur, text="Включить эвристику", variable=self.var_heuristics_enabled).pack(anchor=tk.W, padx=8, pady=6)
        defaults = self._settings.heuristic_rules or self._heuristics.schema()
        for rule in self._heuristics.rules:
            val = bool(defaults.get(rule.rule_id, True))
            var = tk.BooleanVar(value=val)
            self.heuristic_vars[rule.rule_id] = var
            ttk.Checkbutton(tab_heur, text=f"{rule.title} ({rule.rule_id})", variable=var).pack(anchor=tk.W, padx=8, pady=2)

        editor = ttk.LabelFrame(tab_heur, text="Правила (JSON DSL)")
        editor.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        self.heur_rules_text = tk.Text(editor, height=14)
        self.heur_rules_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._attach_scrollbars(self.heur_rules_text)
        current_defs = self._settings.heuristic_rule_definitions or []
        if not current_defs:
            current_defs = self._heuristics.definitions_payload()
        self.heur_rules_text.insert(tk.END, json.dumps(current_defs, ensure_ascii=False, indent=2))

        editor_btn = ttk.Frame(tab_heur)
        editor_btn.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Button(editor_btn, text="Проверить правила", command=self.action_validate_heur_rules).pack(side=tk.LEFT, padx=2)
        ttk.Button(editor_btn, text="Сбросить на встроенные", command=self.action_reset_heur_rules).pack(side=tk.LEFT, padx=2)
        ttk.Button(editor_btn, text="Применить правила", command=self.action_apply_heur_rules).pack(side=tk.LEFT, padx=2)

        run_box = ttk.LabelFrame(tab_heur, text="Режим эвристики")
        run_box.pack(fill=tk.X, padx=8, pady=(4, 8))
        ttk.Label(run_box, text="Применение:").pack(side=tk.LEFT, padx=(6, 2))
        ttk.Combobox(run_box, textvariable=self.var_heur_mode, values=["scan", "heuristic"], state="readonly", width=12).pack(side=tk.LEFT, padx=2)
        ttk.Label(run_box, text="Пакет:").pack(side=tk.LEFT, padx=(10, 2))
        pack_names = list((self._settings.heuristic_rule_packs or self._heuristics.default_rule_packs()).keys())
        ttk.Combobox(run_box, textvariable=self.var_heur_pack, values=pack_names, state="readonly", width=22).pack(side=tk.LEFT, padx=2)
        ttk.Button(run_box, text="Проверить процесс", command=self.action_heuristic_check_process).pack(side=tk.LEFT, padx=4)
        ttk.Button(run_box, text="Проверить файл", command=self.action_heuristic_check_file).pack(side=tk.LEFT, padx=4)

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
        packs = self._settings.heuristic_rule_packs or self._heuristics.default_rule_packs()
        current_text = ""
        if hasattr(self, "heur_rules_text"):
            current_text = self.heur_rules_text.get("1.0", tk.END)
        raw_defs, parse_diags = HeuristicEngine.parse_rules_text(current_text)
        if parse_diags:
            raw_defs = self._settings.heuristic_rule_definitions or []
        return AppSettings(
            vt_api_key=self.var_vt_key.get().strip(),
            process_high_mb=int(self.var_high.get() or 900),
            process_medium_mb=int(self.var_medium.get() or 300),
            enable_vt_lookup=bool(self.var_vt_enabled.get()),
            enable_process_module=bool(self.var_mod_process.get()),
            enable_startup_module=bool(self.var_mod_startup.get()),
            enable_services_module=bool(self.var_mod_services.get()),
            enable_driver_module=bool(self.var_mod_drivers.get()),
            sampling_enabled=bool(self.var_sampling_enabled.get()),
            sampling_points=int(self.var_sampling_points.get() or 5),
            sampling_interval_sec=int(self.var_sampling_interval.get() or 3),
            dry_run_actions=bool(self.var_dry_run.get()),
            locale_code=self.var_locale.get() or Language.RU.value,
            ui_theme=self._theme_service.normalize_theme(self.var_theme.get()),
            cpu_limit_percent=cpu_limit,
            heuristics_enabled=bool(self.var_heuristics_enabled.get()),
            heuristic_rules={k: bool(v.get()) for k, v in getattr(self, "heuristic_vars", {}).items()},
            heuristic_rule_definitions=raw_defs,
            heuristic_rule_packs=packs,
            heuristic_default_mode=str(self.var_heur_mode.get() or "scan"),
            heuristic_last_selected_rules=[k for k, v in getattr(self, "heuristic_vars", {}).items() if bool(v.get())],
            heuristic_last_selected_pack=str(self.var_heur_pack.get() or "all"),
            startup_mode=normalize_mode(mode_id_from_label(self.var_startup_mode.get())),
            open_selected_mode_on_startup=bool(self.var_open_selected_mode_on_startup.get()),
            exe_build_enabled=bool(self.var_exe_build_enabled.get()),
        )

    def save_settings(self) -> None:
        settings = self._collect_settings()
        self._heuristics = HeuristicEngine(custom_rules=settings.heuristic_rule_definitions or [])
        self._scan_service.configure_heuristics(settings.heuristic_rule_definitions or [])
        self._settings_service.save(settings)
        self._settings = settings
        self._apply_theme(settings.ui_theme)
        if settings.startup_mode != self._mode:
            self.status_var.set(f"Настройки сохранены. Режим запуска: {mode_label(settings.startup_mode)}")
        else:
            self.status_var.set("Настройки сохранены")
        self._emit_event(EventLevel.INFO, "Информация", "Настройки сохранены")

    def action_validate_heur_rules(self) -> None:
        text = self.heur_rules_text.get("1.0", tk.END)
        payload, parse_diags = HeuristicEngine.parse_rules_text(text)
        if parse_diags:
            msg = "\n".join(d.format_like_compiler() for d in parse_diags)
            self.process_details.delete("1.0", tk.END)
            self.process_details.insert(tk.END, msg)
            self.status_var.set("Ошибка синтаксиса правил")
            return
        eng = HeuristicEngine()
        sem_diags = eng.validate_raw_rules(payload)
        if sem_diags:
            msg = "\n".join(d.format_like_compiler() for d in sem_diags)
            self.process_details.delete("1.0", tk.END)
            self.process_details.insert(tk.END, msg)
            self.status_var.set("Ошибка валидации правил")
            return
        self.process_details.delete("1.0", tk.END)
        self.process_details.insert(tk.END, "OK: правила валидны")
        self.status_var.set("Правила валидны")

    def action_reset_heur_rules(self) -> None:
        builtin = HeuristicEngine().definitions_payload()
        self.heur_rules_text.delete("1.0", tk.END)
        self.heur_rules_text.insert(tk.END, json.dumps(builtin, ensure_ascii=False, indent=2))
        self.status_var.set("Правила сброшены на встроенные")

    def action_apply_heur_rules(self) -> None:
        text = self.heur_rules_text.get("1.0", tk.END)
        payload, parse_diags = HeuristicEngine.parse_rules_text(text)
        if parse_diags:
            self.process_details.delete("1.0", tk.END)
            self.process_details.insert(tk.END, "\n".join(d.format_like_compiler() for d in parse_diags))
            self.status_var.set("Не удалось применить правила")
            return
        probe = HeuristicEngine()
        sem_diags = probe.validate_raw_rules(payload)
        if sem_diags:
            self.process_details.delete("1.0", tk.END)
            self.process_details.insert(tk.END, "\n".join(d.format_like_compiler() for d in sem_diags))
            self.status_var.set("Не удалось применить правила")
            return
        self._heuristics = HeuristicEngine(custom_rules=payload)
        self._scan_service.configure_heuristics(payload)
        if hasattr(self, "heuristic_vars"):
            for child in list(self.heuristic_vars.keys()):
                if child not in self._heuristics.schema():
                    self.heuristic_vars.pop(child, None)
            for rid, enabled in self._heuristics.schema().items():
                if rid not in self.heuristic_vars:
                    self.heuristic_vars[rid] = tk.BooleanVar(value=enabled)
        self.status_var.set("Правила применены")

    def _selected_rule_ids_for_manual(self) -> set[str] | None:
        pack_name = str(self.var_heur_pack.get() or "all")
        packs = self._settings.heuristic_rule_packs or self._heuristics.default_rule_packs()
        if pack_name in packs and packs.get(pack_name):
            return set(packs.get(pack_name, []))
        selected = {rid for rid, var in self.heuristic_vars.items() if bool(var.get())}
        return selected if selected else None

    def action_heuristic_check_process(self) -> None:
        rec = self._selected_process(silent=True)
        if rec is None:
            self.status_var.set("Выберите процесс")
            return
        selected_ids = self._selected_rule_ids_for_manual()
        result = self._heuristics.evaluate_one(
            rec,
            enabled={k: bool(v.get()) for k, v in self.heuristic_vars.items()},
            mode="heuristic",
            selected_rule_ids=selected_ids,
        )
        rec.heuristic_score = result.score
        rec.heuristic_hits = result.hits
        self._on_process_select(None)
        self.status_var.set(f"Эвристика процесса: score={result.score}, hits={len(result.hits)}")

    def action_heuristic_check_file(self) -> None:
        path = filedialog.askopenfilename(title="Выберите файл для эвристической проверки")
        if not path:
            return
        selected_ids = self._selected_rule_ids_for_manual()
        result = self._heuristics.evaluate_file(
            path,
            enabled={k: bool(v.get()) for k, v in self.heuristic_vars.items()},
            mode="heuristic",
            selected_rule_ids=selected_ids,
        )
        payload = {
            "file": path,
            "score": result.score,
            "hits": result.hits,
            "matches": [m.__dict__ for m in result.matches],
        }
        self.process_details.delete("1.0", tk.END)
        self.process_details.insert(tk.END, json.dumps(payload, ensure_ascii=False, indent=2))
        self.status_var.set(f"Эвристика файла: score={result.score}, hits={len(result.hits)}")

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
                    enable_driver_module=settings.enable_driver_module,
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
        self._all_drivers = list(getattr(snapshot, "driver_records", []) or [])

        self._apply_process_filters()
        self._fill_startup(snapshot)
        self._fill_services(snapshot)
        self._fill_drivers(snapshot)
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
        self._theme_service.apply_memory_tags(tree, self._current_theme)

    @staticmethod
    def _memory_tag(memory_mb: float) -> str:
        if memory_mb >= 900:
            return "mem_red"
        if memory_mb >= 500:
            return "mem_yellow"
        if memory_mb <= 300:
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
        self._proc_row_by_pid.clear()
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
            self._proc_row_by_pid[int(rec.pid)] = iid
        self._restore_tree_sort(self.process_tree)

    def _render_processes_base(self, records: list[ProcessRecord], version: int) -> None:
        if version != self._proc_refresh_version:
            return
        self.process_tree.delete(*self.process_tree.get_children())
        self._row_index.clear()
        self._proc_row_by_pid.clear()
        q = self.var_search.get().lower().strip()
        lvl = self.var_level.get().lower().strip() or "all"
        for rec in records:
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
                "...",
                "...",
                "...",
            )
            iid = self.process_tree.insert("", tk.END, values=vals)
            self._row_index[iid] = rec
            self._proc_row_by_pid[int(rec.pid)] = iid
        self._restore_tree_sort(self.process_tree)

    def _start_proc_stage(self, version: int, text: str) -> None:
        if version != self._proc_refresh_version:
            return
        self._proc_enrich_pending[version] = self._proc_enrich_pending.get(version, 0) + 1
        self._start_status_animation(text)

    def _finish_proc_stage(self, version: int) -> None:
        if version != self._proc_refresh_version:
            return
        remain = max(0, self._proc_enrich_pending.get(version, 1) - 1)
        self._proc_enrich_pending[version] = remain
        if remain == 0:
            self._stop_status_animation("Список процессов обновлен")

    def _enqueue_proc_patch(self, version: int, patch_kind: str, payload: dict[int, object]) -> None:
        self._proc_patch_queue.put((version, patch_kind, payload))
        self.root.after(0, self._apply_proc_patches)

    def _apply_proc_patches(self) -> None:
        while True:
            try:
                version, patch_kind, payload = self._proc_patch_queue.get_nowait()
            except Empty:
                break
            if version != self._proc_refresh_version:
                continue
            for pid, value in payload.items():
                iid = self._proc_row_by_pid.get(int(pid))
                if not iid:
                    continue
                if patch_kind == "color":
                    tag = self._memory_tag(float(value))
                    self.process_tree.item(iid, tags=(tag,))
                    continue
                rec = self._row_index.get(iid)
                if rec is None:
                    continue
                vals = list(self.process_tree.item(iid, "values"))
                if len(vals) < 8:
                    continue
                if patch_kind == "autorun":
                    vals[5] = str(value)
                elif patch_kind == "heur":
                    vals[6] = str(value)
                elif patch_kind == "vt":
                    vals[7] = str(value)
                self.process_tree.item(iid, values=tuple(vals))

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
                "trusted" if rec.trusted else "untrusted",
                rec.risk_score,
                (f"trusted: {rec.trust_reason}" if rec.trusted else rec.risk_reason),
                rec.executable_path,
            )
            tag = "mem_green" if rec.trusted else ("mem_red" if rec.risk_score >= 60 else "mem_yellow" if rec.risk_score >= 30 else "mem_white")
            iid = self.service_tree.insert("", tk.END, values=vals, tags=(tag,))
            self._service_index[iid] = rec
        self._restore_tree_sort(self.service_tree)

    def _fill_drivers(self, snapshot) -> None:
        self._all_drivers = list(getattr(snapshot, "driver_records", []) or [])
        self._refresh_drivers_view()

    def _refresh_drivers_view(self) -> None:
        self.driver_tree.delete(*self.driver_tree.get_children())
        self._driver_index = {}
        for rec in self._all_drivers:
            vals = (
                rec.name,
                rec.state,
                rec.start_mode,
                f"{rec.image_size_mb:.2f} МБ" if rec.image_size_mb > 0 else "-",
                rec.risk_score,
                rec.resource_score,
                rec.executable_path,
            )
            tag = "mem_red" if rec.risk_score >= 60 or rec.resource_score >= 70 else "mem_yellow" if rec.risk_score >= 30 or rec.resource_score >= 40 else "mem_white"
            iid = self.driver_tree.insert("", tk.END, values=vals, tags=(tag,))
            self._driver_index[iid] = rec
        self._restore_tree_sort(self.driver_tree)

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
            trusted_svcs = [s for s in svcs if getattr(s, "trusted", False)]
            if trusted_svcs:
                detail_chunks.append(f"Trusted служб: {len(trusted_svcs)}")

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

        lines.append("")
        lines.append("Драйверы (риск/нагрузка):")
        drivers = list(getattr(snapshot, "driver_records", []) or [])
        if drivers:
            for d in drivers[:100]:
                lines.append(
                    f"- {d.name} | риск {d.risk_score} | нагрузка {d.resource_score} | запуск {d.start_mode} | путь: {d.executable_path or '-'}"
                )
        else:
            lines.append("- Не обнаружены")

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
        if not hasattr(self, "module_list"):
            return
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

    def _selected_driver(self) -> DriverRecord | None:
        selection = self.driver_tree.selection()
        if not selection:
            return None
        return self._driver_index.get(selection[0])

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

    def action_search_driver_online(self) -> None:
        rec = self._selected_driver()
        if rec is None:
            self.status_var.set("Выберите драйвер")
            return
        query = rec.name or rec.display_name or rec.executable_path
        self._search_online(query)

    def action_search_overview_online(self) -> None:
        payload = self._selected_overview_payload()
        if payload is None:
            self.status_var.set("Выберите запись в сканировании")
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
            self._emit_event(EventLevel.INFO, "Rollback", f"Точка отката зафиксирована для действия: {result.message}")
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
            self.status_var.set("Выберите запись в таблице сканирования")
            return
        self._show_action_result(self._action_service.open_in_explorer(path))

    def _open_overview_details(self, _event=None) -> None:
        payload = self._selected_overview_payload()
        if payload is None:
            return

        win = tk.Toplevel(self.root)
        win.title("Детали записи сканирования")
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
        if self._warmup_is_running("Обновление процессов"):
            return
        settings = self._collect_settings()
        self._proc_refresh_version += 1
        version = self._proc_refresh_version
        self._proc_enrich_pending[version] = 0
        self._start_status_animation("Фоновое обновление данных")

        def _raw_fetch() -> None:
            try:
                records = self._scan_service.scan_processes_only(settings.process_high_mb, settings.process_medium_mb)
            except Exception as exc:
                self.root.after(0, lambda: self._handle_error("Ошибка обновления процессов", str(exc)))
                return

            def _apply_base() -> None:
                if version != self._proc_refresh_version:
                    return
                self._all_processes = list(records)
                self._render_processes_base(self._all_processes, version)
                self._stop_status_animation("Сырые данные процессов получены")
                self._run_process_enrichment(version, settings)

            self.root.after(0, _apply_base)

        threading.Thread(target=_raw_fetch, daemon=True).start()

        if not silent:
            self._emit_event(EventLevel.INFO, "Информация", "Запущено фоновое обновление процессов")

    def _run_process_enrichment(self, version: int, settings: AppSettings) -> None:
        records = list(self._all_processes)

        self._start_proc_stage(version, "Фоновое обновление: цвета процессов")

        def _colors_worker() -> None:
            payload = {int(r.pid): float(r.memory_mb) for r in records}
            self._enqueue_proc_patch(version, "color", payload)
            self.root.after(0, lambda: self._finish_proc_stage(version))

        threading.Thread(target=_colors_worker, daemon=True).start()

        self._start_proc_stage(version, "Фоновое обновление: автозагрузка процессов")

        def _autorun_worker() -> None:
            tokens = self._autorun_tokens()
            payload: dict[int, object] = {}
            for rec in records:
                name = (rec.name or "").lower()
                exe = Path(rec.exe_path).name.lower() if rec.exe_path else ""
                payload[int(rec.pid)] = "Да" if name in tokens or exe in tokens else "Нет"
            self._enqueue_proc_patch(version, "autorun", payload)
            self.root.after(0, lambda: self._finish_proc_stage(version))

        threading.Thread(target=_autorun_worker, daemon=True).start()

        if settings.heuristics_enabled:
            self._start_proc_stage(version, "Фоновое обновление: эвристика процессов")

            def _heur_worker() -> None:
                enabled_map = settings.heuristic_rules or self._heuristics.schema()
                payload: dict[int, object] = {}
                for rec in records:
                    score = 0
                    hits: list[str] = []
                    for rule in self._heuristics.rules:
                        if "scan" not in rule.applies_to:
                            continue
                        if not enabled_map.get(rule.rule_id, True):
                            continue
                        delta, msg = rule.evaluate(rec)
                        if delta > 0 and msg:
                            score += delta
                            hits.append(f"{rule.title}: {msg}")
                    rec.heuristic_score = min(100, score)
                    rec.heuristic_hits = hits
                    payload[int(rec.pid)] = self._heur_summary(rec)
                self._enqueue_proc_patch(version, "heur", payload)
                self.root.after(0, lambda: self._finish_proc_stage(version))

            threading.Thread(target=_heur_worker, daemon=True).start()

        if settings.enable_vt_lookup:
            self._start_proc_stage(version, "Фоновое обновление: VirusTotal процессов")

            def _vt_worker() -> None:
                payload: dict[int, object] = {}
                for rec in records:
                    if version != self._proc_refresh_version:
                        return
                    if not rec.file_sha256:
                        payload[int(rec.pid)] = "нет данных"
                        continue
                    try:
                        rec.vt_result = self._scan_service.lookup_sha256(rec.file_sha256)
                    except Exception:
                        rec.vt_result = None
                    payload[int(rec.pid)] = self._vt_summary(rec)
                self._enqueue_proc_patch(version, "vt", payload)
                self.root.after(0, lambda: self._finish_proc_stage(version))

            threading.Thread(target=_vt_worker, daemon=True).start()

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
        if self._warmup_is_running("Обновление автозагрузки"):
            return
        try:
            entries = self._scan_service.scan_startup_only()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "startup_entries": entries,
                    "process_records": self._all_processes,
                    "service_records": self._all_services,
                    "driver_records": self._all_drivers,
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
        if self._warmup_is_running("Обновление служб"):
            return
        try:
            services = self._scan_service.scan_services_only()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "process_records": self._all_processes,
                    "service_records": services,
                    "startup_entries": self._all_startup,
                    "driver_records": self._all_drivers,
                },
            )
            self._all_services = list(services)
            self._fill_services(snapshot)
            if not silent:
                self.status_var.set("Список служб обновлен")
                self._emit_event(EventLevel.INFO, "Информация", "Список служб обновлен")
        except Exception as exc:
            self._handle_error("Ошибка обновления служб", str(exc))

    def action_refresh_drivers(self, silent: bool = False) -> None:
        if self._warmup_is_running("Обновление драйверов"):
            return
        try:
            drivers = self._scan_service.scan_drivers_only()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "driver_records": drivers,
                    "process_records": self._all_processes,
                    "service_records": self._all_services,
                    "startup_entries": self._all_startup,
                },
            )
            self._all_drivers = list(drivers)
            self._fill_drivers(snapshot)
            if not silent:
                self.status_var.set("Список драйверов обновлен")
                self._emit_event(EventLevel.INFO, "Информация", "Список драйверов обновлен")
        except Exception as exc:
            self._handle_error("Ошибка обновления драйверов", str(exc))

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
        if rec is None:
            return
        path = rec.executable_path
        if not path and rec.name:
            path = self._resolve_service_executable(rec.name)
            if path:
                rec.executable_path = path
        if path:
            self._show_action_result(self._action_service.open_in_explorer(path))
            return
        self.status_var.set("Путь службы не найден")

    def action_open_driver_path(self) -> None:
        rec = self._selected_driver()
        if rec and rec.executable_path:
            self._show_action_result(self._action_service.open_in_explorer(rec.executable_path))

    def _resolve_service_executable(self, service_name: str) -> str:
        script = (
            "$name=$args[0];"
            "$svc = Get-CimInstance Win32_Service -Filter \"Name='$name'\" -ErrorAction SilentlyContinue;"
            "if ($svc) { [string]$svc.PathName }"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, service_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=3500,
            )
        except Exception:
            return ""
        if proc.returncode != 0:
            return ""
        raw = (proc.stdout or "").strip()
        if not raw:
            return ""
        if raw.startswith('"'):
            end = raw.find('"', 1)
            if end > 1:
                raw = raw[1:end]
        else:
            raw = raw.split(" ")[0]
        if raw.startswith(r"\SystemRoot\\"):
            raw = raw.replace(r"\SystemRoot", r"C:\Windows", 1)
        return raw

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
        if not self._settings.exe_build_enabled:
            messagebox.showwarning("EXE", "Сборка EXE отключена в настройках")
            return
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
                    build_path = Path(__file__).resolve().parents[2] / msg
                    folder = build_path.parent if build_path.suffix else build_path
                    if folder.exists():
                        self._action_service.open_in_explorer(str(folder))
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

    def _open_driver_menu(self, event) -> None:
        self._menu_target(event, self.driver_tree)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Открыть в Проводнике", command=self.action_open_driver_path)
        m.add_separator()
        m.add_command(label="Искать в интернете", command=self.action_search_driver_online)
        m.add_command(label="Копировать запись", command=lambda: self._copy_tree_row(self.driver_tree))
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
