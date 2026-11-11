from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import QualityConfig

# Status vocabulary
#   pass        nothing to report
#   warn        batch is loaded, an alert is sent
#   quarantine  batch is stored raw only; nothing reaches the cleaned or feature layers
#   fail        run fails; nothing reaches the cleaned or feature layers
BLOCKING = {"fail", "quarantine"}

CORE_COLUMNS = ["ts_utc", "pm25", "pm10", "no2", "ozone"]
WEATHER_COLUMNS = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m"]
REQUIRED_COLUMNS = set(CORE_COLUMNS) | set(WEATHER_COLUMNS)
NUMERIC_COLUMNS = [column for column in CORE_COLUMNS if column != "ts_utc"] + WEATHER_COLUMNS


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    observed_value: float | None = None


class DataQualityError(RuntimeError):
    def __init__(self, results: list[CheckResult]):
        self.results = results
        blocking = [r for r in results if r.status in BLOCKING]
        failures = "; ".join(f"{r.name}: {r.detail}" for r in blocking)
        super().__init__(f"Data quality gate failed: {failures}")

    @property
    def quarantined(self) -> bool:
        """True when the batch was held back without any hard failure."""
        statuses = {r.status for r in self.results}
        return "quarantine" in statuses and "fail" not in statuses


def validate_observations(
    frame: pd.DataFrame,
    now: pd.Timestamp | None = None,
    config: QualityConfig | None = None,
    expected_rows: int | None = None,
) -> list[CheckResult]:
    config = config or QualityConfig()
    now = now or pd.Timestamp.now(tz="UTC")
    results: list[CheckResult] = []

    # Schema: columns present and numeric.
    missing = REQUIRED_COLUMNS - set(frame.columns)
    non_numeric = [
        column
        for column in NUMERIC_COLUMNS
        if column in frame.columns and not pd.api.types.is_numeric_dtype(frame[column])
    ]
    schema_problems = []
    if missing:
        schema_problems.append(f"missing columns: {sorted(missing)}")
    if non_numeric:
        schema_problems.append(f"non-numeric columns: {sorted(non_numeric)}")
    results.append(
        CheckResult(
            "schema",
            "fail" if schema_problems else "pass",
            "; ".join(schema_problems) if schema_problems else "required columns present",
        )
    )
    if schema_problems:
        return results

    # Key nulls: a small share is tolerated (the rows are dropped before load); more fails.
    null_mask = frame[["ts_utc", "pm25"]].isna().any(axis=1)
    null_share = float(null_mask.mean()) if len(frame) else 1.0
    if null_share > config.max_null_share:
        null_status = "fail"
    elif null_share > 0:
        null_status = "warn"
    else:
        null_status = "pass"
    results.append(
        CheckResult(
            "key_nulls",
            null_status,
            f"key/target null share={null_share:.2%} (tolerated {config.max_null_share:.2%})",
            null_share,
        )
    )

    # Everything below looks only at rows that will actually be loaded.
    usable = frame.loc[~null_mask]

    # Range: implausible values quarantine the whole batch.
    invalid_range = int(
        (
            (usable["pm25"] < 0)
            | (usable["pm25"] > config.pm25_ceiling)
            | (usable["pm10"] < 0)
            | (usable["pm10"] > config.pm10_ceiling)
        ).sum()
    )
    results.append(
        CheckResult(
            "range",
            "quarantine" if invalid_range else "pass",
            f"invalid pollutant rows={invalid_range} "
            f"(ceilings pm2.5={config.pm25_ceiling:g}, pm10={config.pm10_ceiling:g})",
            float(invalid_range),
        )
    )

    duplicate_count = int(usable.duplicated(["ts_utc"]).sum())
    results.append(
        CheckResult(
            "duplicates",
            "fail" if duplicate_count else "pass",
            f"duplicate timestamps={duplicate_count}",
            float(duplicate_count),
        )
    )

    # Continuity: warn or fail depending on the size of the largest gap.
    ordered = pd.to_datetime(usable["ts_utc"], utc=True).sort_values()
    gaps_hours = ordered.diff().dropna().dt.total_seconds() / 3600
    largest_gap = float(gaps_hours.max()) if len(gaps_hours) else 0.0
    if largest_gap > config.gap_fail_hours:
        gap_status = "fail"
    elif largest_gap > config.gap_warn_hours:
        gap_status = "warn"
    else:
        gap_status = "pass"
    results.append(
        CheckResult(
            "continuity",
            gap_status,
            f"largest gap={largest_gap:.0f}h (warn>{config.gap_warn_hours:g}h, "
            f"fail>{config.gap_fail_hours:g}h)",
            largest_gap,
        )
    )

    newest = ordered.max() if len(ordered) else pd.NaT
    stale_hours = float((now - newest).total_seconds() / 3600) if pd.notna(newest) else float("inf")
    results.append(
        CheckResult(
            "freshness",
            "warn" if stale_hours > config.freshness_max_hours else "pass",
            f"newest row age={stale_hours:.1f}h (limit {config.freshness_max_hours:g}h)",
            stale_hours,
        )
    )

    # Volume: compare with the rows the requested window should contain.
    rows = len(usable)
    if expected_rows:
        share = rows / expected_rows
        in_band = config.volume_min_share <= share <= config.volume_max_share
        results.append(
            CheckResult(
                "volume",
                "pass" if in_band else "warn",
                f"rows={rows} of ~{expected_rows} expected ({share:.0%}; band "
                f"{config.volume_min_share:.0%}-{config.volume_max_share:.0%})",
                float(rows),
            )
        )
    else:
        results.append(
            CheckResult("volume", "warn" if rows < 1 else "pass", f"rows={rows}", float(rows))
        )
    return results


def raise_if_failed(results: list[CheckResult]) -> None:
    if any(item.status in BLOCKING for item in results):
        raise DataQualityError(results)


def warnings_in(results: list[CheckResult]) -> list[CheckResult]:
    return [item for item in results if item.status == "warn"]
