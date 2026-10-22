#!/usr/bin/env python3
"""Ensure Sony makefile regeneration remains a no-blob, unqualified probe."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ci"))
import prepare_vold_vendor_makefiles as prepare  # noqa: E402


class SonyVendorMakefileTest(unittest.TestCase):
    def source(self, parent: Path) -> Path:
        root = parent / "source"
        root.mkdir()
        for path in prepare.INPUTS:
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(f"PINNED TEST CONTENT {path}\n")
        return root

    def test_regenerate_makefiles_only_and_compare_cached_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            report_path = root / "report.json"
            calls = []

            def fake_generate(argv, cwd, env, check):
                calls.append(argv)
                self.assertTrue(check)
                self.assertEqual(cwd, source)
                self.assertEqual(argv[0], sys.executable)
                self.assertEqual(argv[1:], [
                    "device/sony/pdx235/extract-files.py", "--regenerate_makefiles",
                ])
                self.assertEqual(env["PYTHONPATH"], str(source / "tools/extract-utils"))
                for path in prepare.REQUIRED:
                    output = source / path
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(f"generated from pinned public list: {path}\n")

            with (
                patch.object(prepare, "verify_checkout"),
                patch.object(prepare.subprocess, "run", side_effect=fake_generate),
            ):
                saved = prepare.prepare(source, report_path)
                self.assertEqual(saved["kind"], "synthetic-probe-vendor-makefiles-not-publisher-provenance")
                self.assertTrue(saved["does_not_extract_blobs"])
                self.assertTrue(saved["does_not_qualify_original_vold"])
                self.assertEqual(len(saved["generated_makefile_sha256"]), len(prepare.REQUIRED))
                self.assertEqual(len(calls), 1)
                self.assertEqual(json.loads(report_path.read_text()), saved)
                self.assertEqual(prepare.prepare(source, report_path), saved)
                self.assertEqual(len(calls), 1, "reused makefiles must not trigger extraction again")
                first = source / prepare.REQUIRED[0]
                first.write_text("tampered generated files")
                with self.assertRaisesRegex(prepare.VendorPreparationError, "differ"):
                    prepare.prepare(source, report_path)

    def test_proprietary_elf_or_symlink_are_rejected_even_if_generator_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            for path in prepare.REQUIRED:
                dest = source / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("makefile")
            (source / "vendor/sony/pdx235/libsecret.so").write_bytes(b"binary")
            with self.assertRaisesRegex(prepare.VendorPreparationError, "unexpected binary blob"):
                prepare.report(source, {})
            (source / "vendor/sony/pdx235/libsecret.so").unlink()
            (source / "vendor/sony/pdx235/untrusted.mk").symlink_to(prepare.REQUIRED[0])
            with self.assertRaisesRegex(prepare.VendorPreparationError, "unsafe link"):
                prepare.report(source, {})

    def test_workflow_is_still_unmodified_and_unqualified(self):
        script = (ROOT / "src/ci/probe_original_vold.sh").read_text()
        code = (ROOT / "src/ci/prepare_vold_vendor_makefiles.py").read_text()
        workflow = (ROOT / ".github/workflows/vold-baseline.yml").read_text()
        self.assertIn("src/ci/prepare_vold_vendor_makefiles.py", script)
        self.assertIn("sony-generated-makefiles-report.json", workflow)
        self.assertIn("--regenerate_makefiles", code)
        self.assertIn("does_not_qualify_original_vold", code)
        for x in (script, code, workflow):
            self.assertNotIn("git apply", x)
            self.assertNotIn("ota patch", x)
            self.assertNotIn("SOONG_ALLOW_MISSING_DEPENDENCIES", x)


if __name__ == "__main__":
    unittest.main()
