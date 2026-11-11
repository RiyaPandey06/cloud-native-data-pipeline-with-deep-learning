from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class QualityConfig:
    """Thresholds for the data-quality gate.

    Every number here is a design choice, not a fact about the source. Calibrate the range
    ceilings on the first two weeks of data with ``scripts/calibrate_quality.py`` and override
    them through the environment variables read in :meth:`Settings.from_env`.
    """

    pm25_ceiling: float = 1_000.0
    pm10_ceiling: float = 1_500.0
    max_null_share: float = 0.01  # tolerated share of rows with a null timestamp or PM2.5
    gap_warn_hours: float = 2.0  # largest hourly gap that raises a warning
    gap_fail_hours: float = 6.0  # largest hourly gap that fails the run
    freshness_max_hours: float = 2.0  # "within 2 scheduling intervals" for an hourly schedule
    volume_min_share: float = 0.8  # rows received / rows expected for the requested window
    volume_max_share: float = 1.1


@dataclass(frozen=True)
class Settings:
    database_url: str
    location_id: str
    latitude: float
    longitude: float
    source: str
    backfill_days: int
    schedule_minutes: int
    artifact_dir: Path
    failure_webhook_url: str | None = None
    retrain_interval_hours: int = 168
    train_retry_minutes: int = 60
    overlap_hours: int = 48  # incremental runs re-fetch this much history to pick up revisions
    raw_retention_days: int = 30  # raw payloads older than this are purged after a clean run
    quality: QualityConfig = field(default_factory=QualityConfig)

    @classmethod
    def from_env(cls) -> Settings:
        schedule_minutes = int(os.getenv("SCHEDULE_MINUTES", "60"))
        defaults = QualityConfig()
        intervals = float(os.getenv("FRESHNESS_INTERVALS", "2"))
        quality = QualityConfig(
            pm25_ceiling=float(os.getenv("PM25_CEILING", defaults.pm25_ceiling)),
            pm10_ceiling=float(os.getenv("PM10_CEILING", defaults.pm10_ceiling)),
            max_null_share=float(os.getenv("MAX_NULL_SHARE", defaults.max_null_share)),
            gap_warn_hours=float(os.getenv("GAP_WARN_HOURS", defaults.gap_warn_hours)),
            gap_fail_hours=float(os.getenv("GAP_FAIL_HOURS", defaults.gap_fail_hours)),
            freshness_max_hours=intervals * schedule_minutes / 60,
            volume_min_share=float(os.getenv("VOLUME_MIN_SHARE", defaults.volume_min_share)),
            volume_max_share=float(os.getenv("VOLUME_MAX_SHARE", defaults.volume_max_share)),
        )
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/air_quality.db"),
            location_id=os.getenv("LOCATION_ID", "bengaluru"),
            latitude=float(os.getenv("LATITUDE", "12.9716")),
            longitude=float(os.getenv("LONGITUDE", "77.5946")),
            source=os.getenv("SOURCE", "open-meteo-cams"),
            backfill_days=int(os.getenv("BACKFILL_DAYS", "90")),
            schedule_minutes=schedule_minutes,
            retrain_interval_hours=int(os.getenv("RETRAIN_INTERVAL_HOURS", "168")),
            train_retry_minutes=int(os.getenv("TRAIN_RETRY_MINUTES", "60")),
            artifact_dir=Path(os.getenv("ARTIFACT_DIR", "artifacts")),
            failure_webhook_url=os.getenv("FAILURE_WEBHOOK_URL") or None,
            overlap_hours=int(os.getenv("OVERLAP_HOURS", "48")),
            raw_retention_days=int(os.getenv("RAW_RETENTION_DAYS", "30")),
            quality=quality,
        )
