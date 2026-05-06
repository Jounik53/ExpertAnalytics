from __future__ import annotations

import json
from pathlib import Path

from app.adapters.system.service_scanner import PsutilServiceScanner


def test_service_scanner_trusted_db_normalizes_path(tmp_path: Path):
    db = tmp_path / "trusted.json"
    db.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "names": ["testsvc"],
                        "display_names": ["test service"],
                        "paths": [r"c:\windows\system32\svchost.exe"],
                        "sha256": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    scanner = PsutilServiceScanner(trusted_db_path=db)
    ok, reason = scanner._is_trusted(
        name="TestSvc",
        display_name="Test Service",
        path=r"C:\Windows\System32\svchost.exe",
        file_sha256="",
    )

    assert ok is True
    assert "name" in reason
    assert "path" in reason
