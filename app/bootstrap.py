from __future__ import annotations

import os
from pathlib import Path

from app.adapters.persistence.report_repo import JsonReportRepository
from app.adapters.persistence.remediation_repo import JsonRemediationRepository
from app.adapters.persistence.settings_repo import JsonSettingsRepository
from app.adapters.modules.loader import build_default_registry
from app.adapters.system.actions import WindowsSystemActions
from app.adapters.system.process_scanner import PsutilProcessScanner
from app.adapters.system.sampler import WindowsSampler
from app.adapters.system.driver_scanner import WindowsDriverScanner
from app.adapters.system.service_scanner import PsutilServiceScanner
from app.adapters.system.startup_scanner import RegistryStartupScanner
from app.adapters.system.diagnostics import WindowsDiagnosticsCollector
from app.adapters.vt.http_client import VirusTotalHttpClient
from app.adapters.vt.noop_client import NoopVirusTotalClient
from app.application.logging_setup import configure_logging
from app.application.services.action_service import ActionService
from app.application.services.scan_service import ScanService
from app.application.services.settings_service import SettingsService
from app.application.services.report_insight_service import ReportInsightService
from app.application.services.log_analyzer_service import LogAnalyzerService
from app.ui.i18n import I18n
from app.ui.event_store import UIEventStore
from app.ui.main_window import MainWindow


def build_app() -> MainWindow:
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"
    configure_logging(data_dir / "expert_analytics.log")

    settings_repo = JsonSettingsRepository(data_dir / "settings.json")
    report_repo = JsonReportRepository(base_dir / "reports")
    remediation_repo = JsonRemediationRepository(data_dir / "remediation_log.json")
    registry = build_default_registry()

    settings_service = SettingsService(settings_repo)
    settings = settings_service.load()

    effective_vt_key = settings.vt_api_key or os.getenv("VIRUSTOTAL_API_KEY", "")
    vt_client = (
        VirusTotalHttpClient(effective_vt_key)
        if settings.enable_vt_lookup and effective_vt_key
        else NoopVirusTotalClient()
    )

    scan_service = ScanService(
        process_scanner=PsutilProcessScanner(),
        startup_scanner=RegistryStartupScanner(),
        service_scanner=PsutilServiceScanner(),
        driver_scanner=WindowsDriverScanner(),
        vt=vt_client,
        reports=report_repo,
        diagnostics=WindowsDiagnosticsCollector(),
        sampler=WindowsSampler(PsutilProcessScanner()),
    )
    action_service = ActionService(WindowsSystemActions(), remediation_repo=remediation_repo)

    effective_locale = settings.locale_code or os.getenv("DEFAULT_LOCALE", "ru")

    return MainWindow(
        scan_service=scan_service,
        settings_service=settings_service,
        action_service=action_service,
        reports_repo=report_repo,
        module_registry=registry,
        i18n=I18n(effective_locale),
        report_insight_service=ReportInsightService(),
        log_analyzer_service=LogAnalyzerService(),
        event_store=UIEventStore(data_dir / "ui_events.json"),
    )


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
    _load_env_file(base_dir.parent / ".env")
