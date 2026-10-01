"""Manual staging gate for one successful and one controlled-failure run."""

from __future__ import annotations

import pendulum
from airflow.sdk import DAG

from dags._common import DEFAULT_ARGS, LOCAL_TZ, dispatch, verify

with DAG(
    dag_id="staging_integration",
    description="Exercise one Cloud Run Job and verify its marked partition",
    start_date=pendulum.datetime(2026, 1, 1, tz=LOCAL_TZ),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["staging", "integration", "release-gate"],
) as dag:
    probe = dispatch(
        "run_staging_probe",
        "staging_probe",
        env={
            "RP_STAGING_PROBE_ID": "{{ dag_run.conf['probe_id'] }}",
            "RP_STAGING_PROBE_MODE": "{{ dag_run.conf['mode'] }}",
        },
        cpu="1",
        memory="1Gi",
        timeout_seconds=600,
    )
    # A retry would turn the deliberate failure probe into several executions
    # and obscure the one-to-one acceptance evidence.
    probe.retries = 0

    check = verify.override(task_id="verify_staging_probe")(
        kind="integration",
        dataset="staging_probe",
        ds="{{ ds }}",
        probe_id="{{ dag_run.conf['probe_id'] }}",
    )

    probe >> check
