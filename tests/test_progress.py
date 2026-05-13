from __future__ import annotations

from cfmerge.models import ProgressEvent
from cfmerge.progress import ProgressPhase, ProgressTracker


def test_progress_tracker_emits_monotonic_overall_percent_and_finishes_at_100() -> None:
    events: list[ProgressEvent] = []
    tracker = ProgressTracker(
        events.append,
        (
            ProgressPhase("scan", "Scan", 2.0),
            ProgressPhase("copy", "Copy", 1.0),
        ),
        throttle_seconds=0,
    )

    tracker.start("scan", current=0, total=10, unit="files")
    tracker.update("scan", 5, total=10, unit="files")
    tracker.done("scan", total=10, unit="files")
    tracker.start("copy", current=0, total=4, unit="files")
    tracker.update("copy", 2, total=4, unit="files")
    tracker.done("copy", total=4, unit="files")

    percents = [event.overall_percent for event in events if event.overall_percent is not None]

    assert percents == sorted(percents)
    assert percents[-1] == 100.0
    assert [event.event_type for event in events[:2]] == ["phase_start", "phase_progress"]


def test_progress_tracker_throttles_progress_updates_but_not_done() -> None:
    events: list[ProgressEvent] = []
    tracker = ProgressTracker(
        events.append,
        (ProgressPhase("scan", "Scan", 1.0),),
        throttle_seconds=60,
    )

    tracker.start("scan", current=0, total=100)
    for current in range(1, 20):
        tracker.update("scan", current, total=100)
    tracker.done("scan", total=100)

    assert [event.event_type for event in events].count("phase_progress") <= 1
    assert events[-1].event_type == "phase_done"
    assert events[-1].overall_percent == 100.0
