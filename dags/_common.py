"""Shared DAG construction.

DAGs in this project do exactly two things: dispatch a batch job, then verify
that the job actually produced output. Both are built here so that the
verification step cannot be forgotten in a new DAG -- which is the single most
common way a remote-dispatch pipeline fails silently.

The provider is resolved once, at import, from the storage URI. No DAG contains
a provider branch.

NOTE ON IMPORTS: this targets the Airflow 3.x Task SDK. On Airflow 2.x replace
``from airflow.sdk import ...`` with ``from airflow import DAG`` and
``from airflow.decorators import task``.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import pendulum
from airflow.models import BaseOperator
from airflow.sdk import task

from dags._providers import DISPATCHERS
from jobs.common.cloud import Cloud, infer_cloud

CLOUD = (
    Cloud(os.environ["RP_CLOUD"])
    if os.environ.get("RP_CLOUD")
    else infer_cloud(os.environ.get("RP_STORAGE_URI", ""))
)

ALERT_EMAILS = [e for e in os.environ.get("RP_ALERT_EMAILS", "").split(",") if e]
LOCAL_TZ = pendulum.timezone(os.environ.get("RP_TIMEZONE", "UTC"))

DEFAULT_ARGS: dict[str, Any] = {
    "owner": "research",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "email": ALERT_EMAILS,
    "email_on_failure": bool(ALERT_EMAILS),
    "email_on_retry": False,
    "depends_on_past": False,
}


def dispatch(
    task_id: str,
    module: str,
    *,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cpu: str = "2",
    memory: str = "4Gi",
    timeout_seconds: int = 3600,
) -> BaseOperator:
    """Run ``jobs.<module>`` in a batch container sized for this task.

    The image tag is *not* set here on GCP or AWS -- the job definition owns it
    and ``ctl deploy`` updates it, so every role runs the same tag by
    construction rather than by discipline. Azure Container Instances has no
    persistent definition, so there the tag comes from RP_IMAGE_TAG, which
    ``ctl deploy`` writes to the control node's env file.
    """
    container_args = ["job", module, "--date", "{{ ds }}", "--run-id", "{{ run_id }}"]
    container_args += args or []

    try:
        dispatcher = DISPATCHERS[CLOUD]
    except KeyError:
        raise RuntimeError(
            f"no batch dispatcher for cloud={CLOUD!r}; "
            "set RP_STORAGE_URI to a gs://, s3:// or abfs:// URI"
        ) from None

    return dispatcher(task_id, container_args, env, cpu, memory, timeout_seconds)


@task(task_id="verify_output")
def verify(
    kind: str,
    dataset: str,
    source: str | None = None,
    ds: str | None = None,
    probe_id: str | None = None,
) -> dict:
    """Assert the partition exists, is marked complete, and is not empty.

    Provider-neutral: fsspec resolves gs://, s3:// and abfs:// identically, so
    this function is the same code on all three.

    Imported lazily so that DAG parsing on the scheduler stays fast and does not
    require the storage stack to be importable at parse time.
    """
    from jobs.common import storage
    from jobs.common.config import get_settings

    run_date = date.fromisoformat(ds)
    if kind == "raw":
        if source is None:
            raise ValueError("raw partitions need a source")
        prefix = storage.raw_prefix(source, dataset, run_date)
    elif kind == "derived":
        prefix = storage.derived_prefix(dataset, run_date)
    elif kind == "integration":
        if probe_id is None:
            raise ValueError("integration partitions need a probe_id")
        prefix = storage.integration_prefix(dataset, run_date, probe_id)
    else:
        raise ValueError(f"unsupported partition kind: {kind}")

    if not storage.is_complete(prefix):
        raise AssertionError(f"partition is not marked complete: {prefix}")

    written = storage.size_bytes(prefix)
    minimum = get_settings().min_output_bytes
    if written < minimum:
        raise AssertionError(f"partition {prefix} is only {written} bytes (min {minimum})")

    return {"prefix": prefix, "bytes": written}
