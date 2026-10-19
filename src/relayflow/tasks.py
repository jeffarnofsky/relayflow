from relayflow.celery_app import celery_app


@celery_app.task(name="relayflow.health_check")
def worker_health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "relayflow-worker",
    }
