#!/usr/bin/env python3
"""Regression tests for pin-bound, iterative unmodified vold dependency scans."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ci"))

import build_original_vold as original  # noqa: E402
import vold_source_closure as closure  # noqa: E402


class VoldSourceClosureTest(unittest.TestCase):
    def setUp(self):
        self.seed, self.manifest, self.baseline = closure.verified_contract()
        self.initial = closure.initial_projects(self.seed)

    def test_pinned_70_project_bootstrap_is_strictly_smaller_than_full_manifest(self):
        self.assertEqual(len(self.manifest), 1243)
        # The official manifest omits path= for dozens of AOSP projects.
        self.assertIn("device/common", self.manifest)
        self.assertIn("toolchain/pgo-profiles", self.manifest)
        self.assertEqual(len(self.initial), 70)
        self.assertEqual(len(self.initial), len(set(self.initial)))
        self.assertTrue({"system/vold", "build/soong", "frameworks/base", "device/sony/pdx235"}.issubset(self.initial))
        self.assertTrue(set(self.initial).issubset(self.manifest))
        self.assertEqual(self.seed["manifest_sha256"], self.baseline["manifest"]["sha256"])
        self.assertIn("vendor/sony/pdx235/pdx235-vendor.mk", self.seed["known_unpinned_vendor_inputs"])
        self.assertNotIn("vendor/sony/pdx235", self.manifest)

    def test_invalid_or_unpinned_expansions_fail_closed(self):
        correct = closure.selection_record(self.initial, self.seed)
        self.assertEqual(closure.validate_selection(correct, self.manifest, self.seed), self.initial)
        for mutate in [
            lambda x: x.update(manifest_sha256="0" * 64),
            lambda x: x.update(selected_projects=["system/vold"]),
            lambda x: x.update(selected_projects=x["selected_projects"] + ["vendor/sony/pdx235"]),
            lambda x: x.update(selected_projects=x["selected_projects"] * 2),
        ]:
            selection = dict(correct)
            mutate(selection)
            with self.assertRaises(closure.ClosureError):
                closure.validate_selection(selection, self.manifest, self.seed)

    def test_diagnostics_identify_pinned_source_vs_unpinned_vendor(self):
        log = (
            'error: module "vold" depends on undefined module "fuseMedia.bpf"\n'
            'error: source file system/update_engine/Android.bp: No such file or directory\n'
            'error: vendor/sony/pdx235/pdx235-vendor.mk missing\n'
            'error: module "vold" depends on undefined module "unsupported_vendor_blob"\n'
        )
        report = closure.missing_dependencies(log, self.initial, self.manifest, self.seed)
        self.assertIn("system/update_engine", report["proposed_project_additions"])
        self.assertIn("vendor/sony/pdx235/pdx235-vendor.mk", report["unpinned_missing_paths"])
        self.assertIn("unsupported_vendor_blob", report["modules_without_verified_owner"])
        self.assertNotIn("vendor/sony/pdx235", report["proposed_project_additions"])
        self.assertEqual(closure.owner("system/fs/fs_mgr/fs_mgr/Android.bp", self.manifest), "system/fs/fs_mgr")
        self.assertIsNone(closure.owner("vendor/sony/pdx235/pdx235-vendor.mk", self.manifest))

    def test_static_soong_modules_trace_defaults_and_transitive_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bp = root / "system/vold/Android.bp"
            bp.parent.mkdir(parents=True)
            bp.write_text(
                'cc_defaults {\n  name: "vold_default",\n  shared_libs: ["libbase"],\n}\n'
                'cc_binary {\n name: "vold",\n defaults: ["vold_default"],\n'
                'static_libs: ["libvold"],\n srcs: [":vold_aidl"],\n'
                'arch: { arm64: { shared_libs: ["libcrypto"], }, },\n}\n'
                'cc_library_static {\n name: "libvold",\n static_libs: ["libdm"],\n}\n'
                'filegroup {\n name: "vold_aidl",\n srcs: ["binder/IVold.aidl"],\n}\n'
            )
            report = closure.blueprint_module_graph(root, self.initial)
            self.assertEqual(report["scanned_android_bp_files"], 1)
            self.assertGreaterEqual(report["reachable_module_names"], 6)
            self.assertTrue({"libcrypto", "libbase", "libdm"}.issubset(report["unresolved_module_names"]))
            self.assertNotIn("vold_aidl", report["unresolved_module_names"])

    def test_incremental_sync_reuses_checkout_and_syncs_only_pinned_projects(self):
        manifest_bytes = (ROOT / self.baseline["manifest"]["path"]).read_bytes()
        source_ref = self.baseline["projects"]["manifest"]["revision"]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            log = []

            def fake_command(argv, cwd, **kwargs):
                log.append(argv)
                repo_dir = cwd / ".repo"
                if argv[1] == "init" and "-m" not in argv:
                    (repo_dir / "manifests").mkdir(parents=True)
                    (repo_dir / "manifest.xml").write_text("prior default")
                if argv[1] == "init" and "-m" in argv:
                    (repo_dir / "manifest.xml").write_bytes(
                        (repo_dir / "manifests" / argv[-1]).read_bytes()
                    )
                if argv[1] == "sync":
                    for project in argv[4:]:
                        (cwd / project).mkdir(parents=True, exist_ok=True)

            with (
                patch.object(original.shutil, "which", return_value="/usr/bin/repo"),
                patch.object(original, "command", side_effect=fake_command),
                patch.object(original, "verify_checkout"),
            ):
                original.sync(source, self.baseline, manifest_bytes, 2, self.initial)
                self.assertEqual([a[1] for a in log], ["init", "init", "sync"])
                self.assertEqual(log[-1][-len(self.initial):], self.initial)
                self.assertIn(source_ref, log[0])
                log.clear()
                more = sorted(set(self.initial) | {"system/update_engine"})
                original.sync(source, self.baseline, manifest_bytes, 2, more, resume=True)
                self.assertEqual(len(log), 1)
                self.assertEqual(log[0][1], "sync")
                self.assertEqual(log[0][4:], ["system/update_engine"])
                log.clear()
                original.sync(source, self.baseline, manifest_bytes, 2, more, resume=True)
                self.assertEqual(log, [], "unchanged pinned checkout should not re-run repo sync")

    def test_selections_are_checked_against_unchanged_manifest(self):
        raw = (ROOT / self.baseline["manifest"]["path"]).read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / ".repo").mkdir()
            (source / ".repo/manifest.xml").write_bytes(raw)
            first = self.initial[:2]
            revisions = original.project_map(raw)

            def mocked_command(argv, cwd, **kwargs):
                result = revisions[cwd.relative_to(source).as_posix()][1] if argv[1] == "rev-parse" else ""
                return type("Result", (), {"stdout": result})()

            for project in first:
                (source / project).mkdir(parents=True)
            with (
                patch.object(original.shutil, "which", return_value="/usr/bin/repo"),
                patch.object(original, "command", side_effect=mocked_command),
                patch.object(original.subprocess, "run",
                             side_effect=AssertionError("subset should never request a full repo manifest -r")),
            ):
                original.verify_checkout(source, raw, clean=True, selected=first)
                (source / ".repo/manifest.xml").write_text("tampered")
                with self.assertRaises(original.BuildError):
                    original.verify_checkout(source, raw, clean=True, selected=first)

    def test_probe_remains_independent_of_published_vold_patch(self):
        script = (ROOT / "src/ci/probe_original_vold.sh").read_text()
        workflow = (ROOT / ".github/workflows/vold-baseline.yml").read_text()
        builder = (ROOT / "src/ci/build_original_vold.py").read_text()
        self.assertIn("sync --resume", script)
        self.assertIn("for iteration in 1 2 3", script)
        self.assertIn("vold_source_closure.py diagnose", script)
        self.assertIn("dependency_plan:", workflow)
        self.assertIn("default: plan", workflow)
        self.assertIn("Source compile probe — NOT QUALIFIED", workflow)
        self.assertIn("probe", builder)
        for source in (script, workflow, builder):
            self.assertNotIn("git apply", source)
            self.assertNotIn("force-publish", source)
            self.assertNotIn("SOONG_ALLOW_MISSING_DEPENDENCIES", source)
            self.assertNotIn("publish: true", source)


if __name__ == "__main__":
    unittest.main()
