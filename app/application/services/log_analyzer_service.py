from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class LogAnalysis:
    total_lines: int
    errors: int
    warnings: int
    critical: int


class LogAnalyzerService:
    def analyze_log(self, log_path: Path) -> LogAnalysis:
        if not log_path.exists():
            return LogAnalysis(total_lines=0, errors=0, warnings=0, critical=0)
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        return LogAnalysis(
            total_lines=len(lines),
            errors=sum(1 for x in lines if "ERROR" in x),
            warnings=sum(1 for x in lines if "WARNING" in x),
            critical=sum(1 for x in lines if "CRITICAL" in x),
        )

    def summarize_report(self, payload: dict) -> str:
        created = payload.get("created_at", "")
        used = payload.get("used_ram_gb", 0)
        total = payload.get("total_ram_gb", 0)
        procs = len(payload.get("process_records", []))
        startup = len(payload.get("startup_entries", []))
        services = len(payload.get("service_records", []))
        return (
            f"created_at={created}\n"
            f"ram={used}/{total} GB\n"
            f"processes={procs}, startup={startup}, services={services}\n"
        )
