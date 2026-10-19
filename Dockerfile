
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system relayflow \
    && adduser --system --ingroup relayflow relayflow

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /var/lib/celery \
    && chown -R relayflow:relayflow /app /var/lib/celery

USER relayflow

EXPOSE 8000

CMD ["uvicorn", "relayflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
