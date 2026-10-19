from relayflow.api.routes.ops import operational_summary


class FakeResult:
    def all(self):
        return []


class FakeSession:
    def execute(self, statement):
        return FakeResult()

    def scalar(self, statement):
        return None


def test_operational_summary_includes_zero_counts() -> None:
    result = operational_summary(db=FakeSession())

    assert result["runs_by_status"] == {
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
    }

    assert result["webhook_events_by_status"] == {
        "received": 0,
        "processing": 0,
        "processed": 0,
        "failed": 0,
    }

    assert result["oldest_running_run_age_seconds"] is None
    assert result["oldest_unprocessed_webhook_age_seconds"] is None
    assert "generated_at" in result
