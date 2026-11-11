from datetime import date, datetime

import pandas as pd

from air_quality_pipeline.transform import expected_row_count, prepare_frame

NOW = datetime(2026, 1, 1, 5, 30, tzinfo=pd.Timestamp("2026-01-01", tz="UTC").tzinfo)


def frame(hours: int, pm25: list) -> pd.DataFrame:
    return pd.DataFrame(
        {"ts_utc": pd.date_range("2026-01-01", periods=hours, freq="h", tz="UTC"), "pm25": pm25}
    )


def test_future_forecast_hours_are_dropped_and_counted():
    prepared = prepare_frame(frame(24, [1.0] * 24), NOW)
    assert prepared.future_rows_dropped == 18  # hours 06:00-23:00
    assert prepared.frame.ts_utc.max() == pd.Timestamp("2026-01-01 05:00", tz="UTC")


def test_unpublished_newest_hours_are_trimmed_as_late_data():
    values = [1.0, 2.0, 3.0, None, None, None]
    prepared = prepare_frame(frame(6, values), NOW)
    assert prepared.late_rows_dropped == 3
    assert len(prepared.frame) == 3


def test_nulls_in_the_middle_are_kept_for_the_quality_gate():
    prepared = prepare_frame(frame(6, [1.0, None, 3.0, 4.0, 5.0, 6.0]), NOW)
    assert len(prepared.frame) == 6
    assert prepared.frame.pm25.isna().sum() == 1


def test_all_null_batch_becomes_empty():
    prepared = prepare_frame(frame(4, [None] * 4), NOW)
    assert prepared.frame.empty


def test_expected_rows_are_capped_at_the_last_complete_hour():
    assert expected_row_count(date(2026, 1, 1), date(2026, 1, 1), NOW) == 6  # 00:00..05:00
    assert expected_row_count(date(2025, 12, 31), date(2026, 1, 1), NOW) == 30
    assert expected_row_count(date(2025, 12, 1), date(2025, 12, 1), NOW) == 24
