from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MemoryLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(slots=True)
class VirusTotalResult:
    available: bool
    malicious: int = 0
    suspicious: int = 0
    harmless: int = 0
    undetected: int = 0
    permalink: str | None = None
    summary: str | None = None


@dataclass(slots=True)
class ProcessRecord:
    pid: int
    name: str
    exe_path: str
    username: str
    memory_mb: float
    memory_level: MemoryLevel
    cpu_percent: float
    create_time: str
    is_system: bool
    command_line: str
    file_sha256: str | None = None
    vt_result: VirusTotalResult | None = None
    heuristic_score: int = 0
    heuristic_hits: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StartupEntry:
    name: str
    command: str
    location: str
    enabled: bool
    category: str = "Application"


@dataclass(slots=True)
class ServiceRecord:
    name: str
    display_name: str
    status: str
    start_type: str
    executable_path: str
    pid: int | None
    signer: str = "unknown"
    file_version: str = ""
    file_date: str = ""
    file_sha256: str = ""
    trusted: bool = False
    trust_reason: str = ""
    risk_score: int = 0
    risk_reason: str = ""


@dataclass(slots=True)
class DriverRecord:
    name: str
    display_name: str
    state: str
    start_mode: str
    executable_path: str
    file_sha256: str = ""
    image_size_mb: float = 0.0
    risk_score: int = 0
    risk_reason: str = ""
    resource_score: int = 0
    resource_reason: str = ""


@dataclass(slots=True)
class DiagnosticsSnapshot:
    commit_gb: float
    commit_limit_gb: float
    memory_compression_gb: float
    paged_pool_gb: float
    nonpaged_pool_gb: float
    standby_cache_gb: float
    unattributed_gb: float
    kernel_leak_risk: str
    recommendations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SamplingPoint:
    timestamp: str
    used_ram_gb: float
    commit_gb: float
    paged_pool_gb: float
    nonpaged_pool_gb: float
    top_processes: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ScanSnapshot:
    created_at: datetime
    total_ram_gb: float
    used_ram_gb: float
    process_records: list[ProcessRecord] = field(default_factory=list)
    startup_entries: list[StartupEntry] = field(default_factory=list)
    service_records: list[ServiceRecord] = field(default_factory=list)
    driver_records: list[DriverRecord] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    diagnostics_snapshot: DiagnosticsSnapshot | None = None
    sampling_points: list[SamplingPoint] = field(default_factory=list)


@dataclass(slots=True)
class ActionResult:
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RemediationRecord:
    timestamp: str
    action: str
    target: str
    dry_run: bool
    success: bool
    details: dict[str, Any] = field(default_factory=dict)
