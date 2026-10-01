from __future__ import annotations

from datetime import date

from jobs.common import storage
from jobs.common.config import get_settings
from jobs.common.harness import main
from jobs.staging_probe import CONTROLLED_FAILURE, run


def configure(monkeypatch, *, mode: str, probe_id: str, environment: str = "stage") -> None:
    monkeypatch.setenv("RP_ENV", environment)
    monkeypatch.setenv("RP_STAGING_PROBE_MODE", mode)
    monkeypatch.setenv("RP_STAGING_PROBE_ID", probe_id)
    get_settings.cache_clear()


def test_success_writes_payload_then_completion_marker(monkeypatch) -> None:
    configure(monkeypatch, mode="success", probe_id="probe-success")

    assert main("staging_probe", run, ["--date", "2026-08-11", "--run-id", "run-1"]) == 0

    prefix = storage.integration_prefix(
        "staging_probe", date(2026, 8, 11), "probe-success"
    )
    assert storage.read_json(f"{prefix}/result.json")["result"] == "pass"
    marker = storage.read_json(f"{prefix}/_SUCCESS")
    assert marker["probe_id"] == "probe-success"
    assert marker["rows"] == 1


def test_controlled_failure_is_loud_and_writes_no_marker(monkeypatch, caplog) -> None:
    configure(monkeypatch, mode="failure", probe_id="probe-failure")

    assert main("staging_probe", run, ["--date", "2026-08-11", "--run-id", "run-2"]) == 1

    prefix = storage.integration_prefix(
        "staging_probe", date(2026, 8, 11), "probe-failure"
    )
    assert not storage.is_complete(prefix)
    assert any(
        CONTROLLED_FAILURE in getattr(record, "trace", "")
        for record in caplog.records
    )


def test_probe_refuses_non_staging_environment(monkeypatch) -> None:
    configure(monkeypatch, mode="success", probe_id="probe-dev", environment="dev")

    assert main("staging_probe", run, ["--date", "2026-08-11"]) == 1
