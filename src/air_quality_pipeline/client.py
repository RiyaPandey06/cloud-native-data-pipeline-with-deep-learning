from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


class SourceFetchError(RuntimeError):
    pass


def _retry_after_seconds(response: requests.Response) -> float | None:
    if response.status_code != 429:
        return None
    try:
        return min(float(response.headers.get("Retry-After", "")), 120.0)
    except ValueError:
        return None


@dataclass
class OpenMeteoClient:
    session: requests.Session | None = None
    retries: int = 4
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        self.session = self.session or requests.Session()

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            retry_after: float | None = None
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                retry_after = _retry_after_seconds(response)
                response.raise_for_status()
                payload = response.json()
                if "hourly" not in payload:
                    raise SourceFetchError("Source response has no hourly object")
                return payload
            except (requests.RequestException, ValueError, SourceFetchError) as exc:
                last_error = exc
                if attempt == self.retries - 1:
                    break
                # Exponential backoff with jitter; honour Retry-After when the source sends it.
                delay = max(2**attempt + random.uniform(0, 1), retry_after or 0)
                LOGGER.warning(
                    "source request failed (attempt %d/%d): %s", attempt + 1, self.retries, exc
                )
                time.sleep(delay)
        raise SourceFetchError(
            f"Open-Meteo request failed after {self.retries} attempts: {last_error}"
        )

    def fetch(
        self, latitude: float, longitude: float, start: date, end: date
    ) -> dict[str, tuple[str, dict, dict]]:
        common = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "UTC",
        }
        air_params = {**common, "hourly": "pm2_5,pm10,nitrogen_dioxide,ozone"}
        weather_params = {
            **common,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        }
        return {
            "air_quality": (AIR_QUALITY_URL, air_params, self._get(AIR_QUALITY_URL, air_params)),
            "weather": (WEATHER_URL, weather_params, self._get(WEATHER_URL, weather_params)),
        }
