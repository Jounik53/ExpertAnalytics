from __future__ import annotations

from dataclasses import dataclass

from app.application.module_contracts import AnalyzerModule, ModuleSettingsSchema
from app.domain.entities import DriverRecord
from app.domain.ports import DriverScannerPort


@dataclass(slots=True)
class DriversModule(AnalyzerModule):
    scanner: DriverScannerPort
    module_id: str = "drivers"
    module_name: str = "Drivers Scanner"

    def settings_schema(self) -> ModuleSettingsSchema:
        return ModuleSettingsSchema(enabled_by_default=True, defaults={})

    def run(self, runtime_settings: dict[str, int]) -> dict[str, list[DriverRecord]]:
        return {"driver_records": self.scanner.scan_drivers()}
