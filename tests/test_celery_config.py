from relayflow.celery_app import celery_app


def test_beat_schedule_registers_webhook_reconciler() -> None:
    entry = celery_app.conf.beat_schedule["relayflow-reconcile-stale-webhooks"]

    assert entry["task"] == "relayflow.reconcile_stale_webhook_events"
    assert entry["schedule"] == 60.0


def test_beat_schedule_registers_stale_run_reconciler() -> None:
    entry = celery_app.conf.beat_schedule["relayflow-reconcile-stale-runs"]

    assert entry["task"] == "relayflow.reconcile_stale_runs"
    assert entry["schedule"] == 60.0
