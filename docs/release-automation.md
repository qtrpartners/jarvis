# CI and release trust boundary

Jarvis separates unprivileged validation from registry mutation. Pull-request
code is never evaluated by a job that can request a cloud identity, enter a
GitHub environment, read a repository secret, log in to a registry, or push an
image.

## Workflow contract

| Workflow | Trigger | Authority | Result |
|---|---|---|---|
| `ci.yml` | Pull request and push to `main` | Read-only repository token; no environment, secret, or OIDC permission | Calls the centralized validation workflow at an immutable revision |
| `release.yml` | Successful completion of `ci` for a push to this repository's `main` branch | Read-only repository token, attestation authority, and OIDC inside the protected `production` environment | One tested, signed, immutable GCP image plus retained release evidence |
| `recovery-drill.yml` | Schedule or explicit dispatch | Staging OIDC inside the protected `staging` environment | Isolated quarterly recovery evidence; never image publication |

The release workflow checks the CI conclusion, original event, branch, and
origin repository before its job can enter `production`. It checks out
`workflow_run.head_sha`, disables persisted checkout credentials, verifies that
exact 40-character commit locally, and tags the image with the same SHA.
Validation and release use separate BuildKit cache scopes so an untrusted pull
request cannot populate a cache consumed by a privileged build.

## Build-once GCP release

P4.2 publishes only the GCP image. AWS and Azure stay in credential-free CI
validation until their production release paths have equivalent identity and
registry controls. The release job fails closed when any required GCP variable
is absent or when `GCP_IMAGE` is not an untagged Artifact Registry repository in
the configured project.

The job invokes `docker/build-push-action` exactly once. Every subsequent step
uses its `sha256` output:

1. pull the published image by digest and run every runtime smoke role;
2. create an SPDX JSON SBOM from that exact digest;
3. reject fixed HIGH or CRITICAL vulnerabilities and detected secrets;
4. sign the digest with cosign's GitHub Actions keyless identity and verify the
   signature against this workflow's exact identity;
5. create GitHub/Sigstore build provenance and publish it as an OCI
   attestation; and
6. retain a 90-day evidence artifact containing the digest, commit, base-image
   digest, lockfile and Dockerfile hashes, SBOM, provenance bundle, signature
   verification, smoke log, and vulnerability report.

`scripts/release_evidence.py` independently validates those inputs before it
writes `release.json`. A failed smoke test, malformed digest, missing
attestation, or blocked vulnerability cannot produce passing release evidence.
Downstream staging and promotion workflows must consume
`image.reference` from that manifest and must never rebuild the source commit.

## Centralized validation

