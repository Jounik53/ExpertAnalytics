from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from app.ui.notifications import UIEvent, EventLevel


class UIEventStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[UIEvent]:
        if not self._path.exists():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            result: list[UIEvent] = []
            for row in payload:
                level = EventLevel(row.get("level", "info"))
                result.append(
                    UIEvent(
                        timestamp=str(row.get("timestamp", "")),
                        level=level,
                        title=str(row.get("title", "")),
                        message=str(row.get("message", "")),
                    )
                )
            return result
        except Exception:
            return []

    def save(self, events: list[UIEvent]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(x) for x in events[-3000:]]
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
