from app.ui.notifications import EventLevel, UINotifier


def test_notifier_keeps_events():
    n = UINotifier()
    n.emit(EventLevel.INFO, "t", "m")
    events = n.list_events()
    assert len(events) == 1
    assert events[0].level == EventLevel.INFO
