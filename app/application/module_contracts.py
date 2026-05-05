from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ModuleSettingsSchema:
    enabled_by_default: bool
    defaults: dict[str, Any] = field(default_factory=dict)


class AnalyzerModule(Protocol):
    module_id: str
    module_name: str

    def settings_schema(self) -> ModuleSettingsSchema:
        ...

    def run(self, runtime_settings: dict[str, Any]) -> dict[str, Any]:
        ...
