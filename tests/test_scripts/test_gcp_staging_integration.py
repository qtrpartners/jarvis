from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.gcp_staging_integration import (
    CommandResult,
    IntegrationConfig,
    IntegrationError,
    StagingIntegration,
    resolve_release,
)

DIGEST = f"sha256:{'a' * 64}"
IMAGE = f"us-central1-docker.pkg.dev/jarvis-prod/research/base@{DIGEST}"
COMMIT = "b" * 40


def write_release(root: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "schema_version": 1,
        "result": "pass",
        "source": {
            "repository": "qtrpartners/jarvis",
            "ref": "refs/heads/main",
            "commit": COMMIT,
        },
        "image": {
            "cloud": "gcp",
            "name": "us-central1-docker.pkg.dev/jarvis-prod/research/base",
            "digest": DIGEST,
            "reference": IMAGE,
        },
    }
    payload.update(overrides)
    path = root / "artifact" / "release.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))
    return path


def config(tmp_path: Path) -> IntegrationConfig:
    return IntegrationConfig(
        project="jarvis-stage",
        region="us-central1",
        data_uri="gs://jarvis-stage-data",
        scratch_uri="gs://jarvis-stage-scratch",
        airflow_logs_uri="gs://jarvis-stage-airflow-logs",
        image_reference=IMAGE,
        source_commit=COMMIT,
        release_run_id="35293363890",
        job_service_account="jarvis-job@jarvis-stage.iam.gserviceaccount.com",
        run_id="12345678-1",
        logical_date=datetime(2026, 9, 18, 1, 2, 3, tzinfo=UTC),
        evidence_dir=tmp_path / "evidence",
    )


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.log_calls = 0

    def run(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> CommandResult:
        self.commands.append(command)
        joined = " ".join(command)
        if command[:3] == ["gcloud", "projects", "describe"]:
            return CommandResult(0, '{"projectId":"jarvis-stage"}', "")
        if command[:3] == ["gcloud", "storage", "cat"]:
            if "probe_id=p43-12345678-1-failure" in command[-1]:
                return CommandResult(1, "", "not found")
            if command[-1].endswith("/_SUCCESS"):
                return CommandResult(0, '{"rows":1,"probe_id":"success"}', "")
            return CommandResult(0, '{"result":"pass"}', "")
        if command[:4] == ["gcloud", "storage", "ls", "--recursive"]:
            if "dag_id=staging_integration" not in command[-1]:
                return CommandResult(1, "", "not found")
            self.log_calls += 1
            outputs = {
                1: "",
                2: "gs://jarvis-stage-airflow-logs/success.log\n",
                3: "gs://jarvis-stage-airflow-logs/success.log\n",
                4: (
                    "gs://jarvis-stage-airflow-logs/success.log\n"
                    "gs://jarvis-stage-airflow-logs/failure.log\n"
                ),
            }
            return CommandResult(0, outputs[self.log_calls], "")
        if command[:3] == ["airflow", "dags", "test"]:
            if '"mode":"failure"' in joined:
                return CommandResult(1, "", "DagRun failed")
            return CommandResult(0, "DagRun state=success", "")
        return CommandResult(0, "", "")


def test_resolve_release_accepts_one_passing_immutable_manifest(tmp_path: Path) -> None:
    path = write_release(tmp_path)

    release = resolve_release(tmp_path, "qtrpartners/jarvis")

    assert release.commit == COMMIT
    assert release.reference == IMAGE
    assert release.evidence_path == path


def test_resolve_release_rejects_failed_or_ambiguous_evidence(tmp_path: Path) -> None:
    write_release(tmp_path, result="failed")
    with pytest.raises(IntegrationError, match="not a passing"):
        resolve_release(tmp_path, "qtrpartners/jarvis")

    write_release(tmp_path / "second")
    with pytest.raises(IntegrationError, match="exactly one"):
        resolve_release(tmp_path, "qtrpartners/jarvis")


def test_config_rejects_production_execution(tmp_path: Path) -> None:
    values = config(tmp_path)
    production = IntegrationConfig(**{**values.__dict__, "project": "jarvis-prod"})
    with pytest.raises(IntegrationError, match="ending in -stage"):
        production.validate()


def test_integration_proves_success_failure_logs_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIRFLOW_HOME", str(tmp_path / "airflow"))
    runner = FakeRunner()

    evidence, path = StagingIntegration(
        config(tmp_path), runner, sleeper=lambda _: None, log_attempts=1
    ).run()

    assert evidence["result"] == "pass"
    assert evidence["source_commit"] == COMMIT
    assert evidence["release_run_id"] == "35293363890"
    assert evidence["success"]["dag_state"] == "success"
    assert evidence["success"]["completion_marker"]["rows"] == 1
    assert evidence["controlled_failure"] == {
        "dag_state": "failed",
        "probe_id": "p43-12345678-1-failure",
        "partition_absent": True,
        "airflow_remote_logs": ["gs://jarvis-stage-airflow-logs/failure.log"],
    }
    assert evidence["cleanup"]["status"] == "pass"
    assert json.loads(path.read_text())["result"] == "pass"
    assert any(command[:4] == ["gcloud", "run", "jobs", "create"] for command in runner.commands)
    assert any(command[:4] == ["gcloud", "run", "jobs", "delete"] for command in runner.commands)
