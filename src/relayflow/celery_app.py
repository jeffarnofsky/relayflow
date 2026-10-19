from celery import Celery

from relayflow.core.config import settings

celery_app = Celery(
    "relayflow",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["relayflow.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=False,
    result_expires=3600,
    task_reject_on_worker_lost=True,
)

task_reject_on_worker_lost = (True,)
beat_schedule = (
    {
        "relayflow-reconcile-stale-runs": {
            "task": "relayflow.reconcile_stale_runs",
            "schedule": 60.0,
        },
    },
)
