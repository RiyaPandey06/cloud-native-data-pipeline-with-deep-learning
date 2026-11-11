# Local-only image used to show the pipeline as an Airflow DAG (screenshots / demo video).
FROM apache/airflow:2.10.5-python3.11

USER root
COPY --chown=airflow:root pyproject.toml README.md /opt/project/
COPY --chown=airflow:root src /opt/project/src
COPY --chown=airflow:root scripts /opt/project/scripts
RUN python -m venv /opt/venv && chown -R airflow:root /opt/venv

USER airflow
# The project lives in its own virtualenv so its pins never clash with Airflow's SQLAlchemy 1.4.
RUN /opt/venv/bin/pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu /opt/project
