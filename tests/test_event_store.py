from pathlib import Path

from app.ui.event_store import UIEventStore
from app.ui.notifications import EventLevel, UIEvent


def test_event_store_persists_events(tmp_path: Path):
    store = UIEventStore(tmp_path / "events.json")
    events = [UIEvent(timestamp="t1", level=EventLevel.INFO, title="x", message="y")]
    store.save(events)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].title == "x"
