# Cloud runtime only; local model dependencies are intentionally not installed.
FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements-build.lock.txt requirements-runtime.lock.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-build.lock.txt \
    && python -m pip install --no-cache-dir --no-build-isolation --require-hashes -r requirements-runtime.lock.txt

COPY config.py domains.py document_library.py llms.py server.py alembic.ini ./
COPY agents/ ./agents/
COPY quiz/ ./quiz/
COPY retrieval/ ./retrieval/
COPY storage/ ./storage/
COPY data_ingest/ ./data_ingest/
COPY alembic/ ./alembic/
COPY static/ ./static/
COPY scripts/audit_db_readiness.py scripts/build_taxonomy.py scripts/import_to_db.py scripts/prepare_demo.py ./scripts/
COPY scripts/audit_flash_answers.py scripts/migrate_evaluation.py ./scripts/
COPY scripts/migrate_data_catalog.py ./scripts/

# Deployment supplies licensed assets and explicitly migrated storage at runtime.
VOLUME ["/app/data"]

EXPOSE 9528

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9528"]
