from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import psutil

from app.domain.entities import MemoryLevel, ProcessRecord
from app.domain.ports import ProcessScannerPort


class PsutilProcessScanner(ProcessScannerPort):
    def estimate_process_count(self) -> int:
        try:
            return len(psutil.pids())
        except Exception:
            return 0

    def scan_processes(self, high_mb: int, medium_mb: int, item_callback=None, record_callback=None) -> list[ProcessRecord]:
        records: list[ProcessRecord] = []
        for proc in psutil.process_iter(["pid", "name", "username", "create_time", "exe", "cmdline"]):
            try:
                mem = proc.memory_info().rss / (1024 * 1024)
                level = MemoryLevel.LOW
                if mem >= high_mb:
                    level = MemoryLevel.HIGH
                elif mem >= medium_mb:
                    level = MemoryLevel.MEDIUM

                exe = proc.info.get("exe") or ""
                cmdline = " ".join(proc.info.get("cmdline") or [])
                record = ProcessRecord(
                    pid=int(proc.info["pid"]),
                    name=str(proc.info.get("name") or "unknown"),
                    exe_path=exe,
                    username=str(proc.info.get("username") or "unknown"),
                    memory_mb=mem,
                    memory_level=level,
                    cpu_percent=float(proc.cpu_percent(interval=0.0)),
                    create_time=datetime.fromtimestamp(proc.info.get("create_time") or 0).isoformat(),
                    is_system=("\\Windows\\" in exe) or ("/Windows/" in exe),
                    command_line=cmdline,
                    file_sha256=self._sha256(exe),
                )
                records.append(record)
                if item_callback is not None:
                    level_ru = {
                        "high": "высокий",
                        "medium": "средний",
                        "low": "низкий",
                    }.get(record.memory_level.value, record.memory_level.value)
                    item_callback(
                        f"Процесс: {record.name} (PID {record.pid}) | память: {record.memory_mb:.1f} МБ | уровень: {level_ru} | путь: {record.exe_path or '-'}"
                    )
                if record_callback is not None:
                    record_callback(record)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        records.sort(key=lambda x: x.memory_mb, reverse=True)
        return records

    def memory_totals(self) -> tuple[float, float]:
        vm = psutil.virtual_memory()
        return vm.total / (1024 ** 3), vm.used / (1024 ** 3)

    @staticmethod
    def _sha256(exe_path: str) -> str | None:
        if not exe_path:
            return None
        path = Path(exe_path)
        if not path.exists() or not path.is_file():
            return None
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None
