import pandas as pd
import pytest

from air_quality_pipeline.config import QualityConfig
from air_quality_pipeline.quality import (
    DataQualityError,
    raise_if_failed,
    validate_observations,
    warnings_in,
)

NOW = pd.Timestamp("2026-01-01 03:00", tz="UTC")


def good_frame(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_utc": pd.date_range("2026-01-01", periods=rows, freq="h", tz="UTC"),
            "pm25": [10.0 + i for i in range(rows)],
            "pm10": [20.0 + i for i in range(rows)],
            "no2": [4.0] * rows,
            "ozone": [40.0] * rows,
            "temperature_2m": [25.0] * rows,
            "relative_humidity_2m": [50.0] * rows,
            "precipitation": [0.0] * rows,
            "wind_speed_10m": [3.0] * rows,
        }
    )


def by_name(results):
    return {result.name: result for result in results}


def test_quality_passes_valid_batch():
    results = validate_observations(good_frame(), now=NOW, expected_rows=3)
    raise_if_failed(results)
    assert {result.status for result in results} == {"pass"}


def test_negative_pm25_quarantines_the_batch():
    frame = good_frame()
    frame.loc[1, "pm25"] = -1
    results = validate_observations(frame, now=NOW)
    assert by_name(results)["range"].status == "quarantine"
    with pytest.raises(DataQualityError, match="range") as caught:
        raise_if_failed(results)
    assert caught.value.quarantined


def test_pm25_above_configured_ceiling_is_quarantined():
    frame = good_frame()
    frame.loc[2, "pm25"] = 400
    config = QualityConfig(pm25_ceiling=300)
    assert by_name(validate_observations(frame, NOW, config))["range"].status == "quarantine"
    assert by_name(validate_observations(frame, NOW))["range"].status == "pass"


def test_missing_weather_column_fails_schema_and_stops_early():
    results = validate_observations(good_frame().drop(columns=["wind_speed_10m"]), now=NOW)
    assert [result.name for result in results] == ["schema"]
    with pytest.raises(DataQualityError) as caught:
        raise_if_failed(results)
    assert not caught.value.quarantined


def test_non_numeric_column_fails_schema():
    frame = good_frame()
    frame["pm10"] = ["a", "b", "c"]
    assert by_name(validate_observations(frame, now=NOW))["schema"].status == "fail"


def test_small_null_share_warns_but_large_share_fails():
    frame = good_frame(rows=200)
    frame["ts_utc"] = pd.date_range("2025-12-23", periods=200, freq="h", tz="UTC")
    frame.loc[50, "pm25"] = None  # 0.5 % of rows: tolerated
    config = QualityConfig(max_null_share=0.01)
    now = pd.Timestamp("2026-01-01", tz="UTC")
    assert by_name(validate_observations(frame, now, config))["key_nulls"].status == "warn"
    frame.loc[60:70, "pm25"] = None  # about 6 %: too many
    assert by_name(validate_observations(frame, now, config))["key_nulls"].status == "fail"


def test_duplicate_timestamps_fail():
    frame = pd.concat([good_frame(), good_frame().iloc[[0]]], ignore_index=True)
    assert by_name(validate_observations(frame, now=NOW))["duplicates"].status == "fail"


def test_gap_size_decides_between_warn_and_fail():
    frame = good_frame(rows=10).drop(index=[4, 5])  # a 3 h gap
    assert by_name(validate_observations(frame, now=NOW))["continuity"].status == "warn"
    wide = good_frame(rows=20).drop(index=range(4, 12))  # a 9 h gap
    assert by_name(validate_observations(wide, now=NOW))["continuity"].status == "fail"


def test_stale_data_warns_within_two_intervals_rule():
    late = pd.Timestamp("2026-01-01 08:00", tz="UTC")  # newest row is 6 h old
    result = by_name(validate_observations(good_frame(), now=late))["freshness"]
    assert result.status == "warn"
    assert warnings_in(validate_observations(good_frame(), now=late))


def test_volume_outside_band_warns():
    too_few = validate_observations(good_frame(), NOW, expected_rows=10)
    on_target = validate_observations(good_frame(), NOW, expected_rows=3)
    assert by_name(too_few)["volume"].status == "warn"
    assert by_name(on_target)["volume"].status == "pass"
