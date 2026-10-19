import logging

from fastapi import APIRouter, HTTPException, status
from redis import Redis
from sqlalchemy import text

from relayflow.celery_app import celery_app
from relayflow.core.config import settings
from relayflow.db.session import engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health")
def liveness_check() -> dict[str, str]:
    """Confirm that the API process can serve requests."""
    return {"status": "ok"}


def _database_ready() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database health check failed.")
        return False


def _redis_ready() -> bool:
    client = None

    try:
        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        return bool(client.ping())
    except Exception:
        logger.exception("Redis health check failed.")
        return False
    finally:
        if client is not None:
            client.close()


def _worker_count() -> int:
    try:
        replies = celery_app.control.ping(timeout=0.5)
        return sum(1 for reply in replies if isinstance(reply, dict) and reply)
    except Exception:
        logger.exception("Celery worker health check failed.")
        return 0


@router.get("/health/ready")
def readiness_check() -> dict[str, object]:
    """Check whether required infrastructure is reachable."""
    checks = {
        "database": _database_ready(),
        "redis": _redis_ready(),
    }

    if not all(checks.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                "checks": checks,
            },
        )

    return {
        "status": "ready",
        "checks": checks,
    }


@router.get("/health/worker")
def worker_health_check() -> dict[str, int | str]:
    """Check whether at least one Celery worker responds to ping."""
    workers = _worker_count()

    if workers == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "worker_unavailable",
                "workers_available": 0,
            },
        )

    return {
        "status": "ready",
        "workers_available": workers,
    }
