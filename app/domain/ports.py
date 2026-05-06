from __future__ import annotations

from typing import Any, Protocol

from app.domain.entities import (
    ActionResult,
    DiagnosticsSnapshot,
    ProcessRecord,
    RemediationRecord,
    SamplingPoint,
    ScanSnapshot,
    ServiceRecord,
    DriverRecord,
    StartupEntry,
    VirusTotalResult,
)


class ProcessScannerPort(Protocol):
    def scan_processes(self, high_mb: int, medium_mb: int, item_callback=None, record_callback=None) -> list[ProcessRecord]:
        ...

    def estimate_process_count(self) -> int:
        ...

    def memory_totals(self) -> tuple[float, float]:
        ...


class StartupScannerPort(Protocol):
    def scan_startup_entries(self, item_callback=None) -> list[StartupEntry]:
        ...

    def estimate_startup_count(self) -> int:
        ...


class ServiceScannerPort(Protocol):
    def scan_services(self, item_callback=None) -> list[ServiceRecord]:
        ...

    def estimate_service_count(self) -> int:
        ...


class DriverScannerPort(Protocol):
    def scan_drivers(self, item_callback=None) -> list[DriverRecord]:
        ...

    def estimate_driver_count(self) -> int:
        ...


class DiagnosticsPort(Protocol):
    def collect(self, used_ram_gb: float, process_records: list[ProcessRecord]) -> DiagnosticsSnapshot:
        ...


class SamplerPort(Protocol):
    def collect_points(
        self,
        interval_sec: int,
        points: int,
        high_mb: int,
        medium_mb: int,
        cancel_check,
    ) -> list[SamplingPoint]:
        ...


class VirusTotalPort(Protocol):
    def lookup_by_sha256(self, sha256: str) -> VirusTotalResult:
        ...


class SettingsRepositoryPort(Protocol):
    def load(self) -> dict[str, Any]:
        ...

    def save(self, payload: dict[str, Any]) -> None:
        ...


class ReportRepositoryPort(Protocol):
    def save_snapshot(self, snapshot: ScanSnapshot) -> str:
        ...

    def list_reports(self) -> list[str]:
        ...

    def read_report(self, path: str) -> dict[str, Any]:
        ...


class SystemActionPort(Protocol):
    def terminate_process(self, pid: int) -> ActionResult:
        ...

    def delete_executable(self, path: str) -> ActionResult:
        ...

    def disable_startup(self, entry: StartupEntry) -> ActionResult:
        ...

    def remove_startup(self, entry: StartupEntry) -> ActionResult:
        ...

    def stop_service(self, service_name: str) -> ActionResult:
        ...

    def disable_service(self, service_name: str) -> ActionResult:
        ...

    def uninstall_by_executable(self, executable_path: str) -> ActionResult:
        ...

    def quarantine_file(self, path: str) -> ActionResult:
        ...

    def open_in_explorer(self, path: str) -> ActionResult:
        ...


class RemediationRepositoryPort(Protocol):
    def append_record(self, record: RemediationRecord) -> None:
        ...

    def list_records(self) -> list[RemediationRecord]:
        ...
