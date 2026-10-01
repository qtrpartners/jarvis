"""Create a secret-free evidence manifest for one immutable GCP image build."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*/[A-Za-z0-9_./-]+$")
BASE_PATTERN = re.compile(r"^FROM\s+\S+@(sha256:[0-9a-f]{64})\s*$", re.MULTILINE)
EXPECTED_SMOKE_RESULT = "all Jarvis roles passed for gcp"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"


class EvidenceError(RuntimeError):
    """Raised when release evidence is incomplete or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, media_type: str) -> dict[str, str]:
    if not path.is_file() or path.stat().st_size == 0:
        raise EvidenceError(f"required evidence file is missing or empty: {path}")
    return {
        "file": path.name,
        "media_type": media_type,
        "sha256": _sha256(path),
    }


def _load_json(path: Path) -> Any:
    _artifact(path, "application/json")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise EvidenceError(f"evidence file is not valid JSON: {path}") from error


def _base_digest(dockerfile: Path) -> str:
    if not dockerfile.is_file():
        raise EvidenceError(f"Dockerfile does not exist: {dockerfile}")
    matches = BASE_PATTERN.findall(dockerfile.read_text())
    if len(matches) != 1:
        raise EvidenceError("Dockerfile must contain exactly one digest-pinned FROM image")
    return matches[0]


def _validate_attestation_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path:
        raise EvidenceError("attestation URL must be an absolute github.com HTTPS URL")


def _validate_scan(report: Any) -> None:
    if not isinstance(report, dict) or not isinstance(report.get("Results"), list):
        raise EvidenceError("Trivy report has an unexpected structure")
    blocked: list[str] = []
    for result in report["Results"]:
        if not isinstance(result, dict):
            continue
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise EvidenceError("Trivy vulnerabilities must be a list")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                continue
            severity = str(vulnerability.get("Severity", "")).upper()
            if severity in {"HIGH", "CRITICAL"}:
                blocked.append(str(vulnerability.get("VulnerabilityID", "unknown")))
    if blocked:
        raise EvidenceError(f"Trivy report contains blocked vulnerabilities: {sorted(blocked)!r}")


def build_evidence(
    *,
    image: str,
    digest: str,
    commit: str,
    repository: str,
    ref: str,
    workflow: str,
    run_id: str,
    run_attempt: str,
    lockfile: Path,
    dockerfile: Path,
    sbom: Path,
    provenance_bundle: Path,
    signature_verification: Path,
    smoke_log: Path,
    scan_report: Path,
    attestation_url: str,
    certificate_identity: str,
) -> dict[str, Any]:
    if not COMMIT_PATTERN.fullmatch(commit):
        raise EvidenceError("release commit must be a full lowercase Git SHA")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise EvidenceError("image digest must be a full lowercase sha256 digest")
    if not IMAGE_PATTERN.fullmatch(image) or "@" in image or ":" in image:
        raise EvidenceError("image must be an untagged fully qualified repository name")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise EvidenceError("repository must use owner/name form")
    if ref != "refs/heads/main":
        raise EvidenceError("release evidence must originate from refs/heads/main")
    if not run_id.isdigit() or not run_attempt.isdigit():
        raise EvidenceError("run identifiers must be numeric")
    _validate_attestation_url(attestation_url)
    expected_identity = f"https://github.com/{repository}/{workflow}@{ref}"
    if certificate_identity != expected_identity:
        raise EvidenceError(
            f"certificate identity is {certificate_identity!r}, expected {expected_identity!r}"
        )

    sbom_payload = _load_json(sbom)
    if not isinstance(sbom_payload, dict) or not str(
        sbom_payload.get("spdxVersion", "")
    ).startswith("SPDX-"):
        raise EvidenceError("SBOM is not an SPDX JSON document")

    provenance_payload = _load_json(provenance_bundle)
    if not isinstance(provenance_payload, (dict, list)) or not provenance_payload:
        raise EvidenceError("provenance bundle is empty")

    signature_payload = _load_json(signature_verification)
    if not isinstance(signature_payload, (dict, list)) or not signature_payload:
        raise EvidenceError("cosign verification output is empty")

    scan_payload = _load_json(scan_report)
    _validate_scan(scan_payload)

    smoke = smoke_log.read_text() if smoke_log.is_file() else ""
    if EXPECTED_SMOKE_RESULT not in smoke.splitlines():
        raise EvidenceError("five-role GCP smoke test did not report success")

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "pass",
        "source": {
            "repository": repository,
            "commit": commit,
            "ref": ref,
            "workflow": workflow,
            "run_id": int(run_id),
            "run_attempt": int(run_attempt),
        },
        "image": {
            "cloud": "gcp",
            "name": image,
            "digest": digest,
            "reference": f"{image}@{digest}",
            "base_digest": _base_digest(dockerfile),
        },
        "inputs": {
            "lockfile": _artifact(lockfile, "application/octet-stream"),
            "dockerfile": _artifact(dockerfile, "text/x-dockerfile"),
        },
        "attestations": {
            "provenance": {
                "url": attestation_url,
                "bundle": _artifact(
                    provenance_bundle, "application/vnd.dev.sigstore.bundle+json"
                ),
            },
            "sbom": _artifact(sbom, "application/spdx+json"),
            "signature": {
                "certificate_identity": certificate_identity,
                "oidc_issuer": OIDC_ISSUER,
                "verification": _artifact(signature_verification, "application/json"),
            },
        },
        "tests": {
            "smoke": {
                "status": "pass",
                "roles": [
                    "migration",
                    "scheduler",
                    "api-server",
                    "jupyter",
                    "feed",
                    "job",
                    "release-probe",
                ],
                "evidence": _artifact(smoke_log, "text/plain"),
            },
            "vulnerability_policy": {
                "status": "pass",
                "blocked_severities": ["HIGH", "CRITICAL"],
                "ignore_unfixed": True,
                "evidence": _artifact(scan_report, "application/json"),
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--lockfile", type=Path, required=True)
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--provenance-bundle", type=Path, required=True)
    parser.add_argument("--signature-verification", type=Path, required=True)
    parser.add_argument("--smoke-log", type=Path, required=True)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--attestation-url", required=True)
    parser.add_argument("--certificate-identity", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence = build_evidence(
            image=args.image,
            digest=args.digest,
            commit=args.commit,
            repository=args.repository,
            ref=args.ref,
            workflow=args.workflow,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            lockfile=args.lockfile,
            dockerfile=args.dockerfile,
            sbom=args.sbom,
            provenance_bundle=args.provenance_bundle,
            signature_verification=args.signature_verification,
            smoke_log=args.smoke_log,
            scan_report=args.scan_report,
            attestation_url=args.attestation_url,
            certificate_identity=args.certificate_identity,
        )
    except EvidenceError as error:
        print(f"release evidence failed: {error}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"release evidence written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
