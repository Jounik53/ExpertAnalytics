from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities import ProcessRecord


@dataclass(slots=True)
class HeuristicRule:
    rule_id: str
    title: str
    enabled_by_default: bool = True

    def evaluate(self, rec: ProcessRecord) -> tuple[int, str | None]:
        raise NotImplementedError


class SuspiciousPathRule(HeuristicRule):
    def evaluate(self, rec: ProcessRecord) -> tuple[int, str | None]:
        p = rec.exe_path.lower()
        markers = ["\\temp\\", "\\appdata\\", "\\downloads\\", "\\public\\"]
        if any(m in p for m in markers):
            return 25, "Запуск из подозрительного пути"
        return 0, None


class MinerKeywordRule(HeuristicRule):
    def evaluate(self, rec: ProcessRecord) -> tuple[int, str | None]:
        text = f"{rec.name} {rec.command_line}".lower()
        keys = ["xmrig", "miner", "stratum", "cpuminer", "cgminer", "pool"]
        if any(k in text for k in keys):
            return 45, "Обнаружены ключевые слова майнера"
        return 0, None


class HighCpuMemoryRule(HeuristicRule):
    def evaluate(self, rec: ProcessRecord) -> tuple[int, str | None]:
        if rec.cpu_percent >= 60 and rec.memory_mb >= 500:
            return 20, "Высокая CPU+RAM нагрузка"
        return 0, None


class UnsignedOutsideSystemRule(HeuristicRule):
    def evaluate(self, rec: ProcessRecord) -> tuple[int, str | None]:
        if not rec.is_system and rec.exe_path and "program files" not in rec.exe_path.lower() and "windows" not in rec.exe_path.lower():
            return 10, "Необычное расположение исполняемого файла"
        return 0, None


class HeuristicEngine:
    def __init__(self) -> None:
        self.rules = [
            SuspiciousPathRule("suspicious_path", "Подозрительный путь"),
            MinerKeywordRule("miner_keywords", "Ключевые слова майнера"),
            HighCpuMemoryRule("high_cpu_memory", "Высокая CPU+RAM"),
            UnsignedOutsideSystemRule("unusual_location", "Необычное расположение"),
        ]

    def schema(self) -> dict[str, bool]:
        return {rule.rule_id: rule.enabled_by_default for rule in self.rules}

    def evaluate_many(self, records: list[ProcessRecord], enabled: dict[str, bool]) -> None:
        for rec in records:
            score = 0
            hits: list[str] = []
            for rule in self.rules:
                if not enabled.get(rule.rule_id, True):
                    continue
                delta, msg = rule.evaluate(rec)
                if delta > 0 and msg:
                    score += delta
                    hits.append(f"{rule.title}: {msg}")
            rec.heuristic_score = min(100, score)
            rec.heuristic_hits = hits
