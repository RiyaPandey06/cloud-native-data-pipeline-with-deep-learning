from datetime import UTC, datetime

import pytest

from air_quality_pipeline.schedule import seconds_until_next_slot


def test_hourly_runs_align_to_five_past():
    now = datetime(2026, 1, 1, 10, 20, tzinfo=UTC)
    assert seconds_until_next_slot(now, 60) == 45 * 60


def test_slot_exactly_now_waits_for_the_next_one():
    now = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
    assert seconds_until_next_slot(now, 60) == 3600


def test_before_the_slot_in_the_same_hour():
    now = datetime(2026, 1, 1, 10, 1, tzinfo=UTC)
    assert seconds_until_next_slot(now, 60) == 4 * 60


def test_thirty_minute_cadence():
    now = datetime(2026, 1, 1, 10, 10, tzinfo=UTC)
    assert seconds_until_next_slot(now, 30) == 25 * 60


def test_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        seconds_until_next_slot(datetime(2026, 1, 1, tzinfo=UTC), 0)
