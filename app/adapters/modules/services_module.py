from __future__ import annotations

from dataclasses import dataclass

from app.application.module_contracts import AnalyzerModule, ModuleSettingsSchema
from app.domain.entities import ServiceRecord
from app.domain.ports import ServiceScannerPort


@dataclass(slots=True)
class ServicesModule(AnalyzerModule):
    scanner: ServiceScannerPort
    module_id: str = "services"
    module_name: str = "Services Scanner"

    def settings_schema(self) -> ModuleSettingsSchema:
        return ModuleSettingsSchema(enabled_by_default=True, defaults={})

    def run(self, runtime_settings: dict[str, int]) -> dict[str, list[ServiceRecord]]:
        return {"service_records": self.scanner.scan_services()}
