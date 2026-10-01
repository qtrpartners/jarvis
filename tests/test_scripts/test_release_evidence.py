from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.release_evidence import EvidenceError, build_evidence

COMMIT = "a" * 40
DIGEST = f"sha256:{'b' * 64}"
BASE_DIGEST = f"sha256:{'c' * 64}"


def evidence_files(tmp_path: Path) -> dict[str, Path]:
    files = {
        "lockfile": tmp_path / "uv.lock",
        "dockerfile": tmp_path / "Dockerfile",
        "sbom": tmp_path / "sbom.spdx.json",
        "provenance_bundle": tmp_path / "provenance.json",
        "signature_verification": tmp_path / "signature.json",
        "smoke_log": tmp_path / "smoke.log",
        "scan_report": tmp_path / "trivy.json",
    }
    files["lockfile"].write_text("locked\n")
    files["dockerfile"].write_text(f"FROM python:3.12-slim@{BASE_DIGEST}\n")
    files["sbom"].write_text(json.dumps({"spdxVersion": "SPDX-2.3"}))
    files["provenance_bundle"].write_text(json.dumps({"mediaType": "bundle"}))
    files["signature_verification"].write_text(json.dumps([{"critical": {}}]))
    files["smoke_log"].write_text("all Jarvis roles passed for gcp\n")
    files["scan_report"].write_text(json.dumps({"Results": []}))
    return files


def build(tmp_path: Path, **overrides: object) -> dict[str, object]:
    file_keys = {
        "lockfile",
        "dockerfile",
        "sbom",
        "provenance_bundle",
        "signature_verification",
        "smoke_log",
        "scan_report",
    }
    files = {} if file_keys <= overrides.keys() else evidence_files(tmp_path)
    values: dict[str, object] = {
        "image": "us-central1-docker.pkg.dev/jarvis-prod/research/base",
        "digest": DIGEST,
        "commit": COMMIT,
        "repository": "qtrpartners/jarvis",
        "ref": "refs/heads/main",
        "workflow": ".github/workflows/release.yml",
        "run_id": "123",
        "run_attempt": "1",
        "attestation_url": "https://github.com/qtrpartners/jarvis/attestations/42",
        "certificate_identity": (
            "https://github.com/qtrpartners/jarvis/.github/workflows/"
            "release.yml@refs/heads/main"
        ),
        **files,
    }
    values.update(overrides)
    return build_evidence(**values)  # type: ignore[arg-type]


def test_build_evidence_binds_immutable_image_and_inputs(tmp_path: Path) -> None:
    evidence = build(tmp_path)

    assert evidence["result"] == "pass"
    assert evidence["image"] == {
        "cloud": "gcp",
        "name": "us-central1-docker.pkg.dev/jarvis-prod/research/base",
        "digest": DIGEST,
        "reference": f"us-central1-docker.pkg.dev/jarvis-prod/research/base@{DIGEST}",
        "base_digest": BASE_DIGEST,
    }
    assert evidence["source"]["commit"] == COMMIT
    assert len(evidence["inputs"]["lockfile"]["sha256"]) == 64
    assert evidence["tests"]["smoke"]["status"] == "pass"
    serialized = json.dumps(evidence)
    assert '"token":' not in serialized
    assert '"private_key":' not in serialized


def test_build_evidence_rejects_nonimmutable_digest(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="image digest"):
        build(tmp_path, digest="latest")


def test_build_evidence_rejects_failed_smoke_test(tmp_path: Path) -> None:
    files = evidence_files(tmp_path)
    files["smoke_log"].write_text("scheduler failed\n")
    with pytest.raises(EvidenceError, match="smoke test"):
        build(tmp_path, **files)


def test_build_evidence_rejects_blocked_vulnerability(tmp_path: Path) -> None:
    files = evidence_files(tmp_path)
    files["scan_report"].write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-2099-1", "Severity": "CRITICAL"}
                        ]
                    }
                ]
            }
        )
    )
    with pytest.raises(EvidenceError, match="CVE-2099-1"):
        build(tmp_path, **files)
