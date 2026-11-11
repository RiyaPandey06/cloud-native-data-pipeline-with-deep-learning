from __future__ import annotations

from datetime import UTC, datetime, timedelta


def seconds_until_next_slot(now: datetime, interval_minutes: int, offset_minutes: int = 5) -> float:
    """Seconds to sleep so runs start on clock-aligned slots (e.g. hh:05 for hourly).

    Sleeping a fixed interval *after* a run drifts later with every run and silently skips
    slots when a run is slow. Aligning to the clock keeps the cadence stable; the incremental
    window (with overlap) then covers any slot that was missed.
    """
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slot = day_start + timedelta(minutes=offset_minutes % interval_minutes)
    interval = timedelta(minutes=interval_minutes)
    while slot <= now:
        slot += interval
    return (slot - now).total_seconds()
