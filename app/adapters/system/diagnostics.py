from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psutil

from app.domain.entities import DiagnosticsSnapshot, ProcessRecord
from app.domain.ports import DiagnosticsPort


class WindowsDiagnosticsCollector(DiagnosticsPort):
    def collect(self, used_ram_gb: float, process_records: list[ProcessRecord]) -> DiagnosticsSnapshot:
        commit_gb, commit_limit_gb = self._commit_info()
        mem_compress = self._memory_compression_gb()
        paged_gb = self._counter_gb(r"\\Memory\\Pool Paged Bytes")
        nonpaged_gb = self._counter_gb(r"\\Memory\\Pool Nonpaged Bytes")
        standby_gb = self._counter_gb(r"\\Memory\\Standby Cache Reserve Bytes") + self._counter_gb(r"\\Memory\\Standby Cache Normal Priority Bytes")

        proc_sum_gb = sum(p.memory_mb for p in process_records) / 1024.0
        unattributed = max(0.0, used_ram_gb - proc_sum_gb)

        risk = "low"
        if nonpaged_gb > 2.0 or paged_gb > 4.0:
            risk = "medium"
        if nonpaged_gb > 4.0 or unattributed > 4.0:
            risk = "high"

        recs = []
        if risk != "low":
            recs.append("Check driver updates and disable suspicious third-party kernel services")
            recs.append("Capture pool counters over time to confirm growth trend")
        if commit_limit_gb > 0 and commit_gb / commit_limit_gb > 0.85:
            recs.append("Commit is near limit; increase pagefile and inspect commit-heavy services")

        return DiagnosticsSnapshot(
            commit_gb=commit_gb,
            commit_limit_gb=commit_limit_gb,
            memory_compression_gb=mem_compress,
            paged_pool_gb=paged_gb,
            nonpaged_pool_gb=nonpaged_gb,
            standby_cache_gb=standby_gb,
            unattributed_gb=unattributed,
            kernel_leak_risk=risk,
            recommendations=recs,
        )

    def _commit_info(self) -> tuple[float, float]:
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        limit = (vm.total + sm.total) / (1024 ** 3)
        commit = vm.used / (1024 ** 3)
        return commit, limit

    def _memory_compression_gb(self) -> float:
        return self._counter_gb(r"\\Memory\\Compressed Page Size")

    def _counter_gb(self, counter_path: str) -> float:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Counter '{counter_path}').CounterSamples[0].CookedValue",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=10,
            )

            if proc.returncode != 0:
                return 0.0
            value = float(proc.stdout.strip().splitlines()[-1])
            return value / (1024 ** 3)
        except Exception:
            return 0.0
