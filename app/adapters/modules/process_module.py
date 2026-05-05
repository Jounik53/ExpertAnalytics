from __future__ import annotations

from dataclasses import dataclass

from app.application.module_contracts import AnalyzerModule, ModuleSettingsSchema
from app.domain.entities import ProcessRecord
from app.domain.ports import ProcessScannerPort


@dataclass(slots=True)
class ProcessModule(AnalyzerModule):
    scanner: ProcessScannerPort
    module_id: str = "processes"
    module_name: str = "Process Scanner"

    def settings_schema(self) -> ModuleSettingsSchema:
        return ModuleSettingsSchema(
            enabled_by_default=True,
            defaults={"high_mb": 900, "medium_mb": 300},
        )

    def run(self, runtime_settings: dict[str, int]) -> dict[str, list[ProcessRecord]]:
        high_mb = int(runtime_settings.get("high_mb", 900))
        medium_mb = int(runtime_settings.get("medium_mb", 300))
        records = self.scanner.scan_processes(high_mb=high_mb, medium_mb=medium_mb)
        return {"process_records": records}
