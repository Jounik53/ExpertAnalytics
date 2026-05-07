from __future__ import annotations

from app.application.services.heuristics_service import HeuristicEngine
from app.domain.entities import MemoryLevel, ProcessRecord


def _proc(name: str, cmd: str, path: str, cpu: float, mem: float) -> ProcessRecord:
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


def test_parse_rules_text_reports_json_error():
    payload, diags = HeuristicEngine.parse_rules_text("[{")
    assert payload == []
    assert len(diags) == 1
    assert diags[0].code == "E0001"


def test_validate_raw_rules_reports_semantic_error():
    eng = HeuristicEngine()
    bad = [
        {
            "rule_id": "bad",
            "title": "Bad",
            "score": 10,
            "applies_to": ["scan"],
            "script": {"op": "unknown", "field": "name", "value": "x"},
        }
    ]
    diags = eng.validate_raw_rules(bad)
    assert any(d.code == "E1104" for d in diags)


def test_evaluate_manual_selected_rules():
    p = _proc("powershell.exe", "powershell -enc AAAA", "C:\\Users\\Public\\ps.exe", 10, 100)
    eng = HeuristicEngine()
    res = eng.evaluate_one(p, enabled=eng.schema(), mode="heuristic", selected_rule_ids={"ps_encoded"})
    assert res.score > 0
    assert any(m.rule_id == "ps_encoded" for m in res.matches)


def test_evaluate_file_path_rule():
    eng = HeuristicEngine()
    res = eng.evaluate_file("C:\\Users\\Public\\evil.ps1", enabled=eng.schema(), mode="heuristic")
    assert isinstance(res.score, int)
    assert res.score >= 0


def test_rule_packs_contains_known_pack():
    packs = HeuristicEngine.default_rule_packs()
    assert "powershell_abuse" in packs
    assert "ps_encoded" in packs["powershell_abuse"]
