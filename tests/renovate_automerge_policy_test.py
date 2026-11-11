#!/usr/bin/env python3
"""Offline regressions for the self-hosted Renovate auto-merge boundary."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("policy", ROOT / "src/ci/renovate_automerge_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

REPOSITORY = {"default_branch": "main", "allow_auto_merge": True, "allow_squash_merge": True}
PROTECTION = {
    "required_status_checks": {"strict": True, "checks": [{"context": "test", "app_id": 15368}]},
    "required_pull_request_reviews": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews": True,
        "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
    },
    "enforce_admins": {"enabled": True},
    "allow_force_pushes": {"enabled": False},
}


class AutoMergePolicyTests(unittest.TestCase):
    def test_complete_policy_removes_only_force_override(self):
        self.assertEqual(policy.policy(REPOSITORY, PROTECTION)[0], {})

    def test_repository_prerequisites(self):
        for key, value in (("default_branch", "develop"), ("allow_auto_merge", False),
                           ("allow_squash_merge", False), ("allow_auto_merge", "true")):
            with self.subTest(key=key, value=value):
                repo = dict(REPOSITORY, **{key: value})
                self.assertEqual(policy.policy(repo, PROTECTION)[0], {"automerge": False})

    def test_missing_protection_fields_fail_closed(self):
        for key in PROTECTION:
            with self.subTest(key=key):
                protection = copy.deepcopy(PROTECTION)
                del protection[key]
                self.assertEqual(policy.policy(REPOSITORY, protection)[0], {"automerge": False})

    def test_exact_actions_check_and_up_to_date_branch_required(self):
        for checks in ({"strict": False, "checks": PROTECTION["required_status_checks"]["checks"]},
                       {"strict": True, "contexts": ["test"]},
                       {"strict": True, "checks": [{"context": "test", "app_id": None}]},
                       {"strict": True, "checks": [{"context": "test", "app_id": 1}]},
                       {"strict": True, "checks": [{"context": "unrelated", "app_id": 15368}]}):
            with self.subTest(checks=checks):
                protection = dict(PROTECTION, required_status_checks=checks)
                self.assertEqual(policy.policy(REPOSITORY, protection)[0], {"automerge": False})

    def test_fresh_review_required(self):
        for key, value in (("required_approving_review_count", 0),
                           ("required_approving_review_count", True),
                           ("required_approving_review_count", "1"),
                           ("dismiss_stale_reviews", False)):
            with self.subTest(key=key, value=value):
                protection = copy.deepcopy(PROTECTION)
                protection["required_pull_request_reviews"][key] = value
                self.assertEqual(policy.policy(REPOSITORY, protection)[0], {"automerge": False})

    def test_review_bypass_rejected(self):
        for kind in ("users", "teams", "apps"):
            with self.subTest(kind=kind):
                protection = copy.deepcopy(PROTECTION)
                protection["required_pull_request_reviews"]["bypass_pull_request_allowances"][kind] = [{"id": 1}]
                self.assertEqual(policy.policy(REPOSITORY, protection)[0], {"automerge": False})

    def test_admin_and_force_push_bypasses_rejected(self):
        for key, enabled in (("enforce_admins", False), ("allow_force_pushes", True)):
            with self.subTest(key=key):
                protection = dict(PROTECTION, **{key: {"enabled": enabled}})
                self.assertEqual(policy.policy(REPOSITORY, protection)[0], {"automerge": False})

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "0cwa/ModOS", "GH_TOKEN": "test-only"})
    def test_existing_token_read_only_endpoints(self):
        with patch.object(policy, "github_json", side_effect=[REPOSITORY, PROTECTION]) as fetch:
            self.assertEqual(policy.evaluate()[0], {})
        self.assertEqual(fetch.call_args_list[0].args, ("repos/0cwa/ModOS", "test-only"))
        self.assertEqual(fetch.call_args_list[1].args, ("repos/0cwa/ModOS/branches/main/protection", "test-only"))

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "0cwa/ModOS", "GH_TOKEN": "test-only"})
    def test_disabled_repo_does_not_require_admin_read_access(self):
        with patch.object(policy, "github_json", return_value=dict(REPOSITORY, allow_auto_merge=False)) as fetch:
            self.assertEqual(policy.evaluate()[0], {"automerge": False})
            fetch.assert_called_once()

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "0cwa/ModOS", "GH_TOKEN": "test-only"})
    def test_http_and_invalid_response_fail_closed_without_secrets(self):
        failures = [HTTPError("https://api.github.com", 403, "private", {}, None),
                    TimeoutError("private"), ValueError("private"),
                    TypeError("private"), AttributeError("private")]
        for failure in failures:
            with self.subTest(failure=type(failure)), patch.object(policy, "github_json", side_effect=failure):
                force, reason = policy.evaluate()
                self.assertEqual(force, {"automerge": False})
                self.assertNotIn("private", reason)
                self.assertNotIn("test-only", reason)

    def test_missing_credentials_and_invalid_repository_do_not_fetch(self):
        for repo, token in (("0cwa/ModOS", ""), ("../outside/path", "test"), ("", "test")):
            with self.subTest(repo=repo), patch.dict(os.environ, {"GITHUB_REPOSITORY": repo, "GH_TOKEN": token}), patch.object(policy, "github_json") as fetch:
                self.assertEqual(policy.evaluate()[0], {"automerge": False})
                fetch.assert_not_called()

    def test_github_output_is_single_line_json(self):
        for force in ({}, {"automerge": False}):
            with self.subTest(force=force), patch.object(policy, "evaluate", return_value=(force, "test")), patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(policy.main(), 0)
                key, value = out.getvalue().strip().split("=", 1)
                self.assertEqual(key, "force")
                self.assertEqual(json.loads(value), force)
                self.assertEqual(out.getvalue().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
