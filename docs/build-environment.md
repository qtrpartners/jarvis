# Supported build environment

Jarvis releases are built from locked Python dependencies and digest-pinned
container inputs. Update these versions deliberately and review the resulting
lockfile, image scan, smoke-test, and release evidence together.

| Tool or input | Supported release baseline |
|---|---|
| Python | 3.12 only |
| uv | 0.12.5 |
| Terraform CLI | 1.13.3 |
| Packer CLI | 1.16.x |
| Packer Google Compute plugin | 1.2.7 |
| Docker Engine | 29.x |
| Docker Buildx | 0.36.x |
| Docker Compose | v2 |
| Runtime base | `python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea` |
| uv build image | `ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1` |
| CI PostgreSQL | `postgres:16@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94` |

The runtime image records the full Git revision and base-image digest as OCI
labels. Production release evidence must record the resulting image digest;
Git tags and image tags are discovery aids, not immutable deployment identity.
The build-once release manifest also records SHA-256 hashes for `uv.lock`, the
Dockerfile, SPDX SBOM, Sigstore provenance bundle, cosign verification output,
smoke log, and vulnerability report. Staging and production consume its digest
and never rebuild the source commit.

Dependency automation may propose newer tags, but a maintainer must resolve the
corresponding digest and run the complete clean-build and five-role smoke test
before merging.

GCP VM prerequisites are built separately as a versioned host image. See the
[host-image build and replacement runbook](host-images.md); live Terraform uses
only exact image self-links and never an image family.
