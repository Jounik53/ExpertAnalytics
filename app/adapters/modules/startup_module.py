from __future__ import annotations

from dataclasses import dataclass

from app.application.module_contracts import AnalyzerModule, ModuleSettingsSchema
from app.domain.entities import StartupEntry
from app.domain.ports import StartupScannerPort


@dataclass(slots=True)
class StartupModule(AnalyzerModule):
    scanner: StartupScannerPort
    module_id: str = "startup"
    module_name: str = "Startup Scanner"

    def settings_schema(self) -> ModuleSettingsSchema:
        return ModuleSettingsSchema(enabled_by_default=True, defaults={})

    def run(self, runtime_settings: dict[str, int]) -> dict[str, list[StartupEntry]]:
        return {"startup_entries": self.scanner.scan_startup_entries()}
