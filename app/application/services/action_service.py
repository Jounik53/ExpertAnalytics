from __future__ import annotations

from datetime import datetime

from app.domain.entities import ActionResult, ServiceRecord, StartupEntry
from app.domain.ports import RemediationRepositoryPort, SystemActionPort


class ActionService:
    def __init__(self, actions: SystemActionPort, remediation_repo: RemediationRepositoryPort | None = None) -> None:
        self._actions = actions
        self._repo = remediation_repo
        self._dry_run = False

    def set_dry_run(self, enabled: bool) -> None:
        self._dry_run = enabled
        if hasattr(self._actions, "dry_run"):
            self._actions.dry_run = enabled

    def terminate_process(self, pid: int) -> ActionResult:
        result = self._actions.terminate_process(pid)
        self._audit("terminate_process", str(pid), result)
        return result

    def delete_executable(self, path: str) -> ActionResult:
        result = self._actions.delete_executable(path)
        self._audit("delete_executable", path, result)
        return result

    def quarantine_file(self, path: str) -> ActionResult:
        result = self._actions.quarantine_file(path)
        self._audit("quarantine_file", path, result)
        return result

    def open_in_explorer(self, path: str) -> ActionResult:
        result = self._actions.open_in_explorer(path)
        self._audit("open_in_explorer", path, result)
        return result

    def disable_startup(self, entry: StartupEntry) -> ActionResult:
        result = self._actions.disable_startup(entry)
        self._audit("disable_startup", entry.name, result)
        return result

    def remove_startup(self, entry: StartupEntry) -> ActionResult:
        result = self._actions.remove_startup(entry)
        self._audit("remove_startup", entry.name, result)
        return result

    def stop_service(self, service: ServiceRecord) -> ActionResult:
        result = self._actions.stop_service(service.name)
        self._audit("stop_service", service.name, result)
        return result

    def disable_service(self, service: ServiceRecord) -> ActionResult:
        result = self._actions.disable_service(service.name)
        self._audit("disable_service", service.name, result)
        return result

    def uninstall_by_executable(self, executable_path: str) -> ActionResult:
        result = self._actions.uninstall_by_executable(executable_path)
        self._audit("uninstall_by_executable", executable_path, result)
        return result

    def _audit(self, action: str, target: str, result: ActionResult) -> None:
        if self._repo is None:
            return
        from app.domain.entities import RemediationRecord

        self._repo.append_record(
            RemediationRecord(
                timestamp=datetime.now().isoformat(),
                action=action,
                target=target,
                dry_run=self._dry_run,
                success=result.ok,
                details={"message": result.message, **result.details},
            )
        )

    def remediation_history(self):
        if self._repo is None:
            return []
        return self._repo.list_records()