Stable validation logic lives in the public, credential-free
[`qtrpartners/jarvis-automation`](https://github.com/qtrpartners/jarvis-automation)
repository. GitHub permits a public caller such as Jarvis to use reusable
workflows only from public repositories, so confidentiality cannot be the access
boundary. Instead, the called workflow hard-fails unless `github.repository` is
an explicitly allowlisted caller.

The caller reference is recorded in `config/automation.toml` and pinned to a full
commit SHA in `ci.yml`. The called workflow accepts no inputs or secrets, repeats
`contents: read` on every job, checks out the exact caller SHA without persisted
credentials, and contains no environment, cloud authentication, registry login,
or publishing capability. GitHub also prevents a called workflow from elevating
beyond the caller's token permissions.

To update centralized validation:

1. Change `jarvis-automation` and pass its `workflow-policy` job.
2. Merge the reviewed change and copy its full commit SHA.
3. Update both `ci.yml` and `config/automation.toml` in the same Jarvis pull
   request.
4. Run `uv run pytest -q tests/test_workflow_policy.py` and review the upstream
   workflow diff before merging.

Never pin a branch or moving tag. Production OIDC, environments, publishing,
deployment, and rollback remain local to their owning repository.

The automation repository itself uses read-only default workflow permissions and
an active `main` ruleset with no bypass actors. Changes require a pull request,
the `workflow-policy` check, an up-to-date branch, and resolved review threads;
force-pushes and branch deletion are blocked. Independent code-owner approval
must be enabled when a second trusted maintainer or organization team exists.

The repository policy tests reject a pull-request workflow containing any of
these capabilities:

- `id-token: write`;
- a GitHub environment or `secrets` context;
- a cloud authentication action;
- a registry login; or
- an image build with `push: true`.

The same tests require every third-party action in every workflow to use a full
commit SHA. Run them directly with:

```bash
uv run pytest -q tests/test_workflow_policy.py
```

## Required GitHub configuration

Apply an organization ruleset or branch protection rule to `main` that requires
a pull request, code-owner review for `.github/`, fresh approval after changes,
resolved conversations, and these successful reusable-workflow jobs:

- `validation / authorize caller`;
- `validation / lint-and-test`;
- `validation / terraform`;
- `validation / host-image-template`;
- `validation / security`; and
- every `validation / image-test` matrix job.

Do not make the release workflow a merge check: it intentionally runs only
after the validated commit reaches `main`.

Configure the `production` environment with:

- deployment branches restricted to `main`;
- required release-manager review and prevention of self-review;
- administrator bypass disabled where the repository plan supports it; and
- no long-lived cloud credential secrets.

Provider identifiers belong in protected environment variables, not secrets:

| Provider | Environment variables |
|---|---|
| GCP | `GCP_IMAGE`, `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_CI_SERVICE_ACCOUNT` |

All four values are required. An empty value fails the release rather than
silently producing a successful no-op. `GCP_IMAGE` must use
`REGION-docker.pkg.dev/PROJECT_ID/REPOSITORY/IMAGE` form without a tag or digest.

Cloud trust must independently bind the token to the exact organization,
repository, numeric repository/owner identifiers where supported, the
`production` environment, and `refs/heads/main`. The workflow-side predicates
are defense in depth, not a replacement for cloud-side claim restrictions.

## Cross-repository GitHub Apps

The authoritative least-privilege contract is
`config/github-apps.toml`. It defines two independent principals:

| App | Selected repositories | Repository permissions |
|---|---|---|
| `qtrpartners-jarvis-ci-reader` | `jarvis` | Contents read; metadata read |
| `qtrpartners-jarvis-release-bot` | `jarvis-live` | Contents write; pull requests write; metadata read |

The Apps must be owned by the same GitHub organization that owns Jarvis. A
personal App, an all-repositories installation, or an installation on any extra
repository fails the automated audit. The release App has no administration,
Actions, environments, secrets, members, deployments, or merge authority. The
reader has no write permission. Webhooks are disabled for both Apps because
Jarvis mints installation tokens only from explicit workflows.

Create and install the Apps in this order:

1. Transfer or create `jarvis` and `jarvis-live` under the production
   organization. Do not register a personal substitute.
2. In the organization's developer settings, register `qtrpartners-jarvis-ci-reader` with
   only repository `Contents: Read-only`; install it on selected repository
   `jarvis` only.
3. Register a separate `qtrpartners-jarvis-release-bot` with only repository
   `Contents: Read and write` and `Pull requests: Read and write`; install it on
   selected repository `jarvis-live` only.
4. Create the protected `github-app-audit` environment in Jarvis. Restrict it to
   `main`, require a release-manager reviewer, prevent self-review, and disable
   administrator bypass where the repository plan supports it.
5. Add `JARVIS_CI_READER_APP_ID` and `JARVIS_RELEASE_BOT_APP_ID` as environment
   variables. Add `JARVIS_CI_READER_PRIVATE_KEY` and
   `JARVIS_RELEASE_BOT_PRIVATE_KEY` as environment secrets. Never place a key in
   a repository, Actions variable, Terraform value, artifact, or runtime host.
6. Manually run `github-app-boundary`. Approve the environment as a different
   release manager and retain both passing JSON artifacts with the change
   record. The workflow also runs quarterly and requires approval before it can
   read either key.

`scripts/github_app_boundary.py` uses a short-lived App JWT to inspect installation
metadata and each short-lived installation token to enumerate repositories. It
fails unless the owner is an organization, repository selection is `selected`,
the complete installed-repository set exactly matches the contract, and the
complete permission set is exact. The evidence contains App and installation
metadata but never the token or private key.

Rotate each private key at least quarterly and immediately after suspected
exposure: generate a second key, replace only that App's protected environment
secret, run the audit, then revoke the old key. Never rotate both Apps in one
change. Review App ownership, installation repositories, permissions, recent
token use, environment reviewers, and retained evidence during the same audit.

Changing an App boundary requires a reviewed change to
`config/github-apps.toml` first, followed by the GitHub setting change and a
passing audit. Adding a repository speculatively is prohibited.

## Acceptance check

After the repository settings and GCP environment variables exist:

1. Open a pull request and confirm only `ci` runs and no environment approval or
   cloud authentication is requested.
2. Merge a reviewed change to `main`; confirm `ci` succeeds first.
3. Confirm `release` references the same triggering SHA and waits for the
   `production` environment approval.
4. Approve it as a different authorized reviewer and verify the registry tag is
   exactly that SHA.
5. Verify the retained `release.json` binds that SHA to the tested registry
   digest, pinned base image, lockfile, SPDX SBOM, provenance bundle, verified
   keyless signature, smoke log, and vulnerability report.
6. Confirm the cloud audit log records workload federation and no service-account
   key or other long-lived credential was used.

P4.2 stops at one validated and signed image digest with retained evidence.
Staging deployment and production promotion consume that digest in P4.3 and
P4.4; neither phase is permitted to rebuild it.

P4.3 is implemented by the protected `staging integration` workflow. It
revalidates the retained manifest and keyless signature, runs the released DAG
against an ephemeral staging Cloud Run Job, verifies its marked partition and
remote logs, then proves a controlled execution fails the DAG without
publishing a partition. Follow the [staging integration runbook](staging-integration.md)
for identity, variables, cross-project registry access, evidence, and cleanup.
