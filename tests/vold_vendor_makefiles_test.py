#!/usr/bin/env python3
"""Exercise immutable real Git vendor candidates and their provenance gates."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ci"))
import prepare_vold_vendor_makefiles as prepare  # noqa: E402


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL,
    ).strip()


def commit_files(root):
    git(root, "init", "--quiet")
    git(root, "config", "user.name", "Vendor Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "immutable vendor fixture")
    return git(root, "rev-parse", "HEAD")


class SonyVendorCandidateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.receipt = self.root / "vendor-report.json"
        for relative in prepare.INPUTS:
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # The actual pinned upstream generator is also exercised separately
            # against the downloaded mirrors, with its evidence checked in.
            target.write_text("pass\n" if relative.endswith(".py") else "# fixture list\n")
        (self.source / "tools/extract-utils/extract_utils/fixture_import.py").write_text("pass\n")
        source_projects = []
        for relative in prepare.SOURCE_PATHS:
            target = self.source / relative
            source_projects.append({
                "path": relative, "repository": "source-fixture",
                "revision": commit_files(target),
            })
        projects = []
        for relative in prepare.VENDOR_PATHS:
            target = self.source / relative
            target.mkdir(parents=True)
            device = target.name
            for filename in ("Android.bp", "Android.mk", "BoardConfigVendor.mk", device + "-vendor.mk"):
                (target / filename).write_text("# pinned declaration\n")
            library = target / "proprietary/vendor/lib64/libcandidate.so"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"\x7fELF immutable candidate fixture")
            revision = commit_files(target)
            repository = "TheMuppets/proprietary_vendor_sony_" + device
            url = "https://github.com/" + repository + ".git"
            git(target, "remote", "add", "origin", url)
            records, digest = prepare.inventory(target)
            projects.append({
                "path": relative, "repository": repository, "url": url,
                "revision": revision, "tree": git(target, "rev-parse", "HEAD^{tree}"),
                "file_count": len(records), "content_bytes": sum(item["size"] for item in records),
                "inventory_sha256": digest,
                "declaration_sha256": {item["path"]: item["sha256"] for item in records
                                       if "/" not in item["path"]},
            })
        self.contract = {
            "schema_version": 1, "kind": "lineageos-pdx235-public-vendor-candidate",
            "base_build_id": "fixture", "manifest_sha256": "1" * 64,
            "source_projects": source_projects,
            "source_input_sha256": {
                path: hashlib.sha256((self.source / path).read_bytes()).hexdigest()
                for path in prepare.INPUTS
            },
            "vendor_projects": projects,
        }
        self.lock = self.root / "lock.json"
        self.lock.write_text(json.dumps(self.contract))
        loader = patch.object(prepare, "load_vendor_contract", return_value=self.contract)
        lock_path = patch.object(prepare, "LOCK", self.lock)
        loader.start()
        lock_path.start()
        self.addCleanup(loader.stop)
        self.addCleanup(lock_path.stop)

    def test_real_git_candidates_and_regeneration_do_not_claim_publisher_identity(self):
        with patch.object(prepare, "_git", wraps=prepare._git) as calls:
            first = prepare.prepare(self.source, self.receipt)
            second = prepare.prepare(self.source, self.receipt)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ready-for-unmodified-build-probe")
        self.assertEqual(len(first["projects"]), 2)
        self.assertFalse(first["publisher_authenticated"])
        self.assertFalse(first["original_ota_vendor_identity_proven"])
        self.assertFalse(first["byte_identical_vold_qualified"])
        self.assertTrue(first["regeneration"]["declarations_byte_identical"])
        self.assertEqual(json.loads(self.receipt.read_text()), first)
        self.assertFalse(any("fetch" in call.args for call in calls.call_args_list),
                         "reusing exact vendor trees must not download them again")
        self.assertEqual(list(self.source.rglob("__pycache__")), [])

    def test_receipt_cannot_authorize_changed_vendor_bytes(self):
        prepare.prepare(self.source, self.receipt)
        forged = json.loads(self.receipt.read_text())
        forged["publisher_authenticated"] = True
        self.receipt.write_text(json.dumps(forged))
        library = self.source / prepare.VENDOR_PATHS[0] / "proprietary/vendor/lib64/libcandidate.so"
        library.write_bytes(b"\x7fELF changed bytes")
        with self.assertRaisesRegex(prepare.VendorInputError, "unreviewed local content"):
            prepare.verify_vendor_checkout(self.source)

    def test_wrong_head_and_wrong_mirror_are_rejected(self):
        target = self.source / prepare.VENDOR_PATHS[0]
        git(target, "remote", "set-url", "origin", "https://example.invalid/vendor.git")
        with self.assertRaisesRegex(prepare.VendorInputError, "origin differs"):
            prepare.verify_vendor_checkout(self.source)
        git(target, "remote", "set-url", "origin", self.contract["vendor_projects"][0]["url"])
        git(target, "commit", "--allow-empty", "--quiet", "-m", "moved head")
        with self.assertRaisesRegex(prepare.VendorInputError, "revision changed"):
            prepare.verify_vendor_checkout(self.source)

    def test_ignored_and_extra_files_cannot_enter_candidate_inventory(self):
        target = self.source / prepare.VENDOR_PATHS[0]
        (target / ".git/info/exclude").write_text("extra.bp\n")
        (target / "extra.bp").write_text("unapproved module")
        with self.assertRaisesRegex(prepare.VendorInputError, "unreviewed local content"):
            prepare.verify_vendor_checkout(self.source)

    def test_unreviewed_vendor_project_is_rejected(self):
        (self.source / "vendor/sony/other-device").mkdir()
        with self.assertRaisesRegex(prepare.VendorInputError, "unreviewed project paths"):
            prepare.verify_vendor_checkout(self.source)

    def test_symlinked_project_and_file_are_rejected(self):
        target = self.source / prepare.VENDOR_PATHS[0]
        moved = target.with_name("moved")
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(prepare.VendorInputError, "linked source or vendor path"):
            prepare._fetch_project(self.source, self.contract["vendor_projects"][0])
        target.unlink()
        moved.rename(target)
        library = target / "proprietary/vendor/lib64/libcandidate.so"
        library.unlink()
        library.symlink_to(target / "Android.bp")
        with self.assertRaisesRegex(prepare.VendorInputError, "regular file"):
            prepare.inventory(target)

    def test_lfs_pointer_is_not_accepted_as_real_binary(self):
        target = self.source / prepare.VENDOR_PATHS[0]
        library = target / "proprietary/vendor/lib64/libcandidate.so"
        library.write_bytes(prepare.LFS_POINTER + b"\noid sha256:" + b"0" * 64 + b"\nsize 123\n")
        with self.assertRaisesRegex(prepare.VendorInputError, "unresolved Git LFS"):
            prepare.inventory(target)

    def test_complete_inventory_digest_checks_more_than_git_head(self):
        bad = copy.deepcopy(self.contract["vendor_projects"][0])
        bad["inventory_sha256"] = "0" * 64
        with self.assertRaisesRegex(prepare.VendorInputError, "inventory differs"):
            prepare.verify_project(self.source / bad["path"], bad)

    def test_changed_pinned_generator_fails_before_any_acquisition(self):
        (self.source / prepare.INPUTS[0]).write_text("raise SystemExit('changed')\n")
        with (
            patch.object(prepare, "_fetch_project") as fetch,
            self.assertRaisesRegex(prepare.VendorInputError, "unreviewed local content"),
        ):
            prepare.prepare(self.source, self.receipt)
        fetch.assert_not_called()
        self.assertFalse(self.receipt.exists())

    def test_index_flags_cannot_hide_changed_imported_source(self):
        project = self.source / "tools/extract-utils"
        relative = "extract_utils/fixture_import.py"
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                git(project, "update-index", "--" + flag, relative)
                (project / relative).write_text("raise RuntimeError('changed imported source')\n")
                self.assertEqual(git(project, "status", "--porcelain"), "")
                with (
                    patch.object(prepare, "_fetch_project") as fetch,
                    self.assertRaisesRegex(prepare.VendorInputError, "assume-unchanged or skip-worktree"),
                ):
                    prepare.prepare(self.source, self.receipt)
                fetch.assert_not_called()
                (project / relative).write_text("pass\n")
                git(project, "update-index", "--no-" + flag, relative)
        self.assertFalse(self.receipt.exists())

    def test_generator_input_digest_is_independent_of_git_revision(self):
        self.contract["source_input_sha256"][prepare.INPUTS[0]] = "0" * 64
        with self.assertRaisesRegex(prepare.VendorInputError, "generator input changed"):
            prepare.verify_vendor_checkout(self.source)

    def test_report_cannot_modify_verified_source_or_follow_symlink(self):
        with self.assertRaisesRegex(prepare.VendorInputError, "outside the verified source"):
            prepare.prepare(self.source, self.source / "report.json")
        self.receipt.symlink_to(self.root / "outside.json")
        with self.assertRaisesRegex(prepare.VendorInputError, "must not be a symlink"):
            prepare.prepare(self.source, self.receipt)
        self.assertFalse((self.root / "outside.json").exists())

    def test_failed_acquisition_never_installs_or_resets_a_vendor_tree(self):
        missing = self.source / prepare.VENDOR_PATHS[0]
        backup = self.root / "existing-fixture"
        missing.rename(backup)
        real_git = prepare._git

        def fail_fetch(directory, *args):
            if "fetch" in args:
                raise prepare.VendorInputError("network unavailable")
            return real_git(directory, *args)

        with (
            patch.object(prepare, "_git", side_effect=fail_fetch),
            self.assertRaisesRegex(prepare.VendorInputError, "network unavailable"),
        ):
            prepare._fetch_project(self.source, self.contract["vendor_projects"][0])
        self.assertFalse(missing.exists())
        self.assertEqual(list(self.root.glob("modos-vendor-fetch-*")), [])
        self.assertEqual(git(backup, "rev-parse", "HEAD"), self.contract["vendor_projects"][0]["revision"])


class ActualVendorContractTest(unittest.TestCase):
    def test_checked_in_lock_is_tied_to_original_manifest_and_public_source(self):
        contract = prepare.load_vendor_contract()
        self.assertEqual([p["path"] for p in contract["vendor_projects"]], list(prepare.VENDOR_PATHS))
        self.assertEqual(sum(p["file_count"] for p in contract["vendor_projects"]), 1114)
        self.assertFalse(contract["provenance"]["publisher_authenticated"])


if __name__ == "__main__":
    unittest.main()
