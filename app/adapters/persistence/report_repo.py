from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from app.domain.entities import ScanSnapshot
from app.domain.ports import ReportRepositoryPort


class JsonReportRepository(ReportRepositoryPort):
    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = reports_dir

    def save_snapshot(self, snapshot: ScanSnapshot) -> str:
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        filename = f"report_{snapshot.created_at.strftime('%Y%m%d_%H%M%S')}.json"
        path = self._reports_dir / filename
        payload = asdict(snapshot)
        payload["created_at"] = snapshot.created_at.isoformat()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def list_reports(self) -> list[str]:
        if not self._reports_dir.exists():
            return []
        return [str(p) for p in sorted(self._reports_dir.glob("report_*.json"), reverse=True)]

    def read_report(self, path: str) -> dict:
        report_path = Path(path)
        return json.loads(report_path.read_text(encoding="utf-8"))
