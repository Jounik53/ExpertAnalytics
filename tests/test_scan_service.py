from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.application.services.scan_service import ScanService
from app.domain.entities import MemoryLevel, ProcessRecord, ScanSnapshot, ServiceRecord, StartupEntry, VirusTotalResult


class DummyProcessScanner:
    def scan_processes(self, high_mb: int, medium_mb: int):
        return [
            ProcessRecord(
                pid=10,
                name="example.exe",
                exe_path="C:\\Temp\\example.exe",
                username="user",
                memory_mb=1200.0,
                memory_level=MemoryLevel.HIGH,
                cpu_percent=1.0,
                create_time=datetime.now().isoformat(),
                is_system=False,
                command_line="example",
                file_sha256="abc",
            )
        ]

    def memory_totals(self):
        return 64.0, 40.0


class DummyStartupScanner:
    def scan_startup_entries(self):
        return [StartupEntry(name="test", command="test.exe", location="HKCU", enabled=True)]


class DummyServiceScanner:
    def scan_services(self):
        return [
            ServiceRecord(
                name="svc",
                display_name="Service",
                status="running",
                start_type="auto",
                executable_path="C:\\svc.exe",
                pid=11,
            )
        ]


class DummyVt:
    def lookup_by_sha256(self, sha256: str):
        return VirusTotalResult(available=True, malicious=1, suspicious=0, harmless=20, undetected=5)


class DummyReportRepo:
    def save_snapshot(self, snapshot: ScanSnapshot) -> str:
        return "reports/report.json"

    def list_reports(self) -> list[str]:
        return ["reports/report.json"]

    def read_report(self, path: str) -> dict:
        return {"path": path}


def test_full_scan_attaches_vt_and_report_path():
    service = ScanService(
        process_scanner=DummyProcessScanner(),
        startup_scanner=DummyStartupScanner(),
        service_scanner=DummyServiceScanner(),
        vt=DummyVt(),
        reports=DummyReportRepo(),
    )

    snapshot, report_path = service.full_scan(high_mb=900, medium_mb=300, with_vt=True)

    assert report_path.endswith("report.json")
    assert snapshot.process_records[0].vt_result is not None
    assert snapshot.process_records[0].vt_result.malicious == 1
    assert snapshot.used_ram_gb == 40.0


def test_full_scan_can_disable_modules():
    service = ScanService(
        process_scanner=DummyProcessScanner(),
        startup_scanner=DummyStartupScanner(),
        service_scanner=DummyServiceScanner(),
        vt=DummyVt(),
        reports=DummyReportRepo(),
    )

    snapshot, _ = service.full_scan(
        high_mb=900,
        medium_mb=300,
        with_vt=False,
        enable_process_module=False,
        enable_startup_module=True,
        enable_services_module=False,
    )

    assert snapshot.process_records == []
    assert len(snapshot.startup_entries) == 1
    assert snapshot.service_records == []
