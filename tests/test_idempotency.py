from datetime import UTC, datetime

from sqlalchemy import func, inspect, select, text

from air_quality_pipeline.db import initialise_database, make_engine, make_session_factory
from air_quality_pipeline.models import FeatureHourly, Observation, Prediction
from air_quality_pipeline.repository import (
    recent_predictions,
    reconcile_prediction_actuals,
    upsert_features,
    upsert_observations,
    upsert_predictions,
)


def test_reloading_a_window_does_not_create_duplicates(tmp_path):
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    initialise_database(url)
    session_factory = make_session_factory(url)
    row = {
        "location_id": "bengaluru",
        "ts_utc": datetime(2026, 1, 1, tzinfo=UTC),
        "source": "open-meteo-cams",
        "pm25": 12.0,
        "pm10": 20.0,
        "no2": 2.0,
        "ozone": 30.0,
        "temperature_2m": 23.0,
        "relative_humidity_2m": 60.0,
        "precipitation": 0.0,
        "wind_speed_10m": 4.0,
        "ingested_at": datetime.now(UTC),
    }
    with session_factory() as session:
        upsert_observations(session, [row])
        upsert_observations(session, [{**row, "pm25": 15.0}])
        session.commit()
        assert session.scalar(select(func.count()).select_from(Observation)) == 1
        assert session.scalar(select(Observation.pm25)) == 15.0


def test_feature_upsert_handles_json_values_column(tmp_path):
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    initialise_database(url)
    session_factory = make_session_factory(url)
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    with session_factory() as session:
        upsert_features(
            session,
            [
                {
                    "location_id": "bengaluru",
                    "ts_utc": timestamp,
                    "values": {"pm25_lag_1": 10.0},
                    "built_at": timestamp,
                }
            ],
        )
        upsert_features(
            session,
            [
                {
                    "location_id": "bengaluru",
                    "ts_utc": timestamp,
                    "values": {"pm25_lag_1": 11.0},
                    "built_at": timestamp,
                }
            ],
        )
        session.commit()
        row = session.scalar(select(FeatureHourly))
        assert row.values == {"pm25_lag_1": 11.0}


def test_arrived_observations_are_joined_to_prior_forecasts(tmp_path):
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    initialise_database(url)
    session_factory = make_session_factory(url)
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    target_ts = datetime(2026, 1, 1, 1, tzinfo=UTC)
    with session_factory() as session:
        upsert_predictions(
            session,
            [
                {
                    "model_version": "lstm-demo",
                    "location_id": "bengaluru",
                    "issued_at": issued_at,
                    "target_ts": target_ts,
                    "forecast_pm25": 10.0,
                    "actual_pm25": None,
                }
            ],
        )
        updated = reconcile_prediction_actuals(
            session,
            "bengaluru",
            [{"ts_utc": target_ts, "pm25": 12.5}],
        )
        session.commit()
        assert updated == 1
        assert session.scalar(select(Prediction.actual_pm25)) == 12.5


def test_recent_predictions_can_select_a_specific_model(tmp_path):
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    initialise_database(url)
    session_factory = make_session_factory(url)
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    target_ts = datetime(2026, 1, 1, 1, tzinfo=UTC)
    with session_factory() as session:
        for version, value in [("lstm-v1", 10.0), ("cnn-v1", 12.0)]:
            upsert_predictions(
                session,
                [
                    {
                        "model_version": version,
                        "location_id": "bengaluru",
                        "issued_at": issued_at,
                        "target_ts": target_ts,
                        "forecast_pm25": value,
                        "actual_pm25": None,
                    }
                ],
            )
        session.commit()
        latest = recent_predictions(session, "bengaluru", 24, model_version="cnn-v1")
        assert [item.model_version for item in latest] == ["cnn-v1"]
        assert latest[0].forecast_pm25 == 12.0


def test_database_initialisation_applies_artifact_column_upgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = make_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE model_registry (model_version TEXT PRIMARY KEY)"))
    initialise_database(url)
    columns = {column["name"] for column in inspect(engine).get_columns("model_registry")}
    assert "artifact_blob" in columns
