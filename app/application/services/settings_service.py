from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.ports import SettingsRepositoryPort


@dataclass(slots=True)
class AppSettings:
    vt_api_key: str = ""
    process_high_mb: int = 900
    process_medium_mb: int = 300
    enable_process_module: bool = True
    enable_startup_module: bool = True
    enable_services_module: bool = True
    enable_vt_lookup: bool = False
    sampling_enabled: bool = False
    sampling_points: int = 5
    sampling_interval_sec: int = 3
    dry_run_actions: bool = True
    locale_code: str = "ru"
    cpu_limit_percent: int = 60
    heuristics_enabled: bool = True
    heuristic_rules: dict[str, bool] | None = None


class SettingsService:
    def __init__(self, repo: SettingsRepositoryPort) -> None:
        self._repo = repo

    def load(self) -> AppSettings:
        raw = self._repo.load()
        return AppSettings(
            vt_api_key=str(raw.get("vt_api_key", "")),
            process_high_mb=int(raw.get("process_high_mb", 900)),
            process_medium_mb=int(raw.get("process_medium_mb", 300)),
            enable_process_module=bool(raw.get("enable_process_module", True)),
            enable_startup_module=bool(raw.get("enable_startup_module", True)),
            enable_services_module=bool(raw.get("enable_services_module", True)),
            enable_vt_lookup=bool(raw.get("enable_vt_lookup", False)),
            sampling_enabled=bool(raw.get("sampling_enabled", False)),
            sampling_points=int(raw.get("sampling_points", 5)),
            sampling_interval_sec=int(raw.get("sampling_interval_sec", 3)),
            dry_run_actions=bool(raw.get("dry_run_actions", True)),
            locale_code=str(raw.get("locale_code", "ru")),
            cpu_limit_percent=int(raw.get("cpu_limit_percent", 60)),
            heuristics_enabled=bool(raw.get("heuristics_enabled", True)),
            heuristic_rules=dict(raw.get("heuristic_rules", {})) if isinstance(raw.get("heuristic_rules", {}), dict) else {},
        )

    def save(self, settings: AppSettings) -> None:
        self._repo.save(
            {
                "vt_api_key": settings.vt_api_key,
                "process_high_mb": settings.process_high_mb,
                "process_medium_mb": settings.process_medium_mb,
                "enable_process_module": settings.enable_process_module,
                "enable_startup_module": settings.enable_startup_module,
                "enable_services_module": settings.enable_services_module,
                "enable_vt_lookup": settings.enable_vt_lookup,
                "sampling_enabled": settings.sampling_enabled,
                "sampling_points": settings.sampling_points,
                "sampling_interval_sec": settings.sampling_interval_sec,
                "dry_run_actions": settings.dry_run_actions,
                "locale_code": settings.locale_code,
                "cpu_limit_percent": settings.cpu_limit_percent,
                "heuristics_enabled": settings.heuristics_enabled,
                "heuristic_rules": settings.heuristic_rules or {},
            }
        )
