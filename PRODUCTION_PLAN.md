# Jarvis production functionality plan

Status: active — P4.1, P4.1b, P4.2, and P4.3 complete locally; P4.1a complete with
live organization registration and retained acceptance evidence
Prepared: 2026-09-16
Reference reviewed: [`sixtycapital/infrastructure`](https://github.com/sixtycapital/infrastructure/tree/b6da17b68b9a2be41dbfa616506b70e30ce62c6e), tag `3.4.1`

## 1. Objective

Take Jarvis from a sound multi-cloud scaffold to a production research and batch
platform with the useful operating capabilities demonstrated by Sixty Capital's
infrastructure:

- repeatable cloud-environment creation;
- a shared, reproducible research and production image;
- durable object storage with explicit lifecycle policies;
- managed Airflow metadata storage and remote task logs;
- isolated, elastic compute classes for small and large workloads;
- secure notebook, scheduler, feed, and batch execution;
- automated image delivery and repeatable deployment; and
- documented monitoring, recovery, and day-two operations.

The reference repository is a capability baseline, not an implementation template.
Its reviewed revision dates from 2019 and uses Python 3.7, Airflow 1.10, mutable
deployment tags, broad default-service-account permissions, in-cluster Redis, and
an unauthenticated Airflow webserver. Jarvis must deliver equivalent or better
functionality using its current security and architecture constraints.

## 2. Scope and decisions

### Decisions to adopt

1. **GCP is the first production provider.** It is the shortest path to validated
   parity with the reference. AWS and Azure retain their current interfaces, but
   production parity follows the GCP release rather than blocking it.
2. **Do not introduce Kubernetes or Celery for parity alone.** Cloud Run Jobs provides
   the elastic worker tiers that the reference implemented with GKE node pools and
   32 Celery workers, while retaining scale-to-zero and simpler failure isolation.
3. **Use separate GCP projects for `dev`, `stage`, and `prod`.** This replaces the
   reference's hard-coded scratch/strategy/production project lists with auditable
   environment boundaries.
4. **Object storage remains the source of truth.** BigQuery may be added as an
   optional serving and analysis layer; it must not become an undocumented second
   source of truth.
5. **Promote one image digest.** Build once, test in `dev` and `stage`, then promote
   the same digest to `prod`. Human-friendly tags are pointers, not deployment
   identities.
6. **Terraform owns durable infrastructure.** `ctl` owns build, release, deployment,
   status, and rollback workflows. Neither tool silently performs the other's job.
7. **No secret-bearing `.env` files in production.** Workloads obtain secrets at
   runtime through attached identity and Secret Manager.

### Explicit non-goals

- simultaneous active operation across multiple clouds;
- Kubernetes unless a measured workload cannot run on the selected batch service;
- JupyterHub or multi-user notebook tenancy in the initial production release;
- low-latency trading execution;
- copying the reference repository's package list wholesale; and
- high availability for an individual notebook session.

## 3. GitHub repository linking and access model

Production source control should be organization-owned, artifact-driven, and free of
shared user credentials. GitHub recommends a GitHub App for long-lived organization
automation because it is independent of an individual user, supports fine-grained
permissions and selected-repository installation, and issues short-lived tokens. The
built-in `GITHUB_TOKEN` remains preferred when a workflow needs only its own repository;
it cannot access other repositories. See GitHub's guidance on
[choosing a GitHub App](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/deciding-when-to-build-a-github-app)
and [cross-repository authentication from Actions](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/making-authenticated-api-requests-with-a-github-app-in-a-github-actions-workflow).

### Repository topology

Move production repositories under one GitHub organization rather than linking
personal accounts. Use this target topology once the platform/workload interface is
stable enough to justify the split:

```text
GitHub organization
├── jarvis                  public or private platform/runtime source
├── jarvis-workloads        private jobs, DAGs, schemas, and final-image build
├── jarvis-live             private environment composition and promoted digests
├── jarvis-automation       public credential-free reusable validation workflows
├── research-notebooks      private human research source; never a prod dependency
└── ggstyle                 public versioned Python package

jarvis release/package + base-image digest
              │
              ▼
jarvis-workloads final image + test evidence
              │
              ▼ promotion PR
jarvis-live reviewed digest -> GitHub environment -> cloud OIDC -> deployment
```

Responsibilities and access boundaries:

| Repository | Contents | Production relationship | Default human access |
|---|---|---|---|
| `jarvis` | Platform runtime, provider adapters, CLI, reusable Terraform modules | Produces versioned Python artifacts and a signed base-image digest | Platform maintainers write; broader engineering read |
| `jarvis-workloads` | Proprietary jobs, DAGs, vendor adapters, schemas, golden fixtures | Pins a Jarvis release/base digest and produces the final deployable image | Data engineering write; platform and release teams read/review |
| `jarvis-live` | `dev/stage/prod` Terraform roots, non-secret configuration, approved image digests | Sole Git source of desired deployed versions; never stores Terraform state or secrets | Release managers write; platform read; tightly limited admin |
| `jarvis-automation` | Credential-free reusable validation workflows and organization-owned actions | Supplies full-SHA-pinned CI building blocks to allowlisted repositories; never owns release authority | Platform/security write; public read required for public callers |
| `research-notebooks` | Private notebooks and research utilities | May consume released artifacts; production must never clone or import it | Researchers write; no production bot access by default |
| `ggstyle` | Standalone visualization package | Jarvis consumes an immutable released version, never `../ggstyle` | Package maintainers write; public read if it remains public |

Do not split `jobs/` and `dags/` merely to match this diagram. First define their
package and image interface, then perform the split as an atomic migration with a
working composite-image build. Until then, Jarvis remains the deployable source repo.

### How repositories may link

| Link mechanism | Production decision | Rules |
|---|---|---|
| Released Python wheel | **Preferred for Python libraries** | Publish a version, lock hashes, and promote the immutable artifact. GitHub Packages does not currently provide a PyPI registry, so use PyPI trusted publishing for public packages or a cloud/private Python repository for private packages. |
| OCI image | **Preferred for runtime composition** | Reference the base and final images by digest; tags are discovery aliases only. Runtime hosts pull from the cloud registry using workload identity. |
| Terraform module Git source | **Allowed** | Pin a full commit SHA or immutable release tag. Private module fetches use a read-only GitHub App token in CI, never a developer PAT on a production host. |
| Reusable workflow/action | **Allowed and encouraged** | Call from `jarvis-automation`, pin actions to a full commit SHA, enforce a code-level caller allowlist, and review workflow changes as production code. GitHub requires workflows called by public repositories to be public, so reusable automation must remain credential-free and release authority stays in the caller. |
| Cross-repository checkout in CI | **Exception, not composition default** | Use a selected-repository GitHub App installation token with `contents:read`; record the exact source SHA in build provenance. |
| Promotion pull request | **Preferred cross-repository write** | A release App opens a branch/PR changing an image digest in `jarvis-live`; it cannot merge, approve, or bypass rules. |
| Git submodule/subtree | **Avoid for runtime dependencies** | Recursive authentication, detached revisions, and multi-repository atomicity make operations fragile. Use only for consciously vendored source with a named owner and update procedure. |
| Branch-based Git dependency | **Prohibited in releases** | `main`, moving tags, and unpinned Git URLs are not reproducible inputs. |
| Deploy key | **Single-repository fallback** | Read-only only. GitHub deploy keys apply to one repository and cannot be reused across repositories, making them unsuitable for the normal multi-repo path. |
| Fine-grained PAT | **Temporary human fallback** | Must be repository-scoped, minimally permissioned, expiring, approved by the organization, stored in a credential manager, and tracked for removal. Never use a classic PAT for routine production automation. |

GitHub's documented package registry list covers containers, npm, RubyGems, Maven,
Gradle, and NuGet—not Python packages—so `ggstyle` should use a real Python package
index or a commit-pinned Git dependency during transition, rather than pretending
GitHub Packages is a private PyPI service. See the
[supported GitHub Packages registries](https://docs.github.com/en/packages/working-with-a-github-packages-registry).

### Authentication by principal

| Principal/use case | Authentication | Minimum access and controls |
|---|---|---|
| Human developer | Individual GitHub account, organization membership, 2FA/SSO, GitHub CLI or credential manager | Team-derived repository role; no shared account or shared SSH key |
| Same-repository Actions job | Ephemeral `GITHUB_TOKEN` | Declare job-level `permissions`; default to `contents:read`; elevate only the job that needs it |
| Cross-repository CI reader | `jarvis-ci-reader` GitHub App installation token | Installed only on source repositories; `contents:read`, metadata read; no administration or workflow write |
| Promotion automation | Separate `jarvis-release-bot` GitHub App | `contents:write` and `pull_requests:write` only on `jarvis-live`; creates PRs but cannot approve, merge, or bypass rulesets |
| GitHub Actions to GCP | GitHub OIDC federation to an environment-specific GCP deployer service account | Trust condition binds organization, repository, branch or protected environment, and audience; no JSON service-account key |
| Production VM/container | Cloud workload identity | Pulls images/config from cloud services; holds no GitHub token and does not clone production source |
| Human notebook user | Individual GitHub SSH/HTTPS credential when private notebook sync is needed | Access to `research-notebooks` only; credentials stay in the user's session/keychain, not the notebook image |
| Emergency script | Expiring fine-grained PAT only when App/CLI is unsuitable | Named owner, selected repositories, minimum permissions, expiry, audit issue, and immediate revocation after use |

GitHub documents that deploy keys remain valid for anyone holding the private key even
after that person leaves the organization, reinforcing why production machines should
use Apps or artifact registries instead. See
[deploy-key limitations](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
and [repository roles](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization).

### Human authorization and repository governance

- Set private-repository organization base permission to `none`; grant access through
  visible teams such as `platform-maintainers`, `data-engineering`, `research`,
  `release-managers`, and `security-auditors`.
- Keep at least two organization owners for continuity, but keep owners out of routine
  development and deployment. Use `maintain` or `write` for normal work and reserve
  `admin` for repository security and destructive settings.
- Use outside collaborators only for people who cannot join the organization; grant
  one repository at a time, require 2FA, assign an expiry/review date, and remove
  access at contract end.
- Add `CODEOWNERS` for runtime, Terraform, workflows, data contracts, and production
  environment files. Require code-owner approval for the relevant paths.
- Apply organization rulesets to default branches and release tags: pull requests,
  required checks, fresh approval after changes, conversation resolution, blocked
  force-push/deletion, and no routine bypass. Rulesets can consistently target
  multiple organization repositories.
- Restrict Actions to GitHub-owned and explicitly approved actions; pin third-party
  actions and reusable workflows to full commit SHAs.
- Audit team membership, App installations, outside collaborators, deploy keys, PATs,
  environment reviewers, and ruleset bypasses quarterly.

GitHub teams and granular repository roles are the supported way to grant least
privilege across users and repositories; organization rulesets provide consistent
branch and tag controls across repositories. See
[organization teams](https://docs.github.com/en/organizations/organizing-members-into-teams/about-teams),
[repository roles](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization),
and [organization rulesets](https://docs.github.com/en/organizations/managing-organization-settings/creating-rulesets-for-repositories-in-your-organization).

### Cross-repository build and promotion flow

1. `jarvis` CI uses its own `GITHUB_TOKEN`, tests a clean checkout, publishes a
   versioned artifact/base image, signs the digest, and emits provenance.
2. `jarvis-workloads` receives an explicit dependency-update PR. Its build consumes
   the released artifact or base digest—not a sibling checkout or moving branch—and
   produces the final signed image.
3. Staging integration tests run from `jarvis-workloads`. Cloud access uses OIDC bound
   to the `staging` GitHub environment and staging deployer identity.
4. On success, `jarvis-release-bot` opens a PR in `jarvis-live` that changes only the
   final image digest and associated provenance reference.
5. `CODEOWNERS`, required checks, and release-manager review approve the desired-state
   change. The bot cannot approve or merge its own PR.
6. The production workflow reads the merged digest from `jarvis-live`, enters the
   protected `production` environment, obtains a short-lived GCP credential through
   OIDC, deploys, verifies health, and records the GitHub deployment.
7. Rollback is another reviewed `jarvis-live` digest change or a pre-authorized
   emergency workflow that still records actor, reason, old digest, and new digest.

GitHub environments can restrict deployment branches, require reviewers, prevent
self-review, and withhold environment secrets until protection rules pass. OIDC lets
Actions obtain GCP credentials without storing long-lived cloud keys. See
[deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
and [OIDC for GCP](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-google-cloud-platform).

### Shared workflow controls

`jarvis-automation` exposes public, credential-free reusable workflows because the
public Jarvis caller cannot import a private workflow. Each workflow hard-fails for
callers outside its reviewed repository allowlist. Consumers pin the full commit SHA;
moving tags and branches are prohibited. A called workflow cannot be used as a hidden
privilege escalator: its inputs and secrets are absent or explicitly declared, token
permissions are read-only, and production OIDC remains in the deployment repository's
protected environment. See
[reusable workflow access](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

## 4. Reference capability mapping

| Reference capability | Evidence in the reviewed repository | Jarvis target |
|---|---|---|
| Project and API bootstrap | [`sixtyctl/gcp.py`](https://github.com/sixtycapital/infrastructure/blob/b6da17b68b9a2be41dbfa616506b70e30ce62c6e/sixtyctl/gcp.py) creates projects, attaches billing, and enables services | Terraform bootstrap and live-environment roots create or configure `dev`, `stage`, and `prod`, remote state, APIs, budgets, and quotas |
| Shared research/production environment | [`docker/Dockerfile`](https://github.com/sixtycapital/infrastructure/blob/b6da17b68b9a2be41dbfa616506b70e30ce62c6e/docker/Dockerfile) combines scientific Python, Jupyter, Airflow, and cloud tools | Keep Jarvis's one image and role entrypoint, with locked profiles, clean-build verification, SBOM, scanning, and digest pinning |
| Durable and temporary storage | `create_project_buckets` defines durable and 14-day temporary buckets and storage-class transitions | Separate data, logs, temporary, and Terraform-state concerns; version data; transition raw data; expire logs/temp; test recovery |
| Managed metadata database | `create_db_instance` configures Cloud SQL backups, maintenance, and disk autoresize | Private Cloud SQL PostgreSQL with production HA, PITR, deletion protection, monitoring, and scheduled restore tests |
| Elastic compute tiers | GKE has small and large-preemptible autoscaling node pools | Named Cloud Run Job resource classes with quota limits, timeouts, retries owned by Airflow, and optional spot-capable alternatives only when measured |
| Airflow control plane and workers | [`sixtyctl/airflow.yaml`](https://github.com/sixtycapital/infrastructure/blob/b6da17b68b9a2be41dbfa616506b70e30ce62c6e/sixtyctl/airflow.yaml) deploys webserver, scheduler, workers, Redis, and Cloud SQL proxies | Harden the existing control node; dispatch computation to Cloud Run Jobs; add migration, health, restart, and rollback automation |
| Remote logs | Airflow configuration sends task logs to cloud storage | Retain remote logs, add retention, delivery canaries, dashboards, and alerts |
| Image build and registry delivery | [`cloudbuild.yaml`](https://github.com/sixtycapital/infrastructure/blob/b6da17b68b9a2be41dbfa616506b70e30ce62c6e/cloudbuild.yaml) builds and pushes a base image | GitHub OIDC to Artifact Registry, build provenance, signed digests, vulnerability policy, staged promotion, and rollback |
| Repeatable application rollout | [`dailydeploy/build_deploy.sh`](https://github.com/sixtycapital/infrastructure/blob/b6da17b68b9a2be41dbfa616506b70e30ce62c6e/dailydeploy/build_deploy.sh) updates repositories and restarts Airflow pods | Replace mutable daily tags and forced restarts with tested digest promotion, health gates, and explicit rollback |
| Notebook environment | The base image includes Jupyter/JupyterLab and persistent notebook configuration | Retain IAP-tunneled Jupyter, add encrypted persistent disk snapshots, private notebook source sync, and restore validation |
| Data access boundaries | Project lists and IAM grants share storage, BigQuery, and KMS access | Per-role service accounts, per-prefix or per-bucket access, environment separation, optional governed cross-project datasets, and IAM tests |

## 5. Definition of production quality

Jarvis is production-ready on GCP only when all of the following are true:

- **Reproducible:** a clean checkout can run `uv sync --frozen`, build the image,
  and reproduce the deployed dependency set without sibling directories.
- **Secure:** no public administrative endpoint, no downloaded cloud key, no secret
  in Git, Terraform output, deployment arguments, or a production `.env`; IAM is
  tested against intended allow and deny cases.
- **Deployable:** one reviewed digest can be promoted through environments and
  rolled back without rebuilding or editing cloud resources by hand.
- **Recoverable:** object versions, Cloud SQL PITR, Terraform state, and notebook
  backups each have a documented and successfully exercised restore procedure.
- **Observable:** scheduler health, job failures and duration, data freshness, feed
  lag and gaps, storage growth, database saturation, quota use, and cost have
  dashboards and actionable alerts.
- **Correct:** real data adapters have contract tests, schema checks, deterministic
  partitions, lineage metadata, and safe reruns; a successful task proves that its
  expected data exists and is plausible.
- **Operable:** routine deploy, rollback, backfill, secret rotation, restore, and
  incident procedures are executable runbooks rather than tribal knowledge.
- **Capacity-tested:** representative small and large jobs complete inside their
  resource and time budgets, and overload behavior is known.

## 6. Delivery plan

Phases are ordered by dependency. Each phase has an exit gate; starting exploratory
work early is acceptable, but no production promotion bypasses a gate.

### Phase 0 — Reproducible baseline

Goal: make the current repository a trustworthy build input.

Implementation status (2026-09-16):

| Item | Status | Evidence / remaining action |
|---|---|---|
| P0.1 | Complete | `ggstyle` resolves from an exact public Git commit, the lockfile is regenerated, and a clean temporary checkout completes frozen sync without the sibling repository. |
| P0.2 | Implemented; CI confirmation pending | CI builds `gcp`, `aws`, and `azure` images and runs the five-role smoke suite. All three variants also build and pass locally. |
| P0.3 | Complete | Python and `uv` base inputs are digest-pinned, image revisions are labeled, and supported build-tool versions are documented. |
| P0.4 | Implemented; CI confirmation pending | Source and image vulnerability/secret scans, Terraform misconfiguration scanning, SPDX SBOM artifacts, provenance, and the exception policy are configured. |
| P0.5 | Partial | The MIT license, `CODEOWNERS`, immutable action pins, Dependabot, and documented review policy are present. An owner must still enable repository rules, secret scanning, and push protection in GitHub. |
| P0.6 | Deferred by owner | Keep Jarvis in its current personal repository for now. Organization transfer, team/ruleset setup, and the credential/collaborator audit remain required before a production launch. |
| P0.7 | Complete | ADR 0001 records the GCP-first architecture, artifact boundaries, and conditions for a later repository split. |

- **P0.1 Resolve local dependencies.** Replace the editable `../ggstyle` source with
  a released version or immutable Git revision, regenerate `uv.lock`, and prove the
  package is available inside the Docker build context.
- **P0.2 Establish clean-build tests.** In CI, build all provider image variants from
  a clean checkout; start the `job`, `jupyter`, `feed`, `scheduler`, and `api-server`
  entrypoints with role-appropriate smoke configuration.
- **P0.3 Pin build inputs.** Pin the `uv` image by digest, define the supported Docker
  and Terraform versions, and record the base-image digest in release metadata.
- **P0.4 Add supply-chain checks.** Produce an SBOM, scan OS and Python dependencies,
  scan Terraform and secrets, and define severity/exception policy.
- **P0.5 Finish repository governance.** Select a license, add ownership/review rules,
  enable protected branches, secret scanning, push protection, and dependency update
  policy.
- **P0.6 Establish GitHub organization access.** Move production ownership away from
  personal accounts, create the team/role model from section 3, set private-repository
  base permission to `none`, configure organization rulesets, and inventory/revoke
  shared keys, deploy keys, classic PATs, and stale collaborators.
- **P0.7 Record repository boundaries.** Approve the target repo graph, but keep
  `jobs/` and `dags/` in Jarvis until their versioned package/image contract and the
  composite-image build pass end-to-end tests.

Exit gate:

- clean checkout passes lint, types, tests, Terraform validation, frozen sync, and
  all image builds;
- no dependency resolves from outside the repository or an immutable registry/VCS
  reference; and
- the five image roles pass smoke tests; and
- every human and automation principal reaches Jarvis through a named team, GitHub
  App, or documented short-lived exception—never a shared account.

### Phase 1 — GCP landing zone and environment isolation

Goal: make environment creation repeatable and safe.

Implementation status (2026-09-16): P1.1 through P1.6 are implemented and
locally validated. The protected state bucket and live roots still require
authorized, reviewed applies in their selected GCP projects.

- **P1.1 Add a bootstrap stack.** Create the remote-state bucket with versioning,
  retention, public-access prevention, and narrowly scoped administration. Bootstrap
  is deliberately separate from the state it creates.
- **P1.2 Add live environment roots.** Introduce `terraform/live/gcp/{dev,stage,prod}`
  or an equivalent composition layer with isolated state and explicit provider
  configuration. Reusable resources remain in the current GCP module.
- **P1.3 Provision private networking — complete locally.** Manage or formally import the VPC, regional
  subnets, Private Google Access, Cloud NAT where required, private service access,
  DNS, and IAP-only administration. Default-deny ingress is the baseline. Each live
  root now owns a custom-mode VPC with a fixed non-overlapping CIDR, private-only
  compute NICs, flow/NAT/firewall logging, private service peering, environment-local
  DNS, OS Login metadata, and a tested IAP SSH exception.
- **P1.4 Complete workload identity — complete locally.** Separate deployer, control, job, feed, notebook,
  and CI identities. Add GitHub OIDC federation bound to the exact repository and
  protected environment, plus IAM policy tests. Remove reliance on default service
  accounts. Every environment now has seven explicit user-managed identities; GitHub
  trust requires matching repository name, immutable repository/owner IDs, protected
  environment, and main ref. Runtime and CI permissions are resource-scoped where
  supported; named human operators receive only conditional IAP SSH, OS Admin Login,
  instance power, and the three required VM actAs grants. Native positive/negative
  IAM tests run in CI.
- **P1.5 Add project guardrails — complete locally.** Enable required APIs,
  labels, audit logs, budgets, quota alerts, and environment-specific deletion
  protection. Record which organization policies are required versus optional.
  All live roots now expose
  a tested guardrail contract covering 19 APIs, mandatory ownership/cost labels,
  all-service Data Access audit logs, project-scoped budget thresholds, allocation
  quota warning/exceeded alerts, and explicit dev versus stage/prod deletion
  policies. The billing-account IAM grant, email-channel verification, and parent
  organization policies remain reviewed apply prerequisites.
- **P1.6 Decide cross-project data access — complete locally.** Define approved
  shared-data buckets or BigQuery datasets and grant only explicit reader/writer
  roles; do not embed project allowlists in Python. All environments now deny
  cross-project data by default. Reviewed declarations create only resource-level
  Storage or BigQuery member grants for eligible job, feed, and notebook identities,
  preserve approval metadata, enforce source environment/location/public-access
  checks, and keep BigQuery query execution in the consumer project. The current
  approved resource set is intentionally empty; source-owner approval and narrow
  external IAM authority are apply prerequisites.

Exit gate:

- a new `dev` environment can be created from documented inputs with remote state;
- it has no public VM address or administrative service; and
- CI, runtime roles, and operators can perform intended actions while negative IAM
  tests prove they cannot cross role or environment boundaries.

### Phase 2 — Durable data, secrets, and metadata

Goal: close the data-loss and credential gaps before deploying real workloads.

- **P2.1 Formalize storage classes — complete locally.** Provision distinct data,
  Airflow-log, temporary, and backup locations where separation improves IAM or
  lifecycle enforcement. GCP and AWS now use four distinct buckets; Azure uses
  four private containers in its ADLS Gen2 account. The existing data location is
  retained, control access moves to logs, jobs and notebooks receive a dedicated
  scratch boundary, no runtime identity can access backups, and every provider
  emits the same storage-location and access-contract outputs. Runtime scratch
  configuration and a staged log-migration procedure are documented; provider
  policy tests cover the resulting boundaries.
- **P2.2 Approve lifecycle policy — complete locally.** Policy version 1 transitions
  current raw data after 90 days without deleting it, expires temporary objects after
  14 days and Airflow logs after 90 days, preserves the newest three noncurrent data
  versions behind a 30-day minimum recovery window on GCP/AWS, and leaves backups
  without automatic deletion. Azure HNS cannot enable blob versioning, so it uses
  30-day blob/container soft delete and explicitly reports overwrite recovery as a
  provider-parity gap rather than claiming the three-version control.
  Terraform outputs and policy tests cover every provider, and exceptions require a
  documented legal, vendor, or reproducibility approval.
- **P2.3 Harden Cloud SQL — complete locally.** Production now requires regional
  HA, private encrypted connectivity, SSD automatic storage growth, eight daily
  backups, seven days of PITR logs, and both Terraform and API deletion protection.
  Explicit maintenance tracks promote updates through development, staging, and
  production; Query Insights is bounded and avoids client-address capture.
  Terraform exposes the effective database
  contract and enforces backup/PITR and production-HA invariants. The provisional
  five-minute RPO and two-hour RTO remain honestly marked pending the P2.5 staging
  restore benchmark rather than being claimed as validated service levels.
- **P2.4 Remove secrets from deployment files — complete locally.** GCP now
  generates the database credential and Fernet key through ephemeral Terraform
  values, writes them through provider write-only fields, and grants the control
  identity access only to Airflow's two config secrets. Vendor and feed secret
  containers are seeded out of band and readable only by their owning job or feed
  identity. Airflow and the runtime credential loader resolve values through
  attached identity; production starts fail closed when a required reference is
  absent. `ctl deploy` rejects known secret-bearing keys and transfers only a
  mode-`0600`, explicit non-secret allowlist as `runtime.env`, never the local
  `.env`. Tests enforce the deployment boundary, credential contract, DAG
  override guard, and per-secret IAM mapping; the seeding and rotation runbook is
  documented.
- **P2.5 Test restoration — complete locally; first live evidence required.** A
  quarterly staging workflow now runs isolated Cloud SQL PITR, versioned-object,
  noncurrent Terraform-state, and notebook-volume restore exercises. The runner
  refuses production projects, requires an explicit project confirmation, uses
  generation preconditions, validates SQL and Airflow from the private control
  node, records measured RPO/RTO and checksums, and cleans up every temporary
  resource. Terraform adds a dedicated non-production recovery identity with
  prefix-scoped data access, create-only evidence access, no production grants,
  and a daily 14-day notebook snapshot schedule that survives source-disk
  deletion. Evidence is stored immutably in the backup bucket and as a 90-day
  workflow artifact. The automation, policy tests, and runbook are complete;
  the provisional recovery objective becomes accepted only after the first live
  staging evidence bundle reports `passed`.
- **P2.6 Define data ownership — complete locally.** A provider-neutral TOML
  manifest now defines raw, derived, artifact, scratch, quarantine, log, and
  backup boundaries with accountable owner and steward groups, workload readers
  and writers, retention, classification, and recovery expectations. Stable
  logical group IDs can be remapped to different teams or contact systems
  without changing workload identities. A standard-library validator rejects
  missing classes, unknown references, unsafe or overlapping prefixes, invalid
  retention values, and stale generated documentation; tests and CI enforce the
  contract. Access changes still require a separately reviewed Terraform IAM
  change, so ownership edits cannot silently grant data-plane access.

Exit gate:

- no production secret is present on disk in deployment configuration;
- backup and PITR policies are visible in Terraform; and
- a restore exercise meets the provisional recovery objectives and produces evidence.

### Phase 3 — Host and platform deployment

Goal: turn the current SSH/Compose scaffold into a repeatable service deployment.

Implementation status (2026-09-17): P3.1 through P3.6 are implemented and
locally validated. Live image replacement, service recovery, database migration,
transactional rollback, configuration-drift, and notebook restore drills remain
environment evidence.

- **P3.1 Replace mutable VM bootstrap — complete locally.** A pinned Packer build
  now creates a private, Shielded Debian 12 image with exact Docker/Compose and
  Ops Agent packages, reviewed installer checksums, OS/SSH hardening, bounded
  container logs, disabled unattended package mutation, and an on-host provenance
  manifest. The temporary builder has neither an external IP nor a cloud service
  account. GCP live roots require exact project-qualified image names, have no
  package-installing startup script, grant telemetry only to long-lived VM roles,
  and expose a one-role maintenance switch for guarded replacement and rollback.
  CI validates the Packer template and policy tests enforce the Terraform image
  contract. The first environment build and replacement still require live GCP
  evidence under the host-image runbook; Phase 4 will automate promotion and
  provenance.
- **P3.2 Add service supervision — complete locally.** The host image now installs
  a hardened systemd template and role-aware supervisor for control, feed, and
  notebook Compose projects. GCP deploys enable/restart the unit and block on JSON
  health; boot activation, consecutive health-failure detection, and systemd's
  three-start/five-minute rate limit replace unbounded Compose restart policies.
  The helper validates private env-file modes, notebook mounts, container state,
  and health checks, while Artifact Registry authentication uses only an attached
  identity and a per-role ephemeral `/run` credential. GCP-only Compose overrides
  preserve the existing AWS/Azure recovery policy. Tests enforce the lifecycle-owner,
  IAM, activation, and health contract. Live reboot and crash-drill evidence is
  still required under the service-supervision runbook.
- **P3.3 Add database migration workflow — complete locally.** Scheduler and API
  boot no longer mutate schema. A profiled candidate-image service performs a
  JSON Alembic-ancestry preflight, stops control only after compatibility passes,
  applies migration files under Airflow 3.3's PostgreSQL advisory migration lock,
  verifies every migration head, and leaves control stopped until the exact tag
  is deployed. Control deployment uses a separate candidate tag and refuses to
  promote or activate it unless the schema is already current. Production
  requires a backup/PITR evidence reference; the runbook defines failure handling,
  database-restore rollback, downgrade restrictions, and a concurrent-owner
  staging drill. CI tests compatibility states and initializes a blank PostgreSQL
  database through the migration role before smoke-testing services. Live lock,
  migration, and rollback evidence is still required.
- **P3.4 Make deploys transactional — complete locally.** `ctl plan` resolves a
  tag to its immutable registry digest; `status` compares desired and running
  references; `doctor` checks provider configuration, schema, health, logs, and
  accepted-release evidence; and `rollback` restores only a preflighted,
  schema-compatible previous release. Deploys stage a candidate, verify its
  baked provider, preserve the active digest, health-gate each host, prove logs
  are retrievable, update batch to the same digest, and accept the transaction
  only after a scratch-storage synthetic job writes and reads its output marker.
  Failures emit remote logs and roll activated roles and batch back in reverse
  order; a first deployment without a prior digest fails stopped. CI covers the
  release model, provider and schema gates, rollback ordering, synthetic probe,
  and supervisor contract. A staged failed-health and sub-15-minute rollback
  drill remains required under the transactional deployment runbook.
- **P3.5 Make configuration declarative — complete locally.** A typed renderer
  now combines Terraform-owned resource identities with versioned base, group,
  environment, and optional extension overlays. Groups can copy a stable profile
  and override portable per-environment policy without forking deployment code;
  infrastructure values cannot be shadowed, unknown keys fail closed, and secret
  values are prohibited. Generated env files and manifests are ignored and
  mode-`0600`, contain deterministic source and effective-configuration
  fingerprints, and retain no raw Terraform output. Production deploys and
  migrations require a current generated file; plan, status, and doctor expose
  source, Terraform, host-runtime, image-digest, and release-evidence drift.
  Release evidence records both image digest and configuration fingerprint, and
  rollback restores the prior runtime file with the prior image. Tests cover
  precedence, group extensions, secret/resource boundaries, reproducibility,
  and drift. A live Terraform-change and host-drift exercise remains required.
- **P3.6 Protect notebooks — complete locally.** GCP now stores notebooks on an
  independent, encrypted persistent disk that survives VM replacement. The baked
  host service safely initializes only the exact attached device, mounts ext4 with
  restrictive options, and verifies a filesystem-UUID marker before Jupyter can
  start. Daily retained snapshots now target the data disk rather than the boot
  disk. The quarterly recovery drill restores a snapshot onto an isolated private
  replacement VM and requires a successful real mount before recording evidence.
  Declarative configuration carries the storage mode and identity; Compose fails
  closed on a missing mount. AWS EFS remains encrypted, access-point and IAM scoped,
  automatically backed up when stack-owned, and deployment now verifies the live
  NFSv4 mount plus its TLS/IAM/access-point fstab contract. Reusing EFS requires a
  recorded cross-environment approval. The notebook runbook covers one-time volume
  migration, private Git synchronization with individual credentials, host
  replacement, snapshot recovery, and AWS Backup validation. Policy and unit tests
  cover these contracts. The first live GCP replacement restore remains required;
  AWS image baking, multi-host visibility, deny/allow, and backup-restore evidence
  remain the later provider-parity work in JPR-017 rather than blocking GCP release.

Exit gate:

- replacing any one VM from its image and configuration restores its service without
  manual package installation;
- host reboot and container crash tests recover automatically; and
- a failed health gate leaves the prior release running or performs an automatic,
  observable rollback.

### Phase 4 — Build, promotion, and release automation

Goal: replace the reference's mutable daily rollout with auditable releases.

Implementation status (2026-09-18): P4.1, P4.1b, P4.2, and P4.3 are implemented and
locally validated; P4.1a also has passing live acceptance evidence. The
automation repository's no-bypass ruleset and policy workflow are live. The
first GCP build-once release requires a provisioned Artifact Registry and the
four protected OIDC publication variables; a live unauthorized-caller exercise
also remains evidence.

- **P4.1 Separate validation from release — complete locally.** Pull requests and
  main-branch CI run unit, architecture, DAG, Terraform, Packer, image smoke,
  policy, and security checks with a read-only token and no environment, secret,
  OIDC, registry-login, or push capability. A dedicated release workflow starts
  only after successful CI for a push from this repository's `main`, rechecks the
  event/repository/branch/conclusion boundary, enters the protected `production`
  environment, checks out the exact validated SHA without persisting credentials,
  and publishes only that immutable tag using provider OIDC. Validation and
  release cache namespaces are isolated. Repository policy tests enforce this
  split and full-SHA action pins; the runbook defines ruleset, environment,
  variable, cloud-claim, and live acceptance requirements.
- **P4.1a Add cross-repository Apps — complete.** A versioned contract separates
  an organization-owned, contents-read
  CI reader installed only on `jarvis` from a contents-write/pull-request-write
  release bot installed only on `jarvis-live`. A protected manual and quarterly
  workflow mints short-lived tokens, rejects personal ownership, all-repository or
  extra installations, and permission drift, then retains secret-free evidence.
  The runbook covers registration, selected-repository installation, protected
  App IDs and private keys, one-key-at-a-time rotation, and quarterly review.
  Both Apps are owned by `qtrpartners`: the CI reader is installed only on
  `jarvis`, and the release bot is installed only on `jarvis-live`. The first
  passing live audit retained both secret-free evidence artifacts in
  [run 35291343716](https://github.com/qtrpartners/jarvis/actions/runs/35291343716)
  on 2026-09-18.
- **P4.1b Centralize reusable automation — complete locally.** Stable validation
  jobs now live in the public, credential-free `qtrpartners/jarvis-automation`
  repository. Its reusable workflow accepts only the hardcoded Jarvis caller,
  declares no inputs or secrets, repeats read-only token permissions on every job,
  and has no environment, OIDC, registry-login, publishing, or deployment authority.
  Jarvis calls the successfully policy-tested upstream workflow at an immutable
  full commit SHA recorded in both CI and `config/automation.toml`; local policy
  tests reject pin drift and privilege expansion. Release authority remains in
  Jarvis. The upstream repository enforces read-only default workflow permissions
  and a no-bypass `main` ruleset requiring pull requests, its passing policy check,
  and resolved review threads; a live denied-caller run remains evidence.
- **P4.2 Build once — complete locally; live GCP acceptance pending.** After
  successful read-only main CI, one protected job performs exactly one GCP
  build-and-push and uses the resulting immutable digest for every later step.
  It runs all runtime smoke roles by digest, creates an SPDX SBOM, enforces the
  HIGH/CRITICAL vulnerability and secret policy, signs and verifies the digest
  with the workflow's keyless Sigstore identity, and publishes signed build
  provenance. A fail-closed manifest binds the commit, lockfile, Dockerfile,
  pinned base image, digest, SBOM, provenance bundle, signature verification,
  smoke log, and scan report; complete evidence is retained for 90 days and
  partial failure evidence for 30. AWS and Azure remain validation-only. The
  first live run requires the external GCP project, Artifact Registry, and
  protected OIDC variables before this item can be marked live-complete.
- **P4.3 Add staging integration tests — complete locally; live GCP acceptance
  pending.** A protected workflow consumes the one retained P4.2 digest and
  signature, checks out its exact source commit, and runs the real Airflow DAG
  against a uniquely named ephemeral staging Cloud Run Job. The successful path
  writes and verifies a synthetic partition, completion marker, minimum size,
  and new remote Airflow logs. The controlled path raises an explicit probe
  failure, requires `airflow dags test` to fail loudly, verifies no partition was
  published, and confirms separate remote logs. A dedicated repository,
  environment, and ref-bound OIDC identity has staging-only Cloud Run,
  batch actAs, probe-read, and integration-log permissions; cleanup and
  secret-free evidence retention are fail-closed. The first live pass requires
  a provisioned staging project, the protected staging variables, access to the
  P4.2 producer repository, and a successful P4.2 release artifact.
- **P4.4 Promote with approval.** Production promotion requires the staging result,
  uses the same digest, records the operator and change, and verifies all deployed
  roles converge on that digest.
- **P4.5 Prove rollback.** Retain known-good digests and compatible configuration;
  exercise control, feed, notebook, and batch rollback at least once per quarter.
- **P4.6 Add release policy.** Define migration windows, dependency update cadence,
  emergency patch rules, and maximum time allowed for critical vulnerability fixes.

Exit gate:

- a commit reaches staging without a long-lived credential;
- production deploy and rollback are one reviewed workflow each; and
- release evidence maps the commit, lockfile, base image, SBOM, signature, digest,
  Terraform plan, and deployed environment; and
- cross-repository reads and promotion PRs use short-lived App tokens, while cloud
  deployment uses OIDC and production hosts possess no GitHub credential.

### Phase 5 — Production data and research functionality

Goal: replace examples with validated business workloads.

- **P5.1 Implement the chosen OHLCV vendor.** Specify authentication, symbols,
  calendars, timestamps, schema, pagination, retries, rate limits, corrections, and
  historical backfill behavior. Add recorded contract tests and malformed-response
  tests.
- **P5.2 Harden the live feed.** Add vendor subscription/authentication, sequence and
  gap detection, durable local spooling before upload, reconnect resubscription,
  backfill reconciliation, malformed-message quarantine, and lag metrics.
- **P5.3 Define data contracts.** Version schemas; validate nulls, uniqueness, ranges,
  partition completeness, checksums, source timestamps, ingestion timestamps, and
  lineage. Quarantine invalid input without publishing a success marker.
- **P5.4 Validate derived jobs.** Give P&L and VaR explicit point-in-time input
  contracts, golden fixtures, numerical tolerances, deterministic outputs, and
  correction/replay procedures.
- **P5.5 Introduce named resource classes.** Define and benchmark `small`, `medium`,
  `large-memory`, and any approved accelerator class. Enforce maximum duration,
  concurrency, quota, and cost labels.
- **P5.6 Add governed analysis access.** Expose Parquet cleanly to notebooks and,
  if required, create BigQuery external or curated tables with reconciliation back to
  object-storage partitions.
- **P5.7 Exercise backfills.** Run a representative multi-day backfill, interruption,
  retry, forced rerun, and downstream rebuild without duplicates or silent partial
  output.
- **P5.8 Establish the research workflow.** Deliver the Tier 1 research-environment
  improvements in section 12: reviewable notebooks, a standard project template,
  reproducible run manifests, notebook-to-batch promotion, dataset discovery, and
  explicit point-in-time safeguards.

Exit gate:

- at least one real ingestion-to-derived-data workflow runs on schedule for 30 days;
- freshness and correctness objectives are met and measured; and
- replaying any day is deterministic or produces a documented vendor-correction
  delta; and
- a clean research project can reproduce a designated result from its code revision,
  environment digest, parameters, and immutable input snapshot without relying on
  notebook-local state.

### Phase 6 — Observability, reliability, and operations

Goal: make failures detectable and recovery routine.

- **P6.1 Set service objectives.** Define targets for scheduled-data freshness,
  successful job completion, feed lag/gap recovery, control-plane availability, and
  recovery time. Error budgets should drive reliability work.
- **P6.2 Export platform metrics.** Capture structured-log metrics for job state and
  duration, Airflow scheduler heartbeat and queue age, feed connection/lag/gaps,
  object publication, Cloud SQL capacity, VM/container health, and Cloud Run quotas.
- **P6.3 Build dashboards and alerts.** Cover the service objectives plus unexpected
  cost, storage growth, backup failure, expiring credentials, dependency age, and
  deployment drift. Choose the paging destination and test routing.
- **P6.4 Add synthetic canaries.** Continuously exercise object read/write, a tiny
  scheduled batch job, remote logs, secret access, and notebook tunnel readiness.
- **P6.5 Write runbooks.** Include scheduler down, database unavailable, failed or
  stuck batch job, missing partition, vendor outage, feed gap, storage permission
  regression, secret rotation, quota exhaustion, rollback, and each restore path.
- **P6.6 Run failure drills.** Test feed termination during buffered writes, control
  node loss, database failover/restore, batch timeout, malformed vendor data, revoked
  IAM, and unavailable dependency services.
- **P6.7 Establish operating cadence.** Weekly health/cost review, monthly dependency
  and access review, quarterly restore/rollback drill, and annual threat-model and
  capacity review.

Exit gate:

- every production objective has a dashboard, alert owner, and runbook;
- synthetic failures page the intended destination and recovery is timed; and
- the platform completes a 30-day operating review with no unknown critical blind
  spot.

### Phase 7 — Provider parity after GCP production

Goal: preserve portability with evidence rather than interface-only scaffolding.

- Run the same storage, identity, dispatch, output-verification, secrets, logging,
  promotion, rollback, and recovery contract tests on AWS.
- Replace Azure ACI dispatch with Container Apps Jobs before calling Azure production
  ready, unless an ACI load and reliability test justifies retaining it.
- Add provider-specific live environment roots, budgets, monitoring, backup tests,
  and release workflows.
- Publish an explicit capability matrix. A provider is `experimental`, `staging`, or
  `production`; code presence alone does not qualify it as production.

Exit gate:

- each production-labelled provider independently passes the definition in section 5.

## 7. Verification matrix

| Layer | Pull request | Staging | Production/drill |
|---|---|---|---|
| Python | unit, types, lint, architecture rules, job fixtures | real adapter contract and synthetic DAG | data-quality and freshness canaries |
| Image | clean frozen build, role smoke, SBOM, vulnerability policy | run by digest through every role | signature/admission verification and rollback |
| Terraform | format, validate, lint, policy, speculative plan | apply and idempotent re-plan | reviewed plan, drift detection, restore of state |
| GitHub access | ruleset/config tests, explicit workflow permissions, App permission review | cross-repo allow/deny tests and OIDC claim test | quarterly team/App/PAT/deploy-key audit and emergency-access exercise |
| IAM/secrets | static policy checks | positive and negative role tests | quarterly access review and secret rotation |
| Storage | local atomicity/idempotency tests | cloud lifecycle and version recovery | sampled recovery and retention audit |
| Database | migration compatibility | backup restore and application smoke | scheduled PITR drill |
| Batch | dispatcher contract | success, failure, timeout, retry, large-memory test | SLO, quota, cost, and rollback evidence |
| Feed | parser and state-machine tests | disconnect, malformed data, spool replay | gap alert and reconciliation drill |
| Hosts | image build and policy scan | replacement, reboot, disk restore | rolling replacement and incident exercise |
| Notebook storage | mount/ownership configuration tests | EFS TLS/IAM allow/deny, multi-host visibility, backup restore | missing-mount fail-closed test and quarterly recovery drill |
| Research reproducibility | notebook execution, template, and manifest schema tests | reproduce a fixture through notebook and batch paths | sampled published-result reproduction and provenance audit |
| Research access | policy and negative-access tests | per-role dataset/export checks | quarterly entitlement, export, and shared-workspace review |

## 8. Initial service objectives and recovery targets

These are starting targets to validate during staging, not promises made without data.

| Concern | Initial target |
|---|---|
| Scheduled datasets | 99% of daily partitions published by their documented deadline over 30 days |
| Batch correctness | zero successful Airflow tasks with a missing/invalid completion marker |
| Feed detection | disconnect or sustained lag detected within 2 minutes |
| Feed recovery | detected gap reconciled within 30 minutes when the vendor API is available |
| Airflow metadata RPO | 5 minutes or better through PITR |
| Airflow metadata RTO | 2 hours, validated by restore exercise |
| Object data RPO | no acknowledged published partition lost; recovery through versioning or source replay |
| Deployment rollback | prior compatible digest restored within 15 minutes |
| Critical alert delivery | test notification acknowledged within the agreed on-call window |
| Research reproducibility | 100% of designated published results carry a valid run manifest and immutable input references |
| Interactive cost attribution | 100% of remote notebook and research-batch compute is labelled to a user, project, environment, and resource class |

## 9. Critical path and first implementation backlog

The critical path is:

```text
reproducible build
  -> GitHub organization access + immutable repository links
  -> GCP environment + remote state + networking
  -> runtime secrets + recoverable storage/database
  -> replaceable hosts + transactional deploy
  -> staged digest promotion
  -> real adapters and data contracts
  -> reproducible research workflow + dataset discovery
  -> observability + 30-day production proving period
```

Create the first work items in this order:

1. `JPR-001` — replace the editable `ggstyle` dependency with a released package or
   commit-pinned transitional dependency and restore a valid frozen lock.
2. `JPR-002` — move production repositories under the chosen GitHub organization;
   create teams, base permissions, `CODEOWNERS`, and organization rulesets.
3. `JPR-003` — record the repository topology, GCP-first, and no-Kubernetes decisions.
4. `JPR-004` — add clean image builds and five-role smoke tests to CI.
5. `JPR-005` — create `jarvis-ci-reader` and `jarvis-release-bot`; test their
   selected-repository and permission boundaries.
6. `JPR-006` — add GCP remote-state bootstrap and `dev/stage/prod` live roots.
7. `JPR-007` — manage/import the private VPC, subnet, NAT, DNS, and IAP access.
8. `JPR-008` — configure protected GitHub environments and GCP OIDC; prove repository,
   environment, and cloud IAM boundaries.
9. `JPR-009` — split secret and non-secret runtime configuration; remove production
   database and Fernet values from `.env` deployment flow.
10. `JPR-010` — harden Cloud SQL and automate a staging PITR restore test.
11. `JPR-011` — add temporary/log/data lifecycle policies and object recovery tests.
12. `JPR-012` — build a versioned VM image and systemd-managed service deployment.
13. `JPR-013` — create the App-authenticated promotion PR flow into `jarvis-live`.
14. `JPR-014` — implement staged digest promotion, health gates, and rollback.
15. `JPR-015` — centralize stable CI components in `jarvis-automation` with restricted
    caller access and immutable workflow pins.
16. `JPR-016` — select the real market-data vendor and approve its data contract.
17. `JPR-017` — bake and validate `amazon-efs-utils` in the AWS notebook machine
    image; test multi-host sharing, cross-environment denial/allow, missing-mount
    failure, and EFS backup recovery.
18. `JPR-018` — add Jupytext, notebook hygiene checks, and a standard research-project
    template with `src`, `notebooks`, `tests`, `reports`, ownership, and data-contract
    scaffolding.
19. `JPR-019` — define run-manifest schema v1 and emit it from the shared job harness;
    capture code, image, lock, parameters, seeds, input snapshots, hardware, and
    output references.
20. `JPR-020` — add notebook-to-batch submission, status, log, cancellation, and
    result retrieval without requiring the browser session to remain alive.
21. `JPR-021` — deploy an authenticated MLflow tracking service with a dedicated
    metadata database, object-storage artifacts, backup policy, and retention rules.
22. `JPR-022` — introduce a dataset catalog and OpenLineage events for Airflow and the
    shared job harness; verify an end-to-end input-to-result lineage graph.
23. `JPR-023` — add point-in-time data helpers and tests for corrections, market
    calendars, time zones, survivorship bias, and look-ahead leakage.

`JPR-016` can be investigated in parallel, but implementation should not outrun the
storage, secret, and recovery foundations.

## 10. Risks and controls

| Risk | Control |
|---|---|
| Three-cloud work prevents one production deployment | GCP-first gate; other providers cannot block GCP release |
| A personal account or PAT becomes production infrastructure | Organization ownership, team grants, separate GitHub Apps, short-lived tokens, and quarterly credential inventory |
| Cross-repo reader is compromised | Read-only App, selected repositories, no secrets repository, token generated per job, and provenance records every consumed SHA |
| Promotion bot can self-deploy | Bot opens PR only; rulesets, CODEOWNERS, protected environment, and human approval prevent merge or deployment bypass |
| Shared workflow leaks private implementation through logs | Restrict allowed callers, prohibit outside-collaborator callers unless reviewed, minimize outputs, and test log redaction |
| Vendor behavior remains undefined | Select vendor early; require sandbox, recorded responses, rate-limit and correction semantics |
| Feed loses buffered data on process/host failure | Durable spool, acknowledgement boundary, sequence tracking, and reconciliation |
| Airflow metadata credential leaks through `.env` | Runtime secret fetch with attached identity; scan hosts and deployment artifacts |
| SSH deployment partially updates roles | Digest manifest, health-gated role convergence, status command, and rollback |
| Startup scripts drift or package repositories change | Versioned machine images and replacement-based upgrades |
| Infrastructure plans are technically valid but unaffordable | Budgets, cost labels, quotas, representative load tests, and monthly cost review |
| Data reruns silently differ | Source checksums, schema versions, lineage, deterministic transforms, and correction manifests |
| Notebook output cannot be reproduced | Reviewable text pairing, clean execution tests, immutable run manifests, and published-result sampling |
| Shared notebook storage causes overwrite or data leakage | Per-team roots, individual identity where needed, no concurrent `.ipynb` editing, and object storage as source of truth |
| Interactive research consumes unbounded resources | Named profiles, idle shutdown, duration/concurrency limits, project labels, and budget alerts |
| Production label is applied to untested providers | Capability matrix backed by provider-specific integration and recovery evidence |

## 11. Completion artifacts

The production release should leave behind:

- architecture decision records for the decisions in sections 2 and 3;
- an organization access matrix, team membership review, GitHub App permission and
  installation inventory, ruleset export, and PAT/deploy-key exception register;
- Terraform bootstrap, live environments, reviewed plans, and drift reports;
- a signed image digest, SBOM, provenance, vulnerability report, and promotion record;
- data contracts, adapter contract tests, lineage and data-quality evidence;
- a research-project template, notebook policy, run-manifest schema, dataset catalog,
  and at least one independently reproduced published result;
- dashboards, alert routes, service objectives, and cost budgets;
- deploy, rollback, restore, backfill, feed-gap, and incident runbooks;
- results from IAM, load, failure, rollback, and restore exercises; and
- a provider capability matrix that names GCP production and accurately labels AWS
  and Azure according to their test evidence.

## 12. Research environment improvement roadmap

The research environment should optimize for rapid exploration without allowing a
notebook session to become the only record of how a result was produced. Notebooks
are an interactive interface, object storage contains authoritative datasets and
artifacts, batch services perform durable computation, and run manifests connect the
code, environment, inputs, parameters, and outputs.

This is a capability backlog, not a requirement to deploy every listed service. Tier 1
extends the production critical path. Tier 2 should follow after the underlying data,
identity, and recovery controls are stable. Tier 3 remains conditional on measured
team and workload needs.

### Tier 1 — Reproducible individual research

1. **Adopt a reproducible notebook standard.** Pair maintained notebooks with
   reviewable `py:percent` or Markdown source using
   [Jupytext](https://jupytext.org/using/paired-notebooks/). Strip incidental output,
   validate metadata, and require designated notebooks to execute from a clean kernel.
   Scratch notebooks remain permitted but cannot be published as evidence.
2. **Emit research run manifests.** Record the Git commit, image digest, dependency
   lock hash, parameters, random seeds, immutable input identifiers and checksums,
   timestamps, resource profile, actor/project identity, metrics, and output URIs.
   Version the manifest schema and store each manifest beside its durable result.
3. **Provide a standard project template.** Add `ctl research init` with `src/`,
   `notebooks/`, `tests/`, `reports/`, configuration, ownership, environment, and data
   contract scaffolding. Include a small end-to-end example and CI checks.
4. **Promote notebook work to batch.** Add a Python API and `ctl research run` flow
   that submits a versioned function to the existing batch backend and returns a run
   identifier, status, logs, cancellation control, manifest, and result URI. Closing
   a browser must not terminate durable work.
5. **Catalog datasets and immutable snapshots.** Make dataset description, owner,
   schema, partitions, freshness, source, licensing restrictions, quality state, and
   snapshot identifiers searchable. Every published result references an immutable
   snapshot or a documented point-in-time query.
6. **Build point-in-time research safeguards.** Standardize market calendars, time
   zones, correction versions, `as_of` semantics, universe membership, and corporate
   actions. Add automated checks for future information, survivorship bias, and
   accidental cross-environment reads.
7. **Complete shared-storage operations.** Finish `JPR-017`, monitor EFS capacity,
   throughput and connections, test backup recovery, and use separate roots or access
   points for teams and environments. EFS remains working storage rather than the
   authoritative data or provenance store.

Tier 1 exit criteria:

- a new project is created from the template and passes CI without manual setup;
- one notebook result is reproduced through the batch path in a clean environment;
- the resulting manifest resolves every code, environment, input, and output
  reference; and
- losing and replacing the notebook host does not lose the published result or the
  evidence needed to reproduce it.

### Tier 2 — Team research and governance

8. **Add experiment tracking.** Operate an authenticated
   [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking) service for shared
   parameters, metrics, dataset references, models, and artifacts. Use a database
   separate from Airflow metadata, object storage for artifacts, attached identity,
   backups, retention, and environment-aware access controls.
9. **Collect automatic lineage.** Use the maintained Airflow
   [OpenLineage provider](https://openlineage.io/docs/integrations/airflow/) and emit
   matching events from the job harness. Connect datasets, jobs, runs, code versions,
   manifests, and experiment records without making the lineage backend a runtime
   dependency of successful data publication.
10. **Offer controlled environment profiles.** Publish locked `core`, `quant`, `ml`,
    and any justified `gpu` image profiles with digests, owners, resource limits, and
    support windows. Do not allow runtime `pip install` changes to masquerade as a
    reproducible production environment.
11. **Provide resource-aware interactive compute.** Let users choose approved CPU,
    high-memory, or accelerator profiles and dispatch large work remotely. Enforce
    idle shutdown, maximum duration, concurrency, quotas, and user/project/environment
    cost labels.
12. **Expose research observability.** Present dataset freshness, run state, queue
    time, resource use, estimated cost, failed quality checks, EFS health, and upstream
    incidents in a small portal, CLI view, or Jupyter extension.
13. **Enforce research data guardrails.** Separate research, production, and
    restricted-data identities; default production access to read-only; classify
    sensitive fields; audit queries and exports; and require approval for bulk or
    cross-environment movement.
14. **Publish executable reports.** Generate immutable HTML/PDF reports from reviewed
    notebooks or text sources. Attach the run manifest, snapshot identifiers, code
    revision, image digest, author, reviewer, and generation time.
15. **Add content-addressed result caching.** Hash code, parameters, environment, and
    input snapshots so identical deterministic jobs can reuse verified output. Allow
    explicit cache bypass for vendor corrections and nondeterministic research.
16. **Define research retention states.** Classify work as scratch, active, published,
    or regulated. Expire abandoned scratch artifacts automatically while retaining
    published manifests and all referenced evidence for the approved period.

### Tier 3 — Conditional multi-user capabilities

17. **Introduce per-user notebook tenancy when justified.** If simultaneous users,
    independent access revocation, or untrusted workloads make a shared host unsafe,
    adopt individually authenticated and isolated servers such as
    [JupyterHub](https://jupyterhub.readthedocs.io/en/latest/explanation/singleuser.html).
    Give each user a separate workspace identity and storage root; expose shared data
    through explicit read or write grants. This is not part of the initial single-user
    production gate.
18. **Enable real-time collaboration only after individual identity.** JupyterLab 4
    supports an optional
    [real-time collaboration extension](https://jupyterlab.readthedocs.io/en/stable/user/rtc.html),
    but it must have an approved authentication, authorization, audit, and recovery
    model. Collaboration does not replace Git review, run manifests, or experiment
    tracking, and the same notebook must not be edited concurrently through ordinary
    EFS-backed sessions.

Before adopting an additional platform such as a table format, distributed execution
engine, catalog UI, or feature store, document the measured limitation in the current
Polars/DuckDB/Parquet and cloud-batch design. Prefer extending the existing interfaces
until concurrency, scale, transaction, or discovery requirements justify the new
operational surface.
