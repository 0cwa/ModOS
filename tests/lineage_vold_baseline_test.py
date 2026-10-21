#!/usr/bin/env python3
"""Regression gates for baseline-first unmodified pdx235 vold reproduction."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "ci"), str(ROOT / "tests")]

import build_lineage_vold  # noqa: E402
import lineage_vold_baseline as baseline  # noqa: E402
import resolve_lineage_vold_ota as ota  # noqa: E402
from lineage_vold_contract_test import LineageVoldContractTest  # noqa: E402


class BaselineFirstTest(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "locks/lineage-vold-exfat-timeout.json").read_text())
        self.reference = LineageVoldContractTest.fixture_elf()
        self.digest = hashlib.sha256(self.reference).hexdigest()
        self.ota_digest = "e" * 64

    def test_identical_binary_is_required_and_report_roundtrips(self):
        good = baseline.comparison_report(self.reference, self.reference, self.lock, self.ota_digest)
        self.assertTrue(good["byte_identical"])
        self.assertTrue(good["elf_identity_matches"])
        self.assertIsNone(good["first_difference_offset"])
        self.assertEqual(good["reference_sha256"], self.digest)
        baseline.require_exact_match(good)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reference-vold").write_bytes(self.reference)
            (root / "baseline-vold").write_bytes(self.reference)
            baseline.write_report(root / "report.json", good)
            self.assertEqual(
                baseline.verify_baseline_inputs(
                    root / "report.json", root / "reference-vold", root / "baseline-vold", self.lock
                ),
                good,
            )
            # A receipt is insufficient if the supposedly reproduced binary
            # has been changed between proof and patched build.
            tampered = bytearray(self.reference)
            tampered[-1] = 1
            (root / "baseline-vold").write_bytes(tampered)
            with self.assertRaisesRegex(baseline.BaselineMismatch, "evidence differs"):
                baseline.verify_baseline_inputs(
                    root / "report.json", root / "reference-vold", root / "baseline-vold", self.lock
                )

    def test_same_elf_abi_is_not_sufficient(self):
        differing = bytearray(self.reference)
        differing[-1] = 1
        report = baseline.comparison_report(self.reference, bytes(differing), self.lock, self.ota_digest)
        self.assertTrue(report["elf_identity_matches"])
        self.assertFalse(report["byte_identical"])
        self.assertEqual(report["first_difference_offset"], len(differing) - 1)
        with self.assertRaisesRegex(baseline.BaselineMismatch, "NOT byte-identical"):
            baseline.require_exact_match(report)
        with self.assertRaises(baseline.BaselineMismatch):
            baseline.comparison_report(self.reference, self.reference, self.lock, "invalid")

    def test_baseline_only_build_never_applies_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / ".repo").mkdir(parents=True)
            (source / ".repo" / "manifest.xml").touch()
            out_file = source / self.lock["build"]["artifact"]
            out_file.parent.mkdir(parents=True)
            expected = {
                x["path"]: (x["name"], x["revision"])
                for x in self.lock["projects"].values()
            }
            reference = root / "reference-vold"
            reference.write_bytes(self.reference)

            def fake_android_build(argv, **kwargs):
                self.assertEqual(argv[:2], ["bash", "-euc"])
                self.assertIn("m ", argv[2])
                self.assertIn(" vold", argv[2])
                self.assertNotIn("apply", argv[2])
                out_file.write_bytes(self.reference)
                out_file.chmod(0o755)
                return type("Run", (), {"returncode": 0})()

            with (
                patch.object(build_lineage_vold, "load_contract",
                             return_value=(self.lock, b"manifest", b"patch")),
                patch.object(build_lineage_vold, "project_map", return_value=expected),
                patch.object(build_lineage_vold, "checked_source_manifest"),
                patch.object(build_lineage_vold, "verify_source_checkouts_clean"),
                patch.object(build_lineage_vold.subprocess, "run", side_effect=fake_android_build)
            ):
                artifact = root / "out"
                evidence = build_lineage_vold.command_baseline(
                    source, artifact, reference, self.ota_digest, 2
                )
                self.assertTrue(evidence["byte_identical"])
                self.assertEqual((artifact / "baseline-vold").read_bytes(), self.reference)
                self.assertTrue((artifact / "baseline-report.json").exists())

            # Even if the baseline command succeeded, a caller without the
            # actual baseline receipt/reference is not authorized to patch.
            with self.assertRaisesRegex(build_lineage_vold.BuildError, "gated on exact baseline"):
                build_lineage_vold.command_build(
                    source, root / "patched", 2,
                    root / "nonexistent-report.json", reference, reference,
                )

    def test_official_api_requires_one_exact_nightly_and_digest(self):
        record = {
            "type": "nightly",
            "files": [{
                "type": "nightly", "filename": ota.EXPECTED,
                "sha256": self.ota_digest,
                "url": "https://mirrorbits.lineageos.org/full/pdx235/20261016/" + ota.EXPECTED,
            }],
        }
        self.assertEqual(
            ota.choose_official_file([record], self.ota_digest)[1], self.ota_digest
        )
        with self.assertRaisesRegex(ota.SourceSelectionError, "absent"):
            ota.choose_official_file([])
        with self.assertRaisesRegex(ota.SourceSelectionError, "disagrees"):
            ota.choose_official_file([record], "a" * 64)
        changed = copy.deepcopy(record)
        changed["files"][0]["sha256"] = "a" * 64
        with self.assertRaisesRegex(ota.SourceSelectionError, "ambiguous"):
            ota.choose_official_file([record, changed])
        changed["files"][0]["url"] = "http://mirrorbits.lineageos.org/full/pdx235/20261016/" + ota.EXPECTED
        with self.assertRaisesRegex(ota.SourceSelectionError, "unexpected origin"):
            ota.choose_official_file([changed])


if __name__ == "__main__":
    unittest.main()
