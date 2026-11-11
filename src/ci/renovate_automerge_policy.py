#!/usr/bin/env python3
"""Keep Renovate proposal-only unless GitHub enforces CI and fresh review.

Read-only: this never changes repository settings, grants access, or merges a PR.
The output is a Renovate force override, applied after repository/package rules.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DISABLED = {"automerge": False}


def policy(repository: dict[str, Any], protection: dict[str, Any]) -> tuple[dict, str]:
    """Require classic branch protection; unknown/ruleset-only policy is closed."""
    if repository.get("default_branch") != "main":
        return DISABLED.copy(), "the expected default branch is not main"
    if repository.get("allow_auto_merge") is not True:
        return DISABLED.copy(), "GitHub native auto-merge is disabled"
    if repository.get("allow_squash_merge") is not True:
        return DISABLED.copy(), "squash merging is disabled"
    checks = protection.get("required_status_checks") or {}
    # Bind the required test to GitHub Actions, not an arbitrary status writer.
    required = checks.get("checks") or []
    if checks.get("strict") is not True or not any(
        isinstance(check, dict)
        and check.get("context") == "test"
        and check.get("app_id") == 15368  # GitHub Actions
        for check in required
    ):
        return DISABLED.copy(), "main must require the up-to-date GitHub Actions test check"
    reviews = protection.get("required_pull_request_reviews") or {}
    count = reviews.get("required_approving_review_count")
    if type(count) is not int or count < 1 or reviews.get("dismiss_stale_reviews") is not True:
        return DISABLED.copy(), "main must require approval and dismiss stale reviews"
    bypass = reviews.get("bypass_pull_request_allowances") or {}
    if any(bypass.get(kind) for kind in ("users", "teams", "apps")):
        return DISABLED.copy(), "review bypass allowances must be empty"
    if (protection.get("enforce_admins") or {}).get("enabled") is not True:
        return DISABLED.copy(), "main protection must apply to administrators"
    if (protection.get("allow_force_pushes") or {}).get("enabled") is not False:
        return DISABLED.copy(), "main must disallow force pushes"
    return {}, "GitHub enforces current CI and fresh approval; eligible updates may auto-merge"


def github_json(path: str, token: str) -> dict[str, Any]:
    request = Request(
        f"https://api.github.com/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ModOS-Renovate-policy",
        },
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("GitHub returned a non-object policy response")
    return result


def evaluate() -> tuple[dict, str]:
    repository_name = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository_name) or not token:
        return DISABLED.copy(), "repository identity or existing Renovate credential is missing"
    try:
        repository = github_json(f"repos/{repository_name}", token)
        # These common disabled states do not need administration-read access.
        for field in ("allow_auto_merge", "allow_squash_merge"):
            if repository.get(field) is not True:
                return policy(repository, {})
        protection = github_json(f"repos/{repository_name}/branches/main/protection", token)
        return policy(repository, protection)
    except HTTPError as exc:
        return DISABLED.copy(), f"cannot verify branch protection (GitHub HTTP {exc.code})"
    except (URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
        # Do not print request/response bodies, credentials or untrusted strings.
        return DISABLED.copy(), "cannot verify repository protection"


def main() -> int:
    force, reason = evaluate()
    print(f"force={json.dumps(force, separators=(',', ':'))}")
    print(f"Renovate auto-merge policy: {reason}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
