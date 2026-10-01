# Deployment and operations

This describes the operational contract, not a substitute for a provider review.

## Provisioning

AWS and Azure currently expect existing private networking. For GCP, first
create the backend using the
[state bootstrap procedure](../terraform/bootstrap/gcp/README.md); do not create
an ad hoc bucket or reuse another environment's state prefix.
AWS workload security groups accept only the explicitly configured
`workload_egress_cidr_blocks`; route package downloads and public APIs through a
reviewed egress proxy or private endpoints. Azure Storage and Key Vault require
the configured VM and ACI subnets to expose their respective service endpoints.
Deploy GCP through the [live environment roots](../terraform/live/gcp/README.md),
not by applying the reusable `terraform/gcp` module directly. Each live root
owns an isolated private VPC, Cloud NAT, private service access, private DNS,
and IAP-only SSH ingress alongside the platform resources.

Local bootstrap and runtime settings belong in their documented ignored env
files. Store only credential references or impersonation targets there; use
short-lived Application Default Credentials or workload identity rather than
embedding cloud keys.

The first live-root apply creates its deployer identity. Complete the documented
[GCP identity handoff](gcp-identity.md#initial-identity-handoff), grant that
deployer access to the remote-state bucket through the bootstrap stack, and use
impersonation for every later plan and apply. GitHub publishing uses the separate
CI identity and repository/environment/ref-bound OIDC federation.

Before a live apply, complete the
[GCP guardrail prerequisites](gcp-guardrails.md): approve the environment's
monthly budget, grant the deployer Billing Account Costs Manager on the selected
billing account, and arrange organization-policy evidence. After the apply,
verify the Monitoring email channel and deliver a test notification.
Keep cross-project data variables empty unless the source owner approved the
resource, workload, and role under the
[GCP data-access procedure](gcp-data-access.md). Review the resulting
`data_access_contract` output with every live plan.
Review the four-location [storage contract](storage-classes.md) before applying:
the existing data location must remain in place, logs and scratch receive only
their named workload identities, and backup storage has no runtime member.
Review the generated [data ownership contract](data-ownership.md) with each
group change. Update its source manifest, regenerate it with
`make ownership-render`, and confirm `make ownership-check` passes before any
matching IAM or data-migration change is applied.
Review the [Cloud SQL production policy](cloud-sql.md) before changing database
availability, backup, PITR, maintenance, Query Insights, or deletion settings.
Seed and rotate credentials only through the
[runtime-secrets procedure](runtime-secrets.md); deployment files contain
identifiers, never values.
Apply and exercise the [quarterly recovery contract](recovery-drills.md) in
staging before claiming the provisional RPO or RTO.
Build, inspect, replace, and roll back GCP VM prerequisites only through the
[immutable host-image procedure](host-images.md). A host package update is an
image replacement, never an SSH or startup-script mutation.

```bash
scripts/gcp-live.sh dev validate
scripts/gcp-live.sh dev plan
scripts/gcp-live.sh dev apply
```

Do not commit backend credentials, variable files, plans, or state.

## Production configuration

- Render non-secret runtime settings from Terraform and the selected group
  overlay, then point the ignored local `.env` at the result:

  ```bash
  uv run ctl config render --environment prod --group GROUP
  uv run ctl config check --file .runtime/prod-GROUP.env
  ```

  ```dotenv
  RP_CONFIG_FILE=.runtime/prod-GROUP.env
  SSH_USER=operator
  ```

- Follow the [declarative configuration contract](declarative-configuration.md)
  when adding a group or runtime setting. Do not edit generated files.
- Keep `.env`, `.env.live`, and generated files mode `0600` and out of Git.
- Do not add passwords, tokens, Fernet keys, or credential-file paths to runtime
  overlays; `ctl deploy` rejects known secret-bearing keys and copies only its
  non-secret allowlist.
- Use workload identity and the provider secret store, not downloaded keys.
- Keep `RP_STORAGE_URI` and `AIRFLOW_REMOTE_LOGS` on the same provider.
- Set `RP_SCRATCH_URI` from Terraform's `scratch_uri` output and keep
  `AIRFLOW_REMOTE_LOGS` on the dedicated `airflow_logs_uri`.
- Pin `IMAGE_TAG` to a Git SHA.
- Retain loopback bindings for Airflow and Jupyter.

## Build and release

```bash
uv run ctl build
uv run ctl push
uv run ctl config check
uv run ctl plan all
uv run ctl deploy all
uv run ctl status all
uv run ctl doctor all
```

The CLI rejects dirty trees so image contents correspond to the tag. CI builds
and scans each provider image without cloud access. A separate release workflow
runs only after CI succeeds for a push to this repository's `main`, enters the
protected `production` environment, checks out the exact validated SHA, and uses
keyless OIDC to build and publish one GCP image, test its digest, create its
SBOM and provenance, sign it, and retain release evidence. The release fails
closed until all required protected GCP variables are configured. Follow the
[CI and release trust-boundary runbook](release-automation.md) when changing
workflow permissions, triggers, environments, or cloud federation.
Use the [staging integration gate](staging-integration.md) to accept that exact
digest before promotion; the gate exercises both a marked successful partition
and a loud controlled failure using an ephemeral staging Cloud Run Job.

## Access

```bash
gcloud compute ssh research-dev-control --project PROJECT_ID --zone us-central1-a \
  --tunnel-through-iap -- -N -L 8080:localhost:8080
gcloud compute ssh research-dev-notebook --project PROJECT_ID --zone us-central1-a \
  --tunnel-through-iap -- -N -L 8888:localhost:8888
```

GCP SSH is accepted only from IAP's TCP-forwarding range. Keep the tunnel flag;
do not add an external IP or broader firewall rule for administration. Only the
named `operator_principals` receive OS Admin Login, port-22 IAP access, instance
start/stop, and actAs on the three VM service accounts.

## Routine checks

- Check scheduler/API health and remote Airflow log delivery.
- Run a synthetic batch job and verify its output and success marker.
- Monitor feed lag, reconnects, flush failures, and dropped records.
- Monitor database backups, restores, capacity, and connections.
- Monitor object growth, lifecycle rules, batch failures, quotas, and cost.
- Treat budget and quota notifications as incidents: acknowledge delivery,
  identify the affected environment, and record any approved budget or quota
  change in the infrastructure review.
- Confirm every role uses the intended immutable image tag.
- Confirm each GCP role's systemd unit and JSON health are healthy; follow the
  [service-supervision runbook](service-supervision.md) for reboot and crash drills.
- Confirm notebook storage verification and its latest scheduled recovery point;
  follow the [notebook runbook](notebooks.md) before host replacement or restore.

Useful commands:

```bash
uv run ctl logs control --service scheduler
uv run ctl logs notebook --service jupyter
uv run ctl shell control
uv run ctl run pull_ohlcv --date 2026-08-11 --remote
```

## Deployment and recovery

Validate changes outside production, deploy one immutable tag, verify storage
and logs, then update remaining roles. Keep the previous tag for rollback.
The normal release path resolves that tag to an immutable registry digest,
health-gates each role, verifies remote logs, runs a synthetic batch/storage
probe, and automatically restores already-activated roles if a later gate fails.
Use `ctl rollback all --yes` for the reviewed manual recovery workflow and follow
the [transactional deployment runbook](transactional-deployments.md).
Schema changes use the explicit, single-owner
[database migration workflow](database-migrations.md): migrate with the candidate
image, then deploy that exact tag. Never restore service with an older image
against an upgraded schema without a verified compatibility or database rollback.

The scheduled P2.5 workflow tests database PITR, versioned-object recovery,
Terraform-state recovery, and notebook-volume restoration each quarter under
the documented [Cloud SQL recovery objectives](cloud-sql.md#recovery-objectives).
Also test host rebuilds and rollback to a known-good image SHA. Compute nodes
should be disposable; required research data belongs in durable storage.

See [ADVERSARIAL_REVIEW.md](../ADVERSARIAL_REVIEW.md) for current limitations.
