from __future__ import annotations

import time
from datetime import datetime

from app.domain.entities import SamplingPoint
from app.domain.ports import ProcessScannerPort, SamplerPort


class WindowsSampler(SamplerPort):
    def __init__(self, process_scanner: ProcessScannerPort) -> None:
        self._process_scanner = process_scanner

    def collect_points(self, interval_sec: int, points: int, high_mb: int, medium_mb: int, cancel_check) -> list[SamplingPoint]:
        result: list[SamplingPoint] = []
        for i in range(points):
            if cancel_check():
                break
            records = self._process_scanner.scan_processes(high_mb=high_mb, medium_mb=medium_mb)
            total, used = self._process_scanner.memory_totals()
            top = [
                {"pid": str(x.pid), "name": x.name, "memory_mb": f"{x.memory_mb:.1f}", "path": x.exe_path}
                for x in records[:10]
            ]
            result.append(
                SamplingPoint(
                    timestamp=datetime.now().isoformat(),
                    used_ram_gb=used,
                    commit_gb=used,
                    paged_pool_gb=0.0,
                    nonpaged_pool_gb=0.0,
                    top_processes=top,
                )
            )
            if i < points - 1:
                for _ in range(max(1, interval_sec)):
                    if cancel_check():
                        return result
                    time.sleep(1)
        return result
