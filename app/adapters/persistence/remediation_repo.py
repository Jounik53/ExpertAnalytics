from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from app.domain.entities import RemediationRecord
from app.domain.ports import RemediationRepositoryPort


class JsonRemediationRepository(RemediationRepositoryPort):
    def __init__(self, path: Path) -> None:
        self._path = path

    def append_record(self, record: RemediationRecord) -> None:
        records = self.list_records()
        records.append(record)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([asdict(x) for x in records], ensure_ascii=False, indent=2), encoding="utf-8")

    def list_records(self) -> list[RemediationRecord]:
        if not self._path.exists():
            return []
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return [RemediationRecord(**row) for row in payload]
