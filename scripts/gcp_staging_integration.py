"""Run the P4.3 staging DAG gate against an ephemeral GCP Cloud Run Job."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]-stage$")
REGION_PATTERN = re.compile(r"^[a-z]+-[a-z0-9]+[0-9]$")
DIGEST_IMAGE_PATTERN = re.compile(
    r"^[a-z0-9-]+-docker\.pkg\.dev/"
    r"[a-z][a-z0-9-]{4,28}[a-z0-9]/[a-z0-9._-]+/[a-z0-9._/-]+"
    r"@sha256:[0-9a-f]{64}$"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SAFE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,62}$")
DAG_ID = "staging_integration"
PROBE_DATASET = "staging_probe"


class IntegrationError(RuntimeError):
    """A fail-closed P4.3 integration error."""


@dataclass(frozen=True)
class ReleaseSubject:
    commit: str
    image: str
    digest: str
    reference: str
    evidence_path: Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def run(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> CommandResult:
        print("$ " + shlex.join(command), flush=True)
        completed = subprocess.run(command, capture_output=True, text=True, env=env)
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        if check and result.returncode:
            raise IntegrationError(
                f"command failed with exit {result.returncode}: {shlex.join(command)}"
            )
        return result


def _load_json(value: str, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        raise IntegrationError(f"{description} did not return valid JSON") from None
    if not isinstance(payload, dict):
        raise IntegrationError(f"{description} must be a JSON object")
    return payload


def resolve_release(evidence_dir: Path, repository: str) -> ReleaseSubject:
    manifests = sorted(evidence_dir.rglob("release.json"))
    if len(manifests) != 1:
        raise IntegrationError(
            f"expected exactly one release.json, found {len(manifests)}"
        )
    payload = _load_json(manifests[0].read_text(), "release evidence")
    source = payload.get("source")
    image = payload.get("image")
    if payload.get("schema_version") != 1 or payload.get("result") != "pass":
        raise IntegrationError("release evidence is not a passing schema v1 manifest")
    if not isinstance(source, dict) or not isinstance(image, dict):
        raise IntegrationError("release evidence is missing source or image metadata")
    commit = source.get("commit")
    name = image.get("name")
    digest = image.get("digest")
    reference = image.get("reference")
    if source.get("repository") != repository or source.get("ref") != "refs/heads/main":
        raise IntegrationError("release evidence does not belong to this repository main branch")
    if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
        raise IntegrationError("release evidence has an invalid source commit")
    if image.get("cloud") != "gcp" or not all(
        isinstance(value, str) for value in (name, digest, reference)
    ):
        raise IntegrationError("release evidence is not a GCP image")
    assert isinstance(name, str) and isinstance(digest, str) and isinstance(reference, str)
    if reference != f"{name}@{digest}" or not DIGEST_IMAGE_PATTERN.fullmatch(reference):
        raise IntegrationError(
            "release evidence image is not an immutable Artifact Registry digest"
        )
    return ReleaseSubject(commit, name, digest, reference, manifests[0])


def write_github_output(path: Path, release: ReleaseSubject) -> None:
    with path.open("a") as handle:
        handle.write(f"release_commit={release.commit}\n")
        handle.write(f"image_name={release.image}\n")
        handle.write(f"image_digest={release.digest}\n")
        handle.write(f"image_reference={release.reference}\n")


@dataclass(frozen=True)
class IntegrationConfig:
    project: str
    region: str
    data_uri: str
    scratch_uri: str
    airflow_logs_uri: str
    image_reference: str
    source_commit: str
    release_run_id: str
    job_service_account: str
    run_id: str
    logical_date: datetime
    evidence_dir: Path

    def validate(self) -> None:
        if not PROJECT_PATTERN.fullmatch(self.project):
            raise IntegrationError("project must be a valid ID ending in -stage")
        if not REGION_PATTERN.fullmatch(self.region):
            raise IntegrationError("region must be a valid GCP region")
        uris = (self.data_uri, self.scratch_uri, self.airflow_logs_uri)
        if any(not re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]+", uri) for uri in uris):
            raise IntegrationError("storage locations must be bucket-root gs:// URIs")
        if len(set(uris)) != 3:
            raise IntegrationError("data, scratch, and Airflow log buckets must be distinct")
        if not DIGEST_IMAGE_PATTERN.fullmatch(self.image_reference):
            raise IntegrationError("image must be an immutable Artifact Registry digest")
        if not COMMIT_PATTERN.fullmatch(self.source_commit):
            raise IntegrationError("source commit must be a full lowercase Git SHA")
        if not self.release_run_id.isdigit():
            raise IntegrationError("release run ID must be numeric")
        project_in_image = self.image_reference.split("/", 2)[1]
        if project_in_image == self.project:
            pass
        elif not project_in_image.endswith("-prod"):
            raise IntegrationError("image must come from the staging or production GCP project")
        if not re.fullmatch(
            rf"[a-z][a-z0-9-]{{4,28}}@{re.escape(self.project)}\.iam\.gserviceaccount\.com",
            self.job_service_account,
        ):
            raise IntegrationError("job service account must belong to the staging project")
        if not SAFE_ID_PATTERN.fullmatch(self.run_id):
            raise IntegrationError("run ID must be 8-63 lowercase letters, digits, or hyphens")
        if self.logical_date.tzinfo is None:
            raise IntegrationError("logical date must include a timezone")

    @property
    def job_name(self) -> str:
        return f"jarvis-p43-{self.run_id}"[:63].rstrip("-")

    def probe_id(self, mode: str) -> str:
        return f"p43-{self.run_id}-{mode}"[:63].rstrip("-")

    def prefix(self, mode: str) -> str:
        probe_id = self.probe_id(mode)
        return (
            f"{self.data_uri}/integration/{PROBE_DATASET}"
            f"/dt={self.logical_date.date().isoformat()}/probe_id={probe_id}"
        )


class StagingIntegration:
    def __init__(
        self,
        config: IntegrationConfig,
        runner: CommandRunner,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        log_attempts: int = 24,
    ) -> None:
        config.validate()
        self.config = config
        self.runner = runner
        self.sleeper = sleeper
        self.log_attempts = log_attempts

    def _gcloud(self, *args: str) -> list[str]:
        return ["gcloud", *args]

    def _airflow_env(self) -> dict[str, str]:
        airflow_home = os.environ.get("AIRFLOW_HOME", "")
        if not airflow_home:
            raise IntegrationError("AIRFLOW_HOME is required")
        env = dict(os.environ)
        env.update(
            {
                "AIRFLOW__CORE__DAGS_FOLDER": str(Path.cwd() / "dags"),
                "AIRFLOW__CORE__LOAD_EXAMPLES": "False",
                "AIRFLOW__CORE__EXECUTOR": "SequentialExecutor",
                "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": (
                    f"sqlite:///{Path(airflow_home).resolve() / 'airflow.db'}"
                ),
                "AIRFLOW__LOGGING__REMOTE_LOGGING": "True",
                "AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER": self.config.airflow_logs_uri,
                "AIRFLOW__LOGGING__REMOTE_LOG_CONN_ID": "google_cloud_default",
                "RP_ENV": "stage",
                "RP_CLOUD": "gcp",
                "RP_PROJECT_ID": self.config.project,
                "RP_REGION": self.config.region,
                "RP_STORAGE_URI": self.config.data_uri,
                "RP_SCRATCH_URI": self.config.scratch_uri,
                "RP_BATCH_JOB_NAME": self.config.job_name,
            }
        )
        return env

    def _list_remote_logs(self) -> set[str]:
        prefix = f"{self.config.airflow_logs_uri}/dag_id={DAG_ID}/"
        result = self.runner.run(
            self._gcloud("storage", "ls", "--recursive", prefix), check=False
        )
        if result.returncode not in {0, 1}:
            raise IntegrationError("Airflow remote log listing failed")
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _wait_for_new_logs(self, previous: set[str]) -> list[str]:
        for _ in range(self.log_attempts):
            current = self._list_remote_logs()
            created = sorted(current - previous)
            if created:
                return created
            self.sleeper(5)
        raise IntegrationError("Airflow produced no new remote task log objects")

    def _run_dag(self, mode: str, logical_date: datetime) -> CommandResult:
        conf = json.dumps(
            {"mode": mode, "probe_id": self.config.probe_id(mode)}, separators=(",", ":")
        )
        return self.runner.run(
            [
                "airflow",
                "dags",
                "test",
                DAG_ID,
                logical_date.isoformat(),
                "--dagfile-path",
                "dags/staging_integration.py",
                "--conf",
                conf,
            ],
            env=self._airflow_env(),
            check=False,
        )

    def _create_job(self) -> None:
        values = ",".join(
            (
                "RP_ENV=stage",
                "RP_CLOUD=gcp",
                f"RP_PROJECT_ID={self.config.project}",
                f"RP_REGION={self.config.region}",
                f"RP_STORAGE_URI={self.config.data_uri}",
                f"RP_SCRATCH_URI={self.config.scratch_uri}",
            )
        )
        self.runner.run(
            self._gcloud(
                "run",
                "jobs",
                "create",
                self.config.job_name,
                "--project",
                self.config.project,
                "--region",
                self.config.region,
                "--image",
                self.config.image_reference,
                "--service-account",
                self.config.job_service_account,
                "--tasks",
                "1",
                "--max-retries",
                "0",
                "--task-timeout",
                "600s",
                "--set-env-vars",
                values,
                "--args",
                "job,staging_probe,--date,2000-01-01,--run-id,bootstrap",
                "--quiet",
            )
        )

    def _delete_job(self) -> dict[str, str]:
        result = self.runner.run(
            self._gcloud(
                "run",
                "jobs",
                "delete",
                self.config.job_name,
                "--project",
                self.config.project,
                "--region",
                self.config.region,
                "--quiet",
            ),
            check=False,
        )
        return {"status": "pass" if result.returncode == 0 else "failed"}

    def _read_object(self, uri: str, description: str) -> dict[str, Any]:
        result = self.runner.run(self._gcloud("storage", "cat", uri))
        return _load_json(result.stdout, description)

    def run(self) -> tuple[dict[str, Any], Path]:
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "run_id": self.config.run_id,
            "project": self.config.project,
            "region": self.config.region,
            "image_reference": self.config.image_reference,
            "source_commit": self.config.source_commit,
            "release_run_id": self.config.release_run_id,
            "ephemeral_job": self.config.job_name,
            "result": "failed",
        }
        error: Exception | None = None
        created = False
        try:
            project = _load_json(
                self.runner.run(
                    self._gcloud(
                        "projects", "describe", self.config.project, "--format=json"
                    )
                ).stdout,
                "project describe",
            )
            if project.get("projectId") != self.config.project:
                raise IntegrationError("authenticated project did not match staging")

            self.runner.run(["airflow", "db", "migrate"], env=self._airflow_env())
            created = True
            self._create_job()

            before_success = self._list_remote_logs()
            success = self._run_dag("success", self.config.logical_date)
            (self.config.evidence_dir / "success-airflow.log").write_text(
                success.stdout + success.stderr
            )
            if success.returncode != 0:
                raise IntegrationError("successful staging DAG run failed")
            success_logs = self._wait_for_new_logs(before_success)

            success_prefix = self.config.prefix("success")
            payload = self._read_object(
                f"{success_prefix}/result.json", "staging probe result"
            )
            marker = self._read_object(
                f"{success_prefix}/_SUCCESS", "staging probe completion marker"
            )
            if payload.get("result") != "pass" or marker.get("rows") != 1:
                raise IntegrationError("successful staging partition evidence is invalid")

            before_failure = self._list_remote_logs()
            failure_date = self.config.logical_date + timedelta(seconds=1)
            failure = self._run_dag("failure", failure_date)
            (self.config.evidence_dir / "failure-airflow.log").write_text(
                failure.stdout + failure.stderr
            )
            if failure.returncode == 0 or "DagRun failed" not in (
                failure.stdout + failure.stderr
            ):
                raise IntegrationError("controlled failure did not fail the Airflow DAG loudly")
            failure_logs = self._wait_for_new_logs(before_failure)

            failure_prefix = self.config.prefix("failure")
            for filename in ("result.json", "_SUCCESS"):
                failed_output = self.runner.run(
                    self._gcloud("storage", "cat", f"{failure_prefix}/{filename}"),
                    check=False,
                )
                if failed_output.returncode == 0:
                    raise IntegrationError("controlled failure published a partition")

            evidence.update(
                {
                    "result": "pass",
                    "success": {
                        "dag_state": "success",
                        "probe_id": self.config.probe_id("success"),
                        "partition": success_prefix,
                        "payload": payload,
                        "completion_marker": marker,
                        "airflow_remote_logs": success_logs,
                    },
                    "controlled_failure": {
                        "dag_state": "failed",
                        "probe_id": self.config.probe_id("failure"),
                        "partition_absent": True,
                        "airflow_remote_logs": failure_logs,
                    },
                }
            )
        except Exception as caught:
            error = caught
            evidence["error"] = str(caught)
        finally:
            evidence["cleanup"] = self._delete_job() if created else {"status": "not-required"}
            if evidence["cleanup"]["status"] != "pass" and created:
                evidence["result"] = "failed"
                error = error or IntegrationError("ephemeral Cloud Run Job cleanup failed")
            path = self.config.evidence_dir / f"staging-integration-{self.config.run_id}.json"
            path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

        if error:
            raise IntegrationError(str(error)) from error
        return evidence, path


def _logical_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("logical date must be ISO 8601") from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("logical date must include a timezone")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve-release")
    resolve.add_argument("--evidence-dir", type=Path, required=True)
    resolve.add_argument("--repository", required=True)
    resolve.add_argument("--github-output", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--project", required=True)
    run.add_argument("--region", required=True)
    run.add_argument("--data-uri", required=True)
    run.add_argument("--scratch-uri", required=True)
    run.add_argument("--airflow-logs-uri", required=True)
    run.add_argument("--image-reference", required=True)
    run.add_argument("--source-commit", required=True)
    run.add_argument("--release-run-id", required=True)
    run.add_argument("--job-service-account", required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--logical-date", type=_logical_date, required=True)
    run.add_argument("--evidence-dir", type=Path, default=Path("staging-evidence"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "resolve-release":
            release = resolve_release(args.evidence_dir, args.repository)
            write_github_output(args.github_output, release)
            print(json.dumps(asdict(release), default=str, sort_keys=True))
            return 0

        config = IntegrationConfig(
            project=args.project,
            region=args.region,
            data_uri=args.data_uri.rstrip("/"),
            scratch_uri=args.scratch_uri.rstrip("/"),
            airflow_logs_uri=args.airflow_logs_uri.rstrip("/"),
            image_reference=args.image_reference,
            source_commit=args.source_commit,
            release_run_id=args.release_run_id,
            job_service_account=args.job_service_account,
            run_id=args.run_id,
            logical_date=args.logical_date,
            evidence_dir=args.evidence_dir,
        )
        evidence, path = StagingIntegration(config, CommandRunner()).run()
        print(json.dumps({"result": evidence["result"], "evidence": str(path)}))
        return 0
    except IntegrationError as error:
        print(f"staging integration failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
