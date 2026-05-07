from __future__ import annotations

from app.application.services.settings_service import AppSettings


def test_appsettings_has_heuristic_extended_fields_defaults():
    s = AppSettings()
    assert s.heuristic_rule_definitions is None
    assert s.heuristic_rule_packs is None
    assert s.heuristic_default_mode == "scan"
    assert s.heuristic_last_selected_rules is None
    assert s.heuristic_last_selected_pack == "all"
