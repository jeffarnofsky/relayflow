# RelayFlow

A Python platform for API integrations and workflow automation.

RelayFlow is designed to connect external APIs, execute background
jobs, process webhooks, store execution history, and send notifications
through Telegram.

## Planned Features

- REST API built with FastAPI
- PostgreSQL persistence
- API integration management
- Asynchronous task execution with Celery and Redis
- Retry and execution history
- Webhook ingestion and signature verification
- Telegram bot for operational commands
- Automated testing
- Docker Compose
- GitHub Actions CI

## Tech Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Alembic
- HTTPX
- Celery
- Redis
- python-telegram-bot
- Pytest
- Docker

## Development

Create and activate a virtual environment, then install the project:

    python -m venv .venv
    source .venv/bin/activate
    python -m pip install -e ".[dev]"

Run the API:

    uvicorn relayflow.main:app --reload

Run tests:

    pytest -q

## Project Status

Early development. Features will be implemented incrementally.