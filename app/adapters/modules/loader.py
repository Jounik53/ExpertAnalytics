from __future__ import annotations

from app.adapters.modules.process_module import ProcessModule
from app.adapters.modules.services_module import ServicesModule
from app.adapters.modules.startup_module import StartupModule
from app.adapters.system.process_scanner import PsutilProcessScanner
from app.adapters.system.service_scanner import PsutilServiceScanner
from app.adapters.system.startup_scanner import RegistryStartupScanner
from app.application.module_registry import ModuleRegistry


def build_default_registry() -> ModuleRegistry:
    registry = ModuleRegistry()
    registry.register(ProcessModule(scanner=PsutilProcessScanner()))
    registry.register(StartupModule(scanner=RegistryStartupScanner()))
    registry.register(ServicesModule(scanner=PsutilServiceScanner()))
    return registry
