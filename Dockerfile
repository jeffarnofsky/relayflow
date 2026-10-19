
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

RUN addgroup --system relayflow \
    && adduser --system --ingroup relayflow relayflow

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install \
    --no-cache-dir \
    --timeout=120 \
    --retries=3 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    .

COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /var/lib/celery \
    && chown -R relayflow:relayflow /app /var/lib/celery

USER relayflow

EXPOSE 8000

CMD ["uvicorn", "relayflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
