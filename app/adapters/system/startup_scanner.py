from __future__ import annotations

import json
import os
import subprocess
import winreg
from pathlib import Path
from typing import Any

from app.domain.entities import StartupEntry
from app.domain.ports import StartupScannerPort


_RUN_KEY_SPECS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU", None),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKCU", None),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run", "HKCU", None),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Windows", "HKCU", ("Load", "Run")),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon", "HKCU", ("Shell",)),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM", None),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM", None),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run", "HKLM", None),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows NT\CurrentVersion\Windows", "HKLM", ("Load", "Run")),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon", "HKLM", ("Shell",)),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM", None),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM", None),
]


class RegistryStartupScanner(StartupScannerPort):
    def estimate_startup_count(self) -> int:
        try:
            return len(self.scan_startup_entries())
        except Exception:
            return 0

    def scan_startup_entries(self, item_callback=None) -> list[StartupEntry]:
        entries: list[StartupEntry] = []
        entries.extend(self._registry_entries(item_callback=item_callback))

        folder_entries = self._startup_folder_entries()
        entries.extend(folder_entries)
        if item_callback is not None:
            for rec in folder_entries:
                item_callback(
                    f"Автозагрузка: {rec.name} | состояние: {'включена' if rec.enabled else 'отключена'} | источник: {rec.location} | команда: {rec.command}"
                )

        task_entries = self._scheduled_task_entries()
        entries.extend(task_entries)
        if item_callback is not None:
            for rec in task_entries:
                item_callback(
                    f"Автозагрузка: {rec.name} | состояние: {'включена' if rec.enabled else 'отключена'} | источник: {rec.location} | команда: {rec.command}"
                )

        wmi_entries = self._wmi_subscription_entries()
        entries.extend(wmi_entries)
        if item_callback is not None:
            for rec in wmi_entries:
                item_callback(
                    f"Автозагрузка: {rec.name} | состояние: {'включена' if rec.enabled else 'отключена'} | источник: {rec.location} | команда: {rec.command}"
                )

        return entries

    def _registry_entries(self, item_callback=None) -> list[StartupEntry]:
        result: list[StartupEntry] = []
        for hive, key_path, hive_name, value_names in _RUN_KEY_SPECS:
            try:
                key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ)
            except OSError:
                continue

            location = f"{hive_name}\\{key_path}"
            with key:
                if value_names is None:
                    idx = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, idx)
                            rec = self._build_registry_entry(name, value, location, key_path)
                            if rec is not None:
                                result.append(rec)
                                if item_callback is not None:
                                    item_callback(
                                        f"Автозагрузка: {rec.name} | состояние: {'включена' if rec.enabled else 'отключена'} | источник: {rec.location} | команда: {rec.command}"
                                    )
                            idx += 1
                        except OSError:
                            break
                else:
                    for value_name in value_names:
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                        except OSError:
                            continue
                        rec = self._build_registry_entry(value_name, value, location, key_path)
                        if rec is not None:
                            result.append(rec)
                            if item_callback is not None:
                                item_callback(
                                    f"Автозагрузка: {rec.name} | состояние: {'включена' if rec.enabled else 'отключена'} | источник: {rec.location} | команда: {rec.command}"
                                )
        return result

    @staticmethod
    def _build_registry_entry(name: str, value: object, location: str, key_path: str) -> StartupEntry | None:
        normalized_name = str(name)
        command = str(value)
        stripped = command.strip()
        lowered_key = key_path.lower()
        lowered_name = normalized_name.lower()

        if lowered_key.endswith(r"\windows") and lowered_name in {"load", "run"} and not stripped:
            return None

        if lowered_key.endswith(r"\winlogon") and lowered_name == "shell":
            shell_value = stripped.strip('"').lower()
            if shell_value in {"", "explorer.exe"}:
                return None

        enabled = not stripped.startswith("REM_DISABLED::")
        if lowered_key.endswith(r"\windows") and lowered_name in {"load", "run"}:
            enabled = enabled and bool(stripped)

        return StartupEntry(
            name=normalized_name,
            command=command,
            location=location,
            enabled=enabled,
            category="Application",
        )

    def _startup_folder_entries(self) -> list[StartupEntry]:
        result: list[StartupEntry] = []
        allowed_suffixes = {
            ".lnk",
            ".exe",
            ".bat",
            ".cmd",
            ".ps1",
            ".vbs",
            ".js",
            ".url",
            ".com",
            ".scr",
        }
        appdata = os.getenv("APPDATA", "")
        user_startup = Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup" if appdata else None
        candidates = [
            Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"),
        ]
        if user_startup is not None:
            candidates.append(user_startup)

        for folder in candidates:
            if not folder.exists():
                continue
            for item in folder.iterdir():
                if item.is_dir():
                    continue
                suffix = item.suffix.lower()
                if suffix == ".ini":
                    continue
                if suffix and suffix not in allowed_suffixes:
                    continue
                location = f"StartupFolder:{folder}"
                result.append(
                    StartupEntry(
                        name=item.name,
                        command=str(item),
                        location=location,
                        enabled=not item.name.lower().endswith(".disabled"),
                        category="Application",
                    )
                )
        return result

    def _scheduled_task_entries(self) -> list[StartupEntry]:
        result: list[StartupEntry] = []
        script = (
            "$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue;"
            "if (-not $tasks) { @() | ConvertTo-Json -Compress; exit 0 };"
            "$result = foreach ($t in $tasks) {"
            "  $actions = @($t.Actions | ForEach-Object {"
            "    $exec = [string]$_.Execute;"
            "    $args = [string]$_.Arguments;"
            "    if ($args) { ($exec + ' ' + $args).Trim() } else { $exec }"
            "  });"
            "  [PSCustomObject]@{"
            "    Name = (([string]$t.TaskPath) + ([string]$t.TaskName));"
            "    Command = ($actions -join ' | ');"
            "    Enabled = [bool]$t.Settings.Enabled;"
            "    State = [string]$t.State;"
            "    Principal = [string]$t.Principal.UserId"
            "  }"
            "};"
            "$result | ConvertTo-Json -Compress"
        )
        for row in self._run_powershell_json(script):
            task_name = str(row.get("Name") or "").strip()
            if not task_name:
                continue
            enabled = bool(row.get("Enabled", True))
            state = str(row.get("State") or "").strip()
            principal = str(row.get("Principal") or "").strip()
            command = str(row.get("Command") or "").strip()
            payload = command or f"RunAs={principal};State={state}"
            result.append(
                StartupEntry(
                    name=task_name,
                    command=payload,
                    location="ScheduledTask",
                    enabled=enabled,
                    category="DelayedTask",
                )
            )
        return result

    def _wmi_subscription_entries(self) -> list[StartupEntry]:
        script = (
            "$consumers = @{};"
            "Get-CimInstance -Namespace root/subscription -ClassName CommandLineEventConsumer -ErrorAction SilentlyContinue | ForEach-Object {"
            "  $consumers[[string]$_.__RELPATH] = [PSCustomObject]@{Name=[string]$_.Name; Type='CommandLine'; Payload=[string]$_.CommandLineTemplate}"
            "};"
            "Get-CimInstance -Namespace root/subscription -ClassName ActiveScriptEventConsumer -ErrorAction SilentlyContinue | ForEach-Object {"
            "  $consumers[[string]$_.__RELPATH] = [PSCustomObject]@{Name=[string]$_.Name; Type='Script'; Payload=[string]$_.ScriptText}"
            "};"
            "$filters = @{};"
            "Get-CimInstance -Namespace root/subscription -ClassName __EventFilter -ErrorAction SilentlyContinue | ForEach-Object {"
            "  $filters[[string]$_.__RELPATH] = [string]$_.Name"
            "};"
            "$result = foreach ($b in (Get-CimInstance -Namespace root/subscription -ClassName __FilterToConsumerBinding -ErrorAction SilentlyContinue)) {"
            "  $filterPath = [string]$b.Filter;"
            "  $consumerPath = [string]$b.Consumer;"
            "  $c = $consumers[$consumerPath];"
            "  [PSCustomObject]@{"
            "    Binding = [string]$b.__RELPATH;"
            "    Filter = $filterPath;"
            "    FilterName = [string]$filters[$filterPath];"
            "    Consumer = $consumerPath;"
            "    ConsumerName = if ($c) { [string]$c.Name } else { '' };"
            "    ConsumerType = if ($c) { [string]$c.Type } else { '' };"
            "    Payload = if ($c) { [string]$c.Payload } else { '' }"
            "  }"
            "};"
            "$result | ConvertTo-Json -Compress"
        )
        result: list[StartupEntry] = []
        for row in self._run_powershell_json(script):
            binding = str(row.get("Binding") or "").strip()
            if not binding:
                continue
            filter_name = str(row.get("FilterName") or "").strip()
            consumer_name = str(row.get("ConsumerName") or "").strip()
            consumer_type = str(row.get("ConsumerType") or "").strip()
            name = " / ".join(part for part in [filter_name, consumer_name] if part) or binding
            payload = str(row.get("Payload") or "").strip()
            if consumer_type:
                payload = f"{consumer_type}: {payload}" if payload else consumer_type
            result.append(
                StartupEntry(
                    name=name,
                    command=payload,
                    location=f"WMIBinding:{binding}",
                    enabled=True,
                    category="WMI",
                )
            )
        return result

    @staticmethod
    def _run_powershell_json(script: str) -> list[dict[str, Any]]:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError:
            return []
        if proc.returncode != 0:
            return []
        payload = (proc.stdout or "").strip()
        if not payload:
            return []
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
        return []
