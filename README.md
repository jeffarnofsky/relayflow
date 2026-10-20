# RelayFlow

[![CI](https://github.com/jeffarnofsky/relayflow/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/jeffarnofsky/relayflow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

RelayFlow is a Python service for managing API integrations, executing
HTTP requests asynchronously, receiving signed webhooks, and notifying
authorized users through Telegram.

## Features

- REST API built with FastAPI.
- PostgreSQL persistence with SQLAlchemy and Alembic.
- Background execution with Celery and Redis.
- Integration run history and execution status tracking.
- HTTP endpoint validation and response size limits.
- Encrypted Bearer authentication configuration using Fernet.
- Signed incoming webhooks using HMAC-SHA256.
- Duplicate webhook event protection and subscription-based dispatch.
- Telegram notifications for completed integration runs.
- Health checks and operational summary endpoints.
- Docker Compose environment for local deployment.

## Architecture

```text
Client / Webhook Sender
          |
          v
       FastAPI
          |
          +------ PostgreSQL
          |
          +------ Redis Queue
                     |
                     v
                Celery Worker
                     |
                     +---- HTTP Integration
                     |
                     +---- Telegram

Celery Beat schedules maintenance and reconciliation tasks.
```

## Requirements

- Docker Engine and Docker Compose plugin
- Python 3.12 for local development
- Git

## Configuration

Create a local environment file:

```bash
cp .env.example .env
```

Edit `.env` and configure all required secrets and connection settings.

Important settings include:

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `DATABASE_URL_DOCKER`
- `REDIS_URL`
- `ADMIN_API_KEY`
- `FERNET_KEY`
- `WEBHOOK_SIGNING_SECRET`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`

For Docker Compose, `DATABASE_URL_DOCKER` must use the Compose service
hostname `postgres` and port `5432`. The host-facing PostgreSQL port is
`5433`.

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate random secrets for the admin API key and webhook signing key.
Never commit `.env` or paste real credentials into issue reports or logs.

## Run with Docker Compose

Build the images:

```bash
docker compose build
```

Start the application:

```bash
docker compose up -d
```

Check service status:

```bash
docker compose ps
```

Inspect service logs:

```bash
docker compose logs --tail=100 api worker beat
```

Alembic migrations run through the `migrate` service before the API and
Worker start.

Stop services without deleting persistent data:

```bash
docker compose down
```

Do not use `docker compose down -v` unless you intend to remove the
database, Redis, and Beat volumes.

## Production deployment

`compose.production.yaml` provides a production-oriented override for
a single host using a reverse proxy that runs on the host itself.

Before deployment:

- Create a production `.env` with unique, strong secrets.
- Set `ADMIN_API_KEY`, `FERNET_KEY`, and `WEBHOOK_SIGNING_SECRET`.
- Configure HTTPS termination in the host-level reverse proxy.
- Keep PostgreSQL and Redis inaccessible from public networks.
- Arrange database backups and test your recovery procedure.

Validate and deploy with the production override:

```bash
docker compose -f compose.yaml -f compose.production.yaml config --quiet
docker compose -f compose.yaml -f compose.production.yaml build
docker compose -f compose.yaml -f compose.production.yaml run --rm migrate
docker compose -f compose.yaml -f compose.production.yaml up -d
```

## API

The API is available locally at:

`http://127.0.0.1:8000`

Interactive API documentation:

`http://127.0.0.1:8000/docs`

### Health and operations

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | API liveness |
| GET | `/health/ready` | Database and Redis readiness |
| GET | `/health/worker` | Celery Worker availability |
| GET | `/api/v1/ops/summary` | Run and webhook status counts |

The operational summary endpoint requires the admin API key.

### Integrations

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/integrations` | Create an integration |
| GET | `/api/v1/integrations` | List integrations |
| GET | `/api/v1/integrations/{integration_id}` | Read an integration |
| PATCH | `/api/v1/integrations/{integration_id}` | Update an integration |
| DELETE | `/api/v1/integrations/{integration_id}` | Delete an integration |
| POST | `/api/v1/integrations/{integration_id}/runs` | Queue an execution |
| GET | `/api/v1/integrations/{integration_id}/runs` | List run history |
| GET | `/api/v1/integrations/{integration_id}/runs/{run_id}` | Read a run |

Administrative endpoints require `X-API-Key`.

### Webhooks

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/webhooks/{source}` | Receive a signed webhook |
| POST | `/api/v1/webhook-subscriptions` | Create a subscription |
| GET | `/api/v1/webhook-subscriptions` | List subscriptions |
| DELETE | `/api/v1/webhook-subscriptions/{subscription_id}` | Delete a subscription |

Incoming webhooks require an HMAC-SHA256 signature over the exact
request body. The signature header uses this format:

`X-Webhook-Signature: sha256=<hex-digest>`

Each event must include a unique `X-Webhook-Event-Id` for its source.
The request body must be a JSON object, and the signing secret must
be configured in the server environment.

## Local development

Create and activate a virtual environment, then install the project:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run static checks and tests:

```bash
ruff format .
ruff check .
pytest -q
```

For local execution, ensure PostgreSQL and Redis are running and the
local database URL uses the host-facing port configured in `.env`.

Apply database migrations before starting the local API:

```bash
alembic upgrade head
```

Run the API:

```bash
uvicorn relayflow.main:app --reload
```

Run the Celery Worker in a separate terminal:

```bash
celery -A relayflow.celery_app:celery_app worker --loglevel=INFO
```

Run Celery Beat in another terminal:

```bash
celery -A relayflow.celery_app:celery_app beat --loglevel=INFO
```

Do not run multiple Beat schedulers against the same schedule unless
the scheduler design explicitly supports that arrangement.

## Security notes

- Use unique, strong secrets in every environment.
- Never commit `.env`, API tokens, Fernet keys, or Telegram bot tokens.
- Rotate any credential that has appeared in a shared log.
- Do not store raw Bearer tokens in request headers persisted to the database.
- Bearer-authenticated integrations require HTTPS; plain HTTP requests with Bearer credentials are rejected.
- Use HTTPS for production-facing endpoints.
- Restrict outbound network access and review SSRF protections before
  exposing integration execution to untrusted users.
- Keep database and Redis ports private in production.
- Configure backups and a secret management solution before production use.

## License

RelayFlow is licensed under the MIT License. See [LICENSE](LICENSE).

## Current scope

RelayFlow is an evolving project. Validate the deployed configuration
and test failure and recovery scenarios before using it for critical
workloads or production automation.
