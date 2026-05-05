from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ReportInsight:
    cause_summary: list[str]
    priority_steps: list[str]
    timeline: list[str]


class ReportInsightService:
    def build(self, payload: dict) -> ReportInsight:
        processes = payload.get("process_records", [])
        high = [p for p in processes if p.get("memory_level") == "high"]
        diag = payload.get("diagnostics_snapshot") or {}

        cause = [
            f"Использовано RAM: {payload.get('used_ram_gb', 0):.2f} GB",
            f"Высокое потребление процессов: {len(high)}",
            f"Риск утечки ядра: {diag.get('kernel_leak_risk', 'unknown')}",
        ]

        steps = [
            "Проверить top high-memory процессы и изолировать подозрительные файлы.",
            "Отключить подозрительные записи автозагрузки и отложенные задачи.",
            "Собрать повторный sampling и проверить рост paged/nonpaged pool.",
        ]

        timeline = [
            f"{p.get('timestamp')} RAM={p.get('used_ram_gb')} commit={p.get('commit_gb')}"
            for p in payload.get("sampling_points", [])
        ]

        return ReportInsight(cause_summary=cause, priority_steps=steps, timeline=timeline)
