"""Repository policy tests for GitHub Actions privilege boundaries."""

import re
import tomllib
from pathlib import Path

WORKFLOWS = Path(".github/workflows")
AUTOMATION_CONTRACT = Path("config/automation.toml")
GITHUB_APPS_CONTRACT = Path("config/github-apps.toml")
ACTION_REFERENCE = re.compile(r"^[ \t]*(?:-[ \t]+)?uses:[ \t]+([^\s#]+)", re.MULTILINE)
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")
PULL_REQUEST_TRIGGER = re.compile(r"^[ \t]*pull_request(?:_target)?:", re.MULTILINE)


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def workflow_paths() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def test_pull_request_workflows_are_read_only_and_credential_free() -> None:
    pull_request_workflows = [
        path for path in workflow_paths() if PULL_REQUEST_TRIGGER.search(path.read_text())
    ]
    assert pull_request_workflows

    forbidden = (
        "id-token: write",
        "environment:",
        "${{ secrets.",
        "google-github-actions/auth@",
        "aws-actions/configure-aws-credentials@",
        "azure/login@",
        "docker/login-action@",
        "docker login",
        "docker push",
        "gcloud auth",
        "aws ecr",
        "az acr",
        "push: true",
    )
    for path in pull_request_workflows:
        source = path.read_text()
        assert "permissions:\n  contents: read" in source, path
        assert "pull_request_target:" not in source, path
        assert not re.search(r"^[ \t]+[a-z-]+:[ \t]+write[ \t]*$", source, re.MULTILINE), path
        assert not re.search(r"\$\{\{[ \t]*secrets\.", source), path
        for marker in forbidden:
            assert marker not in source, f"{path} grants pull requests access to {marker!r}"


def test_release_requires_successful_main_ci_from_this_repository() -> None:
    source = workflow("release.yml")

    for required in (
        "workflow_run:",
        'workflows: ["ci"]',
        "types: [completed]",
        "branches: [main]",
        "workflow_run.conclusion == 'success'",
        "workflow_run.event == 'push'",
        "workflow_run.head_branch == 'main'",
        "workflow_run.head_repository.full_name == github.repository",
        "ref: ${{ github.event.workflow_run.head_sha }}",
        "persist-credentials: false",
        "environment: production",
        "id-token: write",
    ):
        assert required in source

    assert "pull_request:" not in source
    assert "workflow_dispatch:" not in source
    assert "${{ secrets." not in source


def test_centralized_validation_is_immutable_and_least_privilege() -> None:
    contract = tomllib.loads(AUTOMATION_CONTRACT.read_text())["validation"]
    revision = contract["revision"]
    reference = f"{contract['repository']}/{contract['workflow']}@{revision}"
    source = workflow("ci.yml")

    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    assert reference in source
    assert contract["allowed_callers"] == ["qtrpartners/jarvis"]
    assert contract["token_permissions"] == ["contents:read"]
    assert contract["cloud_permissions"] is False
    assert contract["accepts_secrets"] is False
    assert "permissions:\n  contents: read" in source
    assert "@main" not in source


def test_release_cache_is_not_a_pull_request_cache() -> None:
    assert "scope=release-${{ matrix.cloud }}" in workflow("release.yml")


def test_all_third_party_actions_are_immutable_pins() -> None:
    references = {path: ACTION_REFERENCE.findall(path.read_text()) for path in workflow_paths()}
    assert references
    for path, actions in references.items():
        for action in actions:
            assert action.startswith("./") or PINNED_ACTION.fullmatch(action), (
                f"{path}: action is not SHA-pinned: {action}"
            )


def test_github_app_audit_uses_separate_protected_credentials() -> None:
    contract = tomllib.loads(GITHUB_APPS_CONTRACT.read_text())
    source = workflow("github-app-boundary.yml")

    assert "workflow_dispatch:" in source
    assert "schedule:" in source
    assert source.count("environment: github-app-audit") == 2
    assert "id-token: write" not in source
    assert "persist-credentials: false" in source
    assert "permission-contents: read" in source
    assert "permission-contents: write" in source
    assert source.count("permission-metadata: read") == 2
    assert "permission-pull-requests: write" in source

    for principal in ("ci_reader", "release_bot"):
        app = contract["apps"][principal]
        assert f"vars.{app['app_id_variable']}" in source
        assert f"secrets.{app['private_key_secret']}" in source
        assert f"--principal {principal}" in source

    assert (
        "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
        in source
    )
