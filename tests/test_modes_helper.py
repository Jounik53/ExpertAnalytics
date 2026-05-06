from __future__ import annotations

from app.application.helpers.modes import mode_id_from_label, mode_label, normalize_mode


def test_normalize_mode_accepts_known_and_fallbacks_to_general():
    assert normalize_mode("services") == "services"
    assert normalize_mode(" SERVICES ") == "services"
    assert normalize_mode("unknown") == "general"


def test_mode_label_and_reverse_mapping():
    assert mode_label("startup") == "Автозагрузка"
    assert mode_label("bad") == "Общий"
    assert mode_id_from_label("Сканирование") == "scanning"
    assert mode_id_from_label("Несуществующий") == "general"
