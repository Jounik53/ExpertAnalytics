from __future__ import annotations

from app.ui.services.theme_service import ThemeService


def test_theme_service_normalizes_theme_values():
    svc = ThemeService()
    assert svc.normalize_theme("LIGHT") == "light"
    assert svc.normalize_theme("something-else") == "dark"


def test_theme_service_metric_palette_returns_expected_ranges():
    svc = ThemeService()

    high_bg, high_fg = svc.metric_palette("dark", 90)
    medium_bg, medium_fg = svc.metric_palette("dark", 60)
    low_bg, low_fg = svc.metric_palette("dark", 10)

    assert high_bg == "#7e3434"
    assert high_fg == "#fff1f1"
    assert medium_bg == "#85722b"
    assert medium_fg == "#fff9e6"
    assert isinstance(low_bg, str)
    assert isinstance(low_fg, str)
