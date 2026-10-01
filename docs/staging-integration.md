# Staging integration gate

P4.3 tests the released image digest against real staging Cloud Run and Cloud
Storage APIs. It does not deploy a persistent scheduler and it never rebuilds
the P4.2 image. A protected GitHub runner checks out the released commit, runs
the real `staging_integration` DAG locally, and dispatches an ephemeral Cloud
Run Job through `CloudRunExecuteJobOperator`.

The gate proves both paths:

1. A successful job writes `result.json`, then the shared job harness writes
   `_SUCCESS`. The DAG verifies that the marked partition is non-empty.
2. A controlled job exception emits `STAGING_PROBE_CONTROLLED_FAILURE`, the
   Cloud Run execution fails, `airflow dags test` exits with `DagRun failed`,
   and no failure partition exists.
3. Each path must create a new remote Airflow task-log object.
4. The ephemeral `jarvis-p43-*` job must be deleted. Cleanup failure fails the
   gate and is recorded in the evidence manifest.

## Provisioning and protected variables

Apply the `terraform/live/gcp/stage` root first. Its `github_oidc` output exposes
the repository/environment/ref-bound provider and the dedicated integration
service account. Configure the GitHub `staging` environment with deployment
branch `main`, required reviewers as appropriate, and these non-secret
variables:

| Variable | Source |
| --- | --- |
| `GCP_STAGING_PROJECT_ID` | staging `project_id` |
| `GCP_STAGING_REGION` | staging `configuration.region` |
| `GCP_STAGING_DATA_URI` | staging `storage_locations.data` |
| `GCP_STAGING_SCRATCH_URI` | staging `storage_locations.scratch` |
| `GCP_STAGING_AIRFLOW_LOGS_URI` | staging `storage_locations.airflow_logs` |
| `GCP_STAGING_WIF_PROVIDER` | staging `github_oidc.workload_identity_provider` |
| `GCP_STAGING_INTEGRATION_SERVICE_ACCOUNT` | staging `github_oidc.integration_service_account` |
| `GCP_STAGING_JOB_SERVICE_ACCOUNT` | staging `identities.job` |

Do not configure a service-account key. The workflow requires GitHub OIDC and
rejects a project ID that does not end in `-stage` or a runtime service account
that is not owned by that project.

If P4.2 stores the release image in a different project, grant both the staging
integration identity and the staging Cloud Run service agent
`roles/artifactregistry.reader` on that one producer repository. The former is
needed for signature verification and the latter for Cloud Run image pull. Use
the repository's Terraform/IAM review process; do not grant project-wide reader
or copy/rebuild the image in staging.

## Running and evaluating the gate

A successful `release` workflow starts `staging integration` automatically.
For a retained release, dispatch it manually with the numeric release workflow
run ID. The workflow downloads the retained `gcp-release-*` artifacts and fails
unless exactly one passing schema-v1 manifest binds this repository's `main`
commit to an immutable Artifact Registry digest. It reverifies the release
workflow's keyless signature before authenticating to staging.

The retained `staging-integration-<run>-<attempt>` artifact contains:

- the second cosign verification result;
- local Airflow output for the success and controlled-failure runs; and
- `staging-integration-<run>-<attempt>.json`, which binds the digest, staging
  project, ephemeral job, partition, completion marker, remote log objects,
  controlled DAG failure, and cleanup result. The integration identity can list
  names in the dedicated Airflow-log bucket to detect new objects, but can read,
  create, overwrite, or delete objects only under this DAG's log prefix.

The JSON result must be `pass`, the success DAG state must be `success`, the
controlled-failure DAG state must be `failed`, `partition_absent` must be true,
and cleanup must be `pass`. A failed workflow may leave a job only when creation
succeeded but deletion failed; delete that exact evidence-recorded job after
investigating the audit log.

P4.3 is live-complete only after a retained passing artifact exists for a P4.2
digest. Local tests and a green workflow definition do not substitute for that
cloud acceptance evidence.
