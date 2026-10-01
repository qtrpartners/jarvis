"""Tests for the GitHub App least-privilege boundary auditor."""

from copy import deepcopy

import pytest

from scripts.github_app_boundary import (
    BoundaryError,
    load_contract,
    validate_contract,
    validate_observation,
)


def installation(
    *,
    permissions: dict[str, str],
    repositories: list[str],
    app_slug: str,
    owner: str = "example-org",
) -> tuple[dict[str, object], list[dict[str, str]]]:
    return (
        {
            "id": 123,
            "app_slug": app_slug,
            "account": {"login": owner},
            "repository_selection": "selected",
            "permissions": permissions,
        },
        [{"full_name": f"{owner}/{repository}"} for repository in repositories],
    )


def test_default_contract_is_minimal_and_separates_credentials() -> None:
    contract = load_contract()
    reader = contract["apps"]["ci_reader"]
    release = contract["apps"]["release_bot"]

    assert reader["permissions"] == {"contents": "read", "metadata": "read"}
    assert release["permissions"] == {
        "contents": "write",
        "metadata": "read",
        "pull_requests": "write",
    }
    assert reader["repositories"] == ["jarvis"]
    assert release["repositories"] == ["jarvis-live"]
    assert reader["private_key_secret"] != release["private_key_secret"]


def test_contract_rejects_all_repository_installation() -> None:
    contract = deepcopy(load_contract())
    contract["apps"]["ci_reader"]["repository_selection"] = "all"

    with pytest.raises(BoundaryError, match="selected repositories"):
        validate_contract(contract)


def test_observation_accepts_exact_reader_boundary() -> None:
    app = load_contract()["apps"]["ci_reader"]
    observed_installation, repositories = installation(
        permissions=app["permissions"], repositories=app["repositories"], app_slug=app["name"]
    )

    evidence = validate_observation(
        principal="ci_reader",
        owner="example-org",
        app=app,
        installation=observed_installation,
        repositories=repositories,
    )

    assert evidence["result"] == "pass"
    assert evidence["repositories"] == ["example-org/jarvis"]
    assert "token" not in evidence


def test_observation_rejects_extra_repository() -> None:
    app = load_contract()["apps"]["ci_reader"]
    observed_installation, repositories = installation(
        permissions=app["permissions"],
        repositories=[*app["repositories"], "private-canary"],
        app_slug=app["name"],
    )

    with pytest.raises(BoundaryError, match="extra=.*private-canary"):
        validate_observation(
            principal="ci_reader",
            owner="example-org",
            app=app,
            installation=observed_installation,
            repositories=repositories,
        )


def test_observation_rejects_permission_expansion() -> None:
    app = load_contract()["apps"]["ci_reader"]
    permissions = {**app["permissions"], "issues": "write"}
    observed_installation, repositories = installation(
        permissions=permissions, repositories=app["repositories"], app_slug=app["name"]
    )

    with pytest.raises(BoundaryError, match="permissions"):
        validate_observation(
            principal="ci_reader",
            owner="example-org",
            app=app,
            installation=observed_installation,
            repositories=repositories,
        )


def test_observation_rejects_wrong_installation_owner() -> None:
    app = load_contract()["apps"]["release_bot"]
    observed_installation, repositories = installation(
        permissions=app["permissions"],
        repositories=app["repositories"],
        app_slug=app["name"],
        owner="personal-user",
    )

    with pytest.raises(BoundaryError, match="expected 'example-org'"):
        validate_observation(
            principal="release_bot",
            owner="example-org",
            app=app,
            installation=observed_installation,
            repositories=repositories,
        )


def test_observation_rejects_personal_owner() -> None:
    app = load_contract()["apps"]["ci_reader"]
    observed_installation, repositories = installation(
        permissions=app["permissions"], repositories=app["repositories"], app_slug=app["name"]
    )

    with pytest.raises(BoundaryError, match="expected an Organization"):
        validate_observation(
            principal="ci_reader",
            owner="example-org",
            owner_type="User",
            app=app,
            installation=observed_installation,
            repositories=repositories,
        )
