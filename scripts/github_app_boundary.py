"""Audit GitHub App installation scope and permissions without exposing its token."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONTRACT = Path("config/github-apps.toml")
DEFAULT_API_URL = "https://api.github.com"
PRINCIPALS = frozenset({"ci_reader", "release_bot"})
PERMISSION_LEVELS = frozenset({"read", "write"})
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class BoundaryError(RuntimeError):
    """Raised when an App exceeds or misses its declared boundary."""


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    with path.open("rb") as handle:
        contract = tomllib.load(handle)
    validate_contract(contract)
    return contract


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") != 1:
        raise BoundaryError("github-app contract schema_version must be 1")
    if contract.get("environment") != "github-app-audit":
        raise BoundaryError("GitHub App keys must be protected by github-app-audit")
    if contract.get("owner_source") != "github.repository_owner":
        raise BoundaryError("App installation owner must follow github.repository_owner")
    if contract.get("owner_type") != "Organization":
        raise BoundaryError("GitHub Apps must be owned and installed by an organization")
    retention = contract.get("evidence_retention_days")
    if not isinstance(retention, int) or not 1 <= retention <= 90:
        raise BoundaryError("evidence_retention_days must be between 1 and 90")

    apps = contract.get("apps")
    if not isinstance(apps, dict) or set(apps) != PRINCIPALS:
        raise BoundaryError("contract must declare exactly ci_reader and release_bot")

    secret_names: set[str] = set()
    variable_names: set[str] = set()
    for principal, app in apps.items():
        if not isinstance(app, dict):
            raise BoundaryError(f"{principal} must be a table")
        if not isinstance(app.get("name"), str) or not app["name"]:
            raise BoundaryError(f"{principal}.name must be non-empty")
        if app.get("repository_selection") != "selected":
            raise BoundaryError(f"{principal} must use selected repositories")

        repositories = app.get("repositories")
        if (
            not isinstance(repositories, list)
            or not repositories
            or any(
                not isinstance(repo, str) or not REPOSITORY_NAME.fullmatch(repo)
                for repo in repositories
            )
            or len(repositories) != len(set(repositories))
        ):
            raise BoundaryError(f"{principal}.repositories must be unique repository names")

        permissions = app.get("permissions")
        if not isinstance(permissions, dict) or permissions.get("metadata") != "read":
            raise BoundaryError(f"{principal} must declare metadata:read")
        if any(level not in PERMISSION_LEVELS for level in permissions.values()):
            raise BoundaryError(f"{principal} contains an invalid permission level")

        variable_name = app.get("app_id_variable")
        secret_name = app.get("private_key_secret")
        if not isinstance(variable_name, str) or not variable_name:
            raise BoundaryError(f"{principal}.app_id_variable must be non-empty")
        if not isinstance(secret_name, str) or not secret_name:
            raise BoundaryError(f"{principal}.private_key_secret must be non-empty")
        variable_names.add(variable_name)
        secret_names.add(secret_name)

    if len(variable_names) != len(apps) or len(secret_names) != len(apps):
        raise BoundaryError("each App must use distinct ID and private-key settings")


class GitHubAPI:
    def __init__(self, token: str, api_url: str = DEFAULT_API_URL) -> None:
        if not token:
            raise BoundaryError("GITHUB_APP_TOKEN is required")
        self.token = token
        self.api_url = api_url.rstrip("/")
        parsed_api_url = urllib.parse.urlparse(self.api_url)
        if parsed_api_url.scheme != "https" or not parsed_api_url.netloc:
            raise BoundaryError("GitHub API URL must be an absolute HTTPS URL")
        self.api_origin = (parsed_api_url.scheme, parsed_api_url.netloc)

    def get(self, path_or_url: str) -> tuple[Any, dict[str, str]]:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{self.api_url}/{path_or_url.lstrip('/')}"
        )
        parsed_url = urllib.parse.urlparse(url)
        if (parsed_url.scheme, parsed_url.netloc) != self.api_origin:
            raise BoundaryError("refusing to send a GitHub credential to another origin")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "jarvis-github-app-boundary-audit",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.load(response), dict(response.headers.items())
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise BoundaryError(f"GitHub API {error.code} for {url}: {body[:300]}") from error
        except urllib.error.URLError as error:
            raise BoundaryError(f"GitHub API request failed for {url}: {error.reason}") from error

    def installation_repositories(self) -> list[dict[str, Any]]:
        url = f"{self.api_url}/installation/repositories?per_page=100"
        repositories: list[dict[str, Any]] = []
        while url:
            payload, headers = self.get(url)
            if not isinstance(payload, dict) or not isinstance(payload.get("repositories"), list):
                raise BoundaryError("unexpected /installation/repositories response")
            repositories.extend(payload["repositories"])
            url = _next_link(headers.get("Link", ""))
        return repositories


def _next_link(header: str) -> str:
    for item in header.split(","):
        match = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', item)
        if match and match.group(2) == "next":
            parsed = urllib.parse.urlparse(match.group(1))
            if parsed.scheme != "https":
                raise BoundaryError("GitHub pagination URL must use HTTPS")
            return match.group(1)
    return ""


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def create_app_jwt(app_id: str, private_key: str) -> str:
    if not app_id.isdigit():
        raise BoundaryError("GITHUB_APP_ID must be numeric")
    if "BEGIN RSA PRIVATE KEY" not in private_key and "BEGIN PRIVATE KEY" not in private_key:
        raise BoundaryError("GITHUB_APP_PRIVATE_KEY is not a PEM private key")

    now = int(time.time())
    header = _base64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _base64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    signing_input = f"{header}.{payload}".encode()

    key_path = ""
    try:
        descriptor, key_path = tempfile.mkstemp(prefix="jarvis-app-key-")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(private_key)
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BoundaryError("failed to sign the GitHub App JWT") from error
    finally:
        if key_path:
            Path(key_path).unlink(missing_ok=True)
    return f"{header}.{payload}.{_base64url(result.stdout)}"


def validate_observation(
    *,
    principal: str,
    owner: str,
    app: dict[str, Any],
    installation: dict[str, Any],
    repositories: list[dict[str, Any]],
    owner_type: str = "Organization",
) -> dict[str, Any]:
    if owner_type != "Organization":
        raise BoundaryError(f"{owner!r} is a {owner_type!r}, expected an Organization")
    account = installation.get("account")
    observed_owner = account.get("login") if isinstance(account, dict) else None
    if not owner or observed_owner != owner:
        raise BoundaryError(f"{principal} is installed on {observed_owner!r}, expected {owner!r}")
    if installation.get("repository_selection") != app["repository_selection"]:
        raise BoundaryError(f"{principal} is not restricted to selected repositories")
    if installation.get("app_slug") != app["name"]:
        raise BoundaryError(
            f"{principal} App slug is {installation.get('app_slug')!r}, expected {app['name']!r}"
        )

    observed_permissions = installation.get("permissions")
    if not isinstance(observed_permissions, dict):
        raise BoundaryError(f"{principal} installation did not report permissions")
    expected_permissions = dict(app["permissions"])
    if observed_permissions != expected_permissions:
        raise BoundaryError(
            f"{principal} permissions are {observed_permissions!r}, "
            f"expected {expected_permissions!r}"
        )

    observed_repositories = {
        repository.get("full_name")
        for repository in repositories
        if isinstance(repository, dict) and isinstance(repository.get("full_name"), str)
    }
    expected_repositories = {f"{owner}/{name}" for name in app["repositories"]}
    if observed_repositories != expected_repositories:
        extra = sorted(observed_repositories - expected_repositories)
        missing = sorted(expected_repositories - observed_repositories)
        raise BoundaryError(
            f"{principal} repository boundary mismatch; extra={extra!r}, missing={missing!r}"
        )

    return {
        "schema_version": 1,
        "observed_at": datetime.now(UTC).isoformat(),
        "principal": principal,
        "app_name": app["name"],
        "app_slug": installation.get("app_slug"),
        "installation_id": installation.get("id"),
        "owner": owner,
        "repository_selection": installation["repository_selection"],
        "permissions": dict(sorted(observed_permissions.items())),
        "repositories": sorted(observed_repositories),
        "result": "pass",
    }


def audit(
    *,
    principal: str,
    owner: str,
    token: str,
    app_id: str,
    private_key: str,
    installation_id: str,
    contract_path: Path = DEFAULT_CONTRACT,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    if principal not in PRINCIPALS:
        raise BoundaryError(f"unknown principal {principal!r}")
    if not installation_id.isdigit():
        raise BoundaryError("GITHUB_APP_INSTALLATION_ID must be numeric")
    token_api = GitHubAPI(token, api_url)
    app_api = GitHubAPI(create_app_jwt(app_id, private_key), api_url)
    owner_record, _ = token_api.get(f"/users/{urllib.parse.quote(owner, safe='')}")
    if not isinstance(owner_record, dict):
        raise BoundaryError("unexpected owner response")
    installation, _ = app_api.get(f"/app/installations/{installation_id}")
    if not isinstance(installation, dict):
        raise BoundaryError("unexpected App installation response")
    repositories = token_api.installation_repositories()
    return validate_observation(
        principal=principal,
        owner=owner,
        app=contract["apps"][principal],
        installation=installation,
        repositories=repositories,
        owner_type=str(owner_record.get("type", "")),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--principal", required=True, choices=sorted(PRINCIPALS))
    parser.add_argument("--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL))
    parser.add_argument("--evidence", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        evidence = audit(
            principal=args.principal,
            owner=args.owner,
            token=os.environ.get("GITHUB_APP_TOKEN", ""),
            app_id=os.environ.get("GITHUB_APP_ID", ""),
            private_key=os.environ.get("GITHUB_APP_PRIVATE_KEY", ""),
            installation_id=os.environ.get("GITHUB_APP_INSTALLATION_ID", ""),
            contract_path=args.contract,
            api_url=args.api_url,
        )
    except BoundaryError as error:
        print(f"GitHub App boundary audit failed: {error}", file=sys.stderr)
        return 1

    output = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(output)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
