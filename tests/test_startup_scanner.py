from __future__ import annotations

from types import SimpleNamespace

from app.adapters.system.startup_scanner import RegistryStartupScanner


def test_build_registry_entry_skips_empty_windows_load():
    rec = RegistryStartupScanner._build_registry_entry(
        name="Load",
        value="   ",
        location=r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\Windows",
        key_path=r"Software\Microsoft\Windows NT\CurrentVersion\Windows",
    )

    assert rec is None


def test_build_registry_entry_skips_default_winlogon_shell():
    rec = RegistryStartupScanner._build_registry_entry(
        name="Shell",
        value="explorer.exe",
        location=r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon",
        key_path=r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon",
    )

    assert rec is None


def test_build_registry_entry_keeps_custom_winlogon_shell_enabled():
    rec = RegistryStartupScanner._build_registry_entry(
        name="Shell",
        value=r"explorer.exe,C:\Users\Public\app.exe",
        location=r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon",
        key_path=r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon",
    )

    assert rec is not None
    assert rec.enabled is True
    assert rec.command.endswith("app.exe")


def test_run_powershell_json_parses_dict_and_list(monkeypatch):
    def fake_run_list(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='[{"Name":"one"}]')

    monkeypatch.setattr("app.adapters.system.startup_scanner.subprocess.run", fake_run_list)
    parsed_list = RegistryStartupScanner._run_powershell_json("ignored")
    assert parsed_list == [{"Name": "one"}]

    def fake_run_dict(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='{"Name":"one"}')

    monkeypatch.setattr("app.adapters.system.startup_scanner.subprocess.run", fake_run_dict)
    parsed_dict = RegistryStartupScanner._run_powershell_json("ignored")
    assert parsed_dict == [{"Name": "one"}]


def test_run_powershell_json_returns_empty_on_invalid_payload(monkeypatch):
    def fake_run_invalid(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="not-json")

    monkeypatch.setattr("app.adapters.system.startup_scanner.subprocess.run", fake_run_invalid)
    parsed = RegistryStartupScanner._run_powershell_json("ignored")
    assert parsed == []
