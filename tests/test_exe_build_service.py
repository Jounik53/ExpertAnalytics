from pathlib import Path

from app.application.services.exe_build_service import ExeBuildService


def test_exe_build_service_returns_tuple_on_failure(tmp_path: Path):
    ok, msg = ExeBuildService().build(tmp_path)
    assert isinstance(ok, bool)
    assert isinstance(msg, str)
