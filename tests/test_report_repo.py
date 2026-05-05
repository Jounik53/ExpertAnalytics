from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.adapters.persistence.report_repo import JsonReportRepository
from app.domain.entities import ScanSnapshot


def test_report_repository_list_and_read(tmp_path: Path):
    repo = JsonReportRepository(tmp_path)
    snapshot = ScanSnapshot(created_at=datetime.now(), total_ram_gb=64.0, used_ram_gb=40.0)

    path = repo.save_snapshot(snapshot)
    listed = repo.list_reports()
    assert path in listed

    payload = repo.read_report(path)
    assert payload["total_ram_gb"] == 64.0
    assert payload["used_ram_gb"] == 40.0
