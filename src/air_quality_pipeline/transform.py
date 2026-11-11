from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd


def response_to_frame(air_payload: dict, weather_payload: dict) -> pd.DataFrame:
    air = pd.DataFrame(air_payload["hourly"])
    weather = pd.DataFrame(weather_payload["hourly"])
    if "time" not in air or "time" not in weather:
        raise ValueError("Both API payloads must include hourly.time")
    frame = air.merge(weather, on="time", how="inner", validate="one_to_one")
    frame = frame.rename(
        columns={
            "time": "ts_utc",
            "pm2_5": "pm25",
            "nitrogen_dioxide": "no2",
            "relative_humidity_2m": "relative_humidity_2m",
        }
    )
    frame["ts_utc"] = pd.to_datetime(frame["ts_utc"], utc=True)
    numeric = [column for column in frame.columns if column != "ts_utc"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    return frame.sort_values("ts_utc").reset_index(drop=True)


@dataclass(frozen=True)
class PreparedFrame:
    frame: pd.DataFrame
    future_rows_dropped: int  # hours after "now": the API also returns model forecasts
    late_rows_dropped: int  # newest hours whose PM2.5 has not been published yet


def prepare_frame(frame: pd.DataFrame, now: datetime | None = None) -> PreparedFrame:
    """Keep only hours that can be treated as observations.

    Open-Meteo returns the whole requested day, including future model forecasts, and the newest
    hours can still be empty while the source catches up. Neither is a data problem, so both are
    trimmed here (and counted) instead of tripping the quality gate. Nulls in the *middle* of
    the window are left alone so the gate can judge them.
    """
    now_ts = pd.Timestamp(now or datetime.now(UTC))
    now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts.tz_convert("UTC")
    cutoff = now_ts.floor("h")
    ordered = frame.sort_values("ts_utc").reset_index(drop=True)
    in_past = ordered["ts_utc"] <= cutoff
    future_rows = int((~in_past).sum())
    ordered = ordered.loc[in_past].reset_index(drop=True)
    late_rows = 0
    if len(ordered):
        published = ordered["pm25"].notna() if "pm25" in ordered else pd.Series(dtype=bool)
        if published.any():
            last_published = published[published].index[-1]
            late_rows = len(ordered) - 1 - int(last_published)
            ordered = ordered.iloc[: last_published + 1].reset_index(drop=True)
        else:
            late_rows = len(ordered)
            ordered = ordered.iloc[0:0]
    return PreparedFrame(ordered, future_rows, late_rows)


def expected_row_count(start: date, end: date, now: datetime | None = None) -> int:
    """Hourly rows the requested window should hold, capped at the last complete hour."""
    now_ts = pd.Timestamp(now or datetime.now(UTC))
    now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts.tz_convert("UTC")
    first = pd.Timestamp(start, tz="UTC")
    last = min(pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23), now_ts.floor("h"))
    return max(int((last - first) / pd.Timedelta(hours=1)) + 1, 0)


def build_features(observations: pd.DataFrame) -> pd.DataFrame:
    frame = observations.sort_values("ts_utc").copy()
    frame["hour"] = frame.ts_utc.dt.hour
    frame["day_of_week"] = frame.ts_utc.dt.day_of_week
    frame["month"] = frame.ts_utc.dt.month
    for lag in (1, 2, 3, 24, 48, 168):
        frame[f"pm25_lag_{lag}"] = frame.pm25.shift(lag)
    for window in (3, 24, 168):
        frame[f"pm25_rolling_mean_{window}"] = frame.pm25.shift(1).rolling(window).mean()
        frame[f"pm25_rolling_std_{window}"] = frame.pm25.shift(1).rolling(window).std()
    frame["feature_built_at"] = datetime.now(UTC)
    return frame
