"""Hourly air-quality DAG: start -> ingest -> validate -> load -> score.

Airflow runs it locally (docker-compose.airflow.yml) so the orchestration can be shown in
screenshots; the live deployment uses the lighter cron-style worker / Cloud Run job, which calls
the very same Pipeline stages (see docs/DESIGN_DECISIONS.md).

Each task shells out to ``scripts/run_stage.py`` inside the project's own virtualenv. That keeps
the project's SQLAlchemy 2 / pandas / PyTorch pins away from Airflow's own dependencies.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.operators.bash import BashOperator

from airflow import DAG

PYTHON = "/opt/venv/bin/python"
STAGE = "{python} /opt/project/scripts/run_stage.py {stage}"
CONTEXT = " --context '{{ ti.xcom_pull(task_ids=\"start_run\") }}'"
ATTEMPT = " --attempt {{ ti.try_number }} --max-attempts {{ ti.max_tries + 1 }}"


def stage(task_id: str, name: str, retries: int, **kwargs) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=STAGE.format(python=PYTHON, stage=name) + CONTEXT + ATTEMPT,
        retries=retries,
        **kwargs,
    )


with DAG(
    dag_id="air_quality_hourly",
    description="Ingest, validate, load and score hourly PM2.5 data",
    schedule="5 * * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,  # the pipeline's incremental window already re-covers missed hours
    max_active_runs=1,
    default_args={
        "owner": "data-engineering",
        "retry_delay": timedelta(minutes=2),
        "retry_exponential_backoff": True,
    },
    tags=["air-quality", "pipeline"],
) as dag:
    start_run = BashOperator(
        task_id="start_run",
        bash_command=f"{PYTHON} /opt/project/scripts/run_stage.py start",
        retries=2,
    )
    ingest = stage("ingest", "ingest", retries=3)  # the source API is the flaky part
    validate = stage("validate", "validate", retries=0)  # a bad batch stays bad
    load = stage("load", "load", retries=2)
    score = stage("score", "score", retries=1)

    start_run >> ingest >> validate >> load >> score
