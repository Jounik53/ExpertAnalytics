from __future__ import annotations

from dataclasses import dataclass

from app.application.module_contracts import AnalyzerModule


@dataclass(slots=True)
class ModuleRegistry:
    _modules: dict[str, AnalyzerModule]

    def __init__(self) -> None:
        self._modules = {}

    def register(self, module: AnalyzerModule) -> None:
        self._modules[module.module_id] = module

    def list_modules(self) -> list[AnalyzerModule]:
        return list(self._modules.values())

    def schema_map(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for module in self._modules.values():
            schema = module.settings_schema()
            result[module.module_id] = {
                "module_name": module.module_name,
                "enabled_by_default": schema.enabled_by_default,
                "defaults": schema.defaults,
            }
        return result
