#!/usr/bin/env python3
"""Ensure absent or unqualified Sony inputs fail before vendor generation."""
from __future__ import annotations

from contextlib import redirect_stderr
import io
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

    def test_missing_vendor_inputs_report_blocker_without_running_generator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            receipt = root / "vendor-input-report.json"
            before = sorted(p.relative_to(source).as_posix() for p in source.rglob("*"))
            with (
                patch.object(prepare, "verify_checkout") as verify,
                patch.object(prepare.subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(prepare.VendorPreparationError, "authenticated proprietary"):
                    prepare.prepare(source, receipt)
                run.assert_not_called()
                self.assertEqual(verify.call_args.kwargs, {
                    "clean": True,
                    "selected": list(prepare.VENDOR_PREREQUISITE_PROJECTS),
                })
            saved = json.loads(receipt.read_text())
            self.assertEqual(saved["status"], "blocked")
            self.assertEqual(saved["kind"], "blocked-unqualified-proprietary-vendor-inputs")
            self.assertTrue(saved["does_not_extract_blobs"])
            self.assertTrue(saved["does_not_qualify_original_vold"])
            self.assertFalse(saved["makefiles_generated"])
            self.assertFalse(saved["preexisting_vendor_tree_unqualified"])
            self.assertEqual(saved["required_input_example"], prepare.REQUIRED_ELF_EXAMPLE)
            self.assertFalse(saved["required_input_example_is_complete_list"])
            self.assertEqual(set(saved["input_sha256"]), set(prepare.INPUTS))
            self.assertEqual(
                before, sorted(p.relative_to(source).as_posix() for p in source.rglob("*")),
                "diagnosing missing blobs must not create partial .mk/.bp files or bytecode",
            )

    def test_reused_diagnostic_still_fails_and_cannot_mask_changed_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            receipt = root / "report.json"
            with patch.object(prepare, "verify_checkout"):
                for _ in range(2):
                    with self.assertRaisesRegex(prepare.VendorPreparationError, "authenticated proprietary"):
                        prepare.prepare(source, receipt)
                (source / prepare.INPUTS[0]).write_text("changed input")
                with self.assertRaisesRegex(prepare.VendorPreparationError, "not the current blocked-input"):
                    prepare.prepare(source, receipt)

    def test_preexisting_vendor_elf_and_partial_makefiles_are_not_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            elf = source / prepare.REQUIRED_ELF_EXAMPLE
            elf.parent.mkdir(parents=True)
            elf.write_bytes(b"unqualified bytes")
            (source / "vendor/sony/pdx235/Android.bp").write_text("partial upstream output")
            receipt = root / "report.json"
            with (
                patch.object(prepare, "verify_checkout"),
                patch.object(prepare.subprocess, "run") as run,
                self.assertRaisesRegex(prepare.VendorPreparationError, "provenance contract"),
            ):
                prepare.prepare(source, receipt)
            run.assert_not_called()
            self.assertTrue(json.loads(receipt.read_text())["preexisting_vendor_tree_unqualified"])
            self.assertEqual(elf.read_bytes(), b"unqualified bytes")

    def test_old_synthetic_success_receipt_is_never_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            receipt = root / "report.json"
            receipt.write_text(json.dumps({
                "kind": "synthetic-probe-vendor-makefiles-not-publisher-provenance",
                "generated_makefile_sha256": {},
            }))
            with (
                patch.object(prepare, "verify_checkout"),
                self.assertRaisesRegex(prepare.VendorPreparationError, "not the current blocked-input"),
            ):
                prepare.prepare(source, receipt)

    def test_linked_vendor_root_and_receipt_are_rejected(self):
        for linked in ("vendor", "receipt"):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = self.source(root)
                receipt = root / "report.json"
                other = root / "other"
                other.mkdir()
                if linked == "vendor":
                    (source / "vendor").symlink_to(other, target_is_directory=True)
                else:
                    receipt.symlink_to(other / "outside.json")
                with (
                    patch.object(prepare, "verify_checkout"),
                    self.assertRaisesRegex(prepare.VendorPreparationError, "unsafe|symlink"),
                ):
                    prepare.prepare(source, receipt)
                self.assertEqual(list(other.iterdir()), [])

    def test_dirty_pinned_source_prevents_diagnostic_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            receipt = root / "report.json"
            with (
                patch.object(prepare, "verify_checkout", side_effect=ValueError("source revision changed")),
                self.assertRaisesRegex(ValueError, "source revision changed"),
            ):
                prepare.prepare(source, receipt)
            self.assertFalse(receipt.exists())

    def test_cli_exits_nonzero_with_retained_blocker_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            receipt = root / "report.json"
            stderr = io.StringIO()
            with (
                patch.object(prepare, "verify_checkout"),
                patch.object(sys, "argv", [
                    "prepare_vold_vendor_makefiles.py", "--source-root", str(source),
                    "--report", str(receipt),
                ]),
                redirect_stderr(stderr),
            ):
                self.assertEqual(prepare.main(), 1)
            self.assertIn(prepare.REQUIRED_ELF_EXAMPLE, stderr.getvalue())
            self.assertTrue(receipt.is_file())


if __name__ == "__main__":
    unittest.main()
