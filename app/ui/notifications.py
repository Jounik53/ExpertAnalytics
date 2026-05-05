from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from datetime import timedelta


class EventLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class UIEvent:
    timestamp: str
    level: EventLevel
    title: str
    message: str


class UINotifier:
    def __init__(self) -> None:
        self._events: list[UIEvent] = []

    def emit(self, level: EventLevel, title: str, message: str) -> UIEvent:
        if self._events:
            prev = self._events[-1]
            if prev.level == level and prev.title == title and prev.message == message:
                try:
                    prev_dt = datetime.fromisoformat(prev.timestamp)
                    if datetime.now() - prev_dt < timedelta(seconds=5):
                        return prev
                except ValueError:
                    pass
        event = UIEvent(timestamp=datetime.now().isoformat(), level=level, title=title, message=message)
        self._events.append(event)
        if len(self._events) > 500:
            self._events = self._events[-500:]
        return event

    def list_events(self) -> list[UIEvent]:
        return list(self._events)

    def load_events(self, events: list[UIEvent]) -> None:
        self._events = list(events)[-500:]
