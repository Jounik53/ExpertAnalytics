from __future__ import annotations

from app.application.services.report_insight_service import ReportInsightService


def test_report_insight_builds_human_readable_sections():
    payload = {
        "used_ram_gb": 42.0,
        "process_records": [{"memory_level": "high"}, {"memory_level": "low"}],
        "diagnostics_snapshot": {"kernel_leak_risk": "medium"},
        "sampling_points": [{"timestamp": "2026-01-01T10:00:00", "used_ram_gb": 40.5, "commit_gb": 41.0}],
    }
    insight = ReportInsightService().build(payload)

    assert any("42.00" in x for x in insight.cause_summary)
    assert any("medium" in x for x in insight.cause_summary)
    assert len(insight.priority_steps) == 3
    assert len(insight.timeline) == 1
