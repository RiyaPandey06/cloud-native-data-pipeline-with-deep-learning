from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, future=True, pool_pre_ping=True, connect_args=connect_args)


def make_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(database_url), autoflush=False, expire_on_commit=False)


def initialise_database(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername.startswith("sqlite") and url.database not in {None, ":memory:"}:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    _add_model_artifact_column_if_missing(engine)


def _add_model_artifact_column_if_missing(engine) -> None:
    """Apply the one additive schema upgrade used by the first project release."""
    columns = {column["name"] for column in inspect(engine).get_columns("model_registry")}
    if "artifact_blob" in columns:
        return
    column_type = "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"
    statement = text(f"ALTER TABLE model_registry ADD COLUMN artifact_blob {column_type}")
    with engine.begin() as connection:
        connection.execute(statement)
