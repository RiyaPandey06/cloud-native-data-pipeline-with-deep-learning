FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
RUN pip install --upgrade pip && pip install --extra-index-url https://download.pytorch.org/whl/cpu .
# Run as an unprivileged user; artifacts is the only writable path the app needs.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/artifacts && chown -R app /app/artifacts
USER app

CMD ["uvicorn", "air_quality_pipeline.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
