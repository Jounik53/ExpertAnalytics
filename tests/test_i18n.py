from __future__ import annotations

from app.ui.i18n import I18n


def test_i18n_defaults_to_ru_and_switches_to_en():
    i18n = I18n()
    assert i18n.t("status.ready") == "Готово"

    i18n.set_locale("en")
    assert i18n.t("status.ready") == "Ready"
