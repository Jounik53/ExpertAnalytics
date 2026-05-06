from __future__ import annotations

from pathlib import Path

from app.adapters.persistence.settings_repo import JsonSettingsRepository
from app.application.services.settings_service import AppSettings, SettingsService


def test_settings_save_and_load(tmp_path: Path):
    repo = JsonSettingsRepository(tmp_path / "settings.json")
    service = SettingsService(repo)

    payload = AppSettings(vt_api_key="k", process_high_mb=1000, process_medium_mb=400, enable_vt_lookup=True)
    service.save(payload)

    loaded = service.load()
    assert loaded.vt_api_key == "k"
    assert loaded.process_high_mb == 1000
    assert loaded.process_medium_mb == 400
    assert loaded.enable_vt_lookup is True
    assert loaded.enable_driver_module is True


def test_settings_theme_defaults_to_dark_and_normalizes(tmp_path: Path):
    repo = JsonSettingsRepository(tmp_path / "settings.json")
    service = SettingsService(repo)

    loaded = service.load()
    assert loaded.ui_theme == "dark"

    service.save(AppSettings(ui_theme="LIGHT"))
    loaded2 = service.load()
    assert loaded2.ui_theme == "light"

    service.save(AppSettings(ui_theme="unknown"))
    loaded3 = service.load()
    assert loaded3.ui_theme == "dark"


def test_settings_startup_mode_defaults_and_normalizes(tmp_path: Path):
    repo = JsonSettingsRepository(tmp_path / "settings.json")
    service = SettingsService(repo)

    loaded = service.load()
    assert loaded.startup_mode == "general"
    assert loaded.open_selected_mode_on_startup is False

    service.save(AppSettings(startup_mode="startup", open_selected_mode_on_startup=True, exe_build_enabled=False))
    loaded2 = service.load()
    assert loaded2.startup_mode == "startup"
    assert loaded2.open_selected_mode_on_startup is True
    assert loaded2.exe_build_enabled is False

    service.save(AppSettings(startup_mode="unknown-mode"))
    loaded3 = service.load()
    assert loaded3.startup_mode == "general"
