"""Staging-only synthetic batch job used by the P4.3 integration gate."""

from __future__ import annotations

import os

from jobs.common import storage
from jobs.common.harness import JobContext, JobResult, entrypoint

PROBE = "staging_probe"
CONTROLLED_FAILURE = "STAGING_PROBE_CONTROLLED_FAILURE"


def run(ctx: JobContext) -> JobResult:
    if ctx.settings.env != "stage":
        raise RuntimeError("staging probe is restricted to RP_ENV=stage")

    probe_id = os.environ.get("RP_STAGING_PROBE_ID", "")
    mode = os.environ.get("RP_STAGING_PROBE_MODE", "success")
    prefix = storage.integration_prefix(PROBE, ctx.run_date, probe_id)

    if mode == "failure":
        raise RuntimeError(CONTROLLED_FAILURE)
    if mode != "success":
        raise RuntimeError(f"unsupported staging probe mode: {mode}")

    payload = {
        "schema_version": 1,
        "probe_id": probe_id,
        "run_id": ctx.run_id,
        "run_date": ctx.run_date.isoformat(),
        "environment": ctx.settings.env,
        "cloud": ctx.settings.cloud.value if ctx.settings.cloud else None,
        "result": "pass",
        "padding": "p4.3-integration-evidence-" * 4,
    }
    storage.write_json(f"{prefix}/result.json", payload)
    return JobResult(
        output_prefix=prefix,
        rows=1,
        metadata={"probe_id": probe_id, "mode": mode},
    )


if __name__ == "__main__":
    entrypoint(PROBE, run)
