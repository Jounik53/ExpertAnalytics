from app.application.services.heuristics_service import HeuristicEngine
from app.domain.entities import MemoryLevel, ProcessRecord


def _proc(name: str, cmd: str, path: str, cpu: float, mem: float):
    return ProcessRecord(
        pid=1,
        name=name,
        exe_path=path,
        username="u",
        memory_mb=mem,
        memory_level=MemoryLevel.HIGH,
        cpu_percent=cpu,
        create_time="t",
        is_system=False,
        command_line=cmd,
    )


def test_heuristics_detects_miner_keywords():
    p = _proc("x", "xmrig --pool abc", "C:\\Temp\\x.exe", 70, 600)
    eng = HeuristicEngine()
    eng.evaluate_many([p], eng.schema())
    assert p.heuristic_score > 0
    assert len(p.heuristic_hits) > 0
