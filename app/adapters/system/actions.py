from __future__ import annotations

import os
import shutil
import subprocess
import ctypes
from pathlib import Path

import psutil
import winreg

from app.domain.entities import ActionResult, StartupEntry
from app.domain.ports import SystemActionPort


class WindowsSystemActions(SystemActionPort):
    def __init__(self) -> None:
        self.dry_run = False

    def terminate_process(self, pid: int) -> ActionResult:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            return ActionResult(ok=True, message=f"Процесс {pid} завершен")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            return ActionResult(ok=False, message=f"Не удалось завершить процесс {pid}: {exc}")

    def delete_executable(self, path: str) -> ActionResult:
        if self.dry_run:
            return ActionResult(ok=True, message=f"[dry-run] удалить {path}")
        target = Path(path)
        if not target.exists():
            return ActionResult(ok=False, message="Исполняемый файл не найден")
        try:
            target.unlink(missing_ok=False)
            return ActionResult(ok=True, message=f"Удалено: {target}")
        except OSError as exc:
            return ActionResult(ok=False, message=f"Ошибка удаления: {exc}")

    def disable_startup(self, entry: StartupEntry) -> ActionResult:
        if self.dry_run:
            return ActionResult(ok=True, message=f"[dry-run] отключить автозапуск {entry.name}")
        return self._set_startup(entry, enabled=False)

    def remove_startup(self, entry: StartupEntry) -> ActionResult:
        if self.dry_run:
            return ActionResult(ok=True, message=f"[dry-run] удалить из автозапуска {entry.name}")
        try:
            if entry.location.startswith("StartupFolder:"):
                return self._remove_startup_file(entry)
            if entry.location == "ScheduledTask":
                return self._remove_scheduled_task(entry)
            if entry.location.startswith("WMIBinding:"):
                return self._remove_wmi_binding(entry)

            hive, key_path = self._resolve_run_hive(entry.location)
            if hive is None:
                return ActionResult(ok=False, message="Неподдерживаемое расположение автозапуска")
            key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE)
            with key:
                normalized_name = (entry.name or "").strip().lower()
                lowered_key = key_path.lower()
                if lowered_key.endswith(r"\winlogon") and normalized_name == "shell":
                    winreg.SetValueEx(key, entry.name, 0, winreg.REG_SZ, "explorer.exe")
                elif lowered_key.endswith(r"\windows") and normalized_name in {"load", "run"}:
                    winreg.SetValueEx(key, entry.name, 0, winreg.REG_SZ, "")
                else:
                    winreg.DeleteValue(key, entry.name)
            return ActionResult(ok=True, message=f"Запись автозапуска '{entry.name}' удалена")
        except PermissionError:
            return ActionResult(ok=False, message="Недостаточно прав для удаления автозапуска. Запустите приложение от имени администратора")
        except OSError as exc:
            return ActionResult(ok=False, message=f"Ошибка удаления автозапуска: {exc}")

    def stop_service(self, service_name: str) -> ActionResult:
        if self.dry_run:
            return ActionResult(ok=True, message=f"[dry-run] остановить службу {service_name}")
        result = subprocess.run(["sc", "stop", service_name], capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
        if result.returncode == 0:
            return ActionResult(ok=True, message=f"Служба {service_name} остановлена")
        ps_cmd = f"Stop-Service -Name '{service_name}' -ErrorAction Stop"
        ps = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
        if ps.returncode == 0:
            return ActionResult(ok=True, message=f"Служба {service_name} остановлена")
        return ActionResult(ok=False, message=f"Ошибка остановки службы: {result.stdout} {result.stderr} {ps.stdout} {ps.stderr}")

    def disable_service(self, service_name: str) -> ActionResult:
        if self.dry_run:
            return ActionResult(ok=True, message=f"[dry-run] отключить службу {service_name}")
        result = subprocess.run(
            ["sc", "config", service_name, "start=", "disabled"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if result.returncode == 0:
            return ActionResult(ok=True, message=f"Служба {service_name} отключена")
        ps_cmd = f"Set-Service -Name '{service_name}' -StartupType Disabled -ErrorAction Stop"
        ps = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
        if ps.returncode == 0:
            return ActionResult(ok=True, message=f"Служба {service_name} отключена")
        return ActionResult(ok=False, message=f"Ошибка отключения службы: {result.stdout} {result.stderr} {ps.stdout} {ps.stderr}")

    def uninstall_by_executable(self, executable_path: str) -> ActionResult:
        if self.dry_run:
            return ActionResult(ok=True, message=f"[dry-run] деинсталляция по {executable_path}")
        uninstall_cmd = self._find_uninstall_string(executable_path)
        if uninstall_cmd:
            cmd = uninstall_cmd.strip()
            try:
                completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=True)
                if completed.returncode == 0:
                    return ActionResult(ok=True, message="Деинсталлятор выполнен успешно")
                return ActionResult(ok=False, message=f"Ошибка деинсталлятора: {completed.stdout} {completed.stderr}")
            except OSError as exc:
                return ActionResult(ok=False, message=f"Не удалось запустить деинсталлятор: {exc}")

        path = Path(executable_path)
        if not path.exists():
            return ActionResult(ok=False, message="Исполняемый файл не найден для резервного удаления")

        removed = []
        errors = []
        try:
            if path.is_file():
                path.unlink(missing_ok=False)
                removed.append(str(path))
            parent = path.parent
            if parent.exists() and parent.name.lower() not in {"windows", "system32"}:
                shutil.rmtree(parent, ignore_errors=False)
                removed.append(str(parent))
        except OSError as exc:
            errors.append(str(exc))

        if errors:
            return ActionResult(ok=False, message="Резервное удаление выполнено частично", details={"errors": errors, "removed": removed})
        return ActionResult(ok=True, message="Резервное удаление выполнено", details={"removed": removed})

    def quarantine_file(self, path: str) -> ActionResult:
        if self.dry_run:
            return ActionResult(ok=True, message=f"[dry-run] карантин {path}")
        src = Path(path)
        if not src.exists():
            return ActionResult(ok=False, message="Файл не найден")
        qdir = Path("quarantine")
        qdir.mkdir(parents=True, exist_ok=True)
        dst = qdir / src.name
        try:
            src.replace(dst)
            return ActionResult(ok=True, message=f"Помещено в карантин: {dst}")
        except OSError as exc:
            return ActionResult(ok=False, message=f"Ошибка карантина: {exc}")

    def open_in_explorer(self, path: str) -> ActionResult:
        target = Path(path)
        if not target.exists():
            return ActionResult(ok=False, message="Файл или папка не найдены")
        try:
            if target.is_file():
                subprocess.run(["explorer", "/select,", str(target)], check=False, shell=False)
            else:
                subprocess.run(["explorer", str(target)], check=False, shell=False)
            return ActionResult(ok=True, message=f"Открыто в Проводнике: {target}")
        except OSError as exc:
            return ActionResult(ok=False, message=f"Не удалось открыть в Проводнике: {exc}")

    def _set_startup(self, entry: StartupEntry, enabled: bool) -> ActionResult:
        try:
            if entry.location.startswith("StartupFolder:"):
                return self._toggle_startup_file(entry, enabled=enabled)
            if entry.location == "ScheduledTask":
                return self._toggle_scheduled_task(entry, enabled=enabled)
            if entry.location.startswith("WMIBinding:"):
                if enabled:
                    return ActionResult(ok=False, message="Включение WMI-подписок не поддерживается. Используйте ручное восстановление")
                return self._remove_wmi_binding(entry)

            hive, key_path = self._resolve_run_hive(entry.location)
            if hive is None:
                return ActionResult(ok=False, message="Неподдерживаемое расположение автозапуска")
            key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE)
            with key:
                normalized_name = (entry.name or "").strip().lower()
                lowered_key = key_path.lower()
                if lowered_key.endswith(r"\winlogon") and normalized_name == "shell":
                    value = entry.command if enabled else "explorer.exe"
                elif lowered_key.endswith(r"\windows") and normalized_name in {"load", "run"}:
                    value = entry.command if enabled else ""
                else:
                    value = entry.command if enabled else f"REM_DISABLED::{entry.command}"
                winreg.SetValueEx(key, entry.name, 0, winreg.REG_SZ, value)
            state = "включена" if enabled else "отключена"
            return ActionResult(ok=True, message=f"Запись автозапуска '{entry.name}' {state}")
        except PermissionError:
            return ActionResult(ok=False, message="Недостаточно прав для изменения автозапуска. Запустите приложение от имени администратора")
        except OSError as exc:
            return ActionResult(ok=False, message=f"Не удалось обновить запись автозапуска: {exc}")

    @staticmethod
    def _resolve_run_hive(location: str):
        if location.startswith("HKCU\\"):
            return winreg.HKEY_CURRENT_USER, location[len("HKCU\\"):]
        if location.startswith("HKLM\\"):
            return winreg.HKEY_LOCAL_MACHINE, location[len("HKLM\\"):]
        return None, ""

    def _toggle_scheduled_task(self, entry: StartupEntry, enabled: bool) -> ActionResult:
        cmd = ["schtasks", "/Change", "/TN", entry.name, "/ENABLE" if enabled else "/DISABLE"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
        if result.returncode == 0:
            state = "включена" if enabled else "отключена"
            return ActionResult(ok=True, message=f"Задача автозапуска '{entry.name}' {state}")
        return ActionResult(ok=False, message=f"Ошибка изменения задачи: {result.stdout} {result.stderr}")

    def _remove_scheduled_task(self, entry: StartupEntry) -> ActionResult:
        result = subprocess.run(["schtasks", "/Delete", "/TN", entry.name, "/F"], capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
        if result.returncode == 0:
            return ActionResult(ok=True, message=f"Задача автозапуска '{entry.name}' удалена")
        return ActionResult(ok=False, message=f"Ошибка удаления задачи: {result.stdout} {result.stderr}")

    def _remove_wmi_binding(self, entry: StartupEntry) -> ActionResult:
        binding_path = entry.location.split(":", 1)[1].strip() if ":" in entry.location else ""
        if not binding_path:
            return ActionResult(ok=False, message="Не удалось определить WMI binding")
        script = (
            "$path = $args[0];"
            "$binding = Get-CimInstance -Namespace root/subscription -ClassName __FilterToConsumerBinding -ErrorAction SilentlyContinue | "
            "Where-Object { [string]$_.__RELPATH -eq $path } | Select-Object -First 1;"
            "if (-not $binding) { exit 2 };"
            "Remove-CimInstance -InputObject $binding -ErrorAction Stop"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, binding_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if result.returncode == 0:
            return ActionResult(ok=True, message=f"WMI автозагрузка '{entry.name}' удалена")
        if result.returncode == 2:
            return ActionResult(ok=True, message=f"WMI автозагрузка '{entry.name}' уже отсутствует")
        return ActionResult(ok=False, message=f"Ошибка удаления WMI автозагрузки: {result.stdout} {result.stderr}")

    @staticmethod
    def _extract_file_path(command: str) -> Path:
        cmd = (command or "").strip().strip('"')
        if not cmd:
            return Path("")
        direct = Path(cmd)
        if direct.exists():
            return direct
        first = cmd.split(" ")[0].strip('"')
        return Path(first)

    @staticmethod
    def _startup_file_target(entry: StartupEntry) -> Path:
        if entry.location.startswith("StartupFolder:"):
            folder = entry.location.split(":", 1)[1].strip()
            if folder:
                by_location = Path(folder) / entry.name
                if by_location.exists():
                    return by_location
        return WindowsSystemActions._extract_file_path(entry.command)

    def _toggle_startup_file(self, entry: StartupEntry, enabled: bool) -> ActionResult:
        target = self._startup_file_target(entry)
        if not target.exists():
            return ActionResult(ok=False, message="Файл автозагрузки не найден")
        try:
            if enabled and target.suffix.lower() == ".disabled":
                target.rename(target.with_suffix(""))
            elif not enabled and target.suffix.lower() != ".disabled":
                target.rename(target.with_name(target.name + ".disabled"))
            state = "включена" if enabled else "отключена"
            return ActionResult(ok=True, message=f"Запись автозагрузки '{entry.name}' {state}")
        except OSError as exc:
            return ActionResult(ok=False, message=f"Ошибка изменения файла автозагрузки: {exc}")

    def _remove_startup_file(self, entry: StartupEntry) -> ActionResult:
        target = self._startup_file_target(entry)
        if not target.exists():
            return ActionResult(ok=False, message="Файл автозагрузки не найден")
        try:
            target.unlink()
            return ActionResult(ok=True, message=f"Файл автозагрузки '{entry.name}' удален")
        except OSError as exc:
            return ActionResult(ok=False, message=f"Ошибка удаления файла автозагрузки: {exc}")

    @staticmethod
    def is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def _find_uninstall_string(executable_path: str) -> str | None:
        base_keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        target = executable_path.lower()
        for hive, root in base_keys:
            try:
                key = winreg.OpenKey(hive, root, 0, winreg.KEY_READ)
            except OSError:
                continue
            with key:
                idx = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, idx)
                        idx += 1
                        subkey = winreg.OpenKey(hive, root + "\\" + subkey_name, 0, winreg.KEY_READ)
                        with subkey:
                            install_location = _safe_query(subkey, "InstallLocation")
                            uninstall_string = _safe_query(subkey, "UninstallString")
                            display_icon = _safe_query(subkey, "DisplayIcon")
                            concat = " ".join(x.lower() for x in [install_location, display_icon] if x)
                            if target and target in concat:
                                return uninstall_string
                    except OSError:
                        break
        return None


def _safe_query(key, value_name: str) -> str:
    try:
        value, _ = winreg.QueryValueEx(key, value_name)
        return str(value)
    except OSError:
        return ""
