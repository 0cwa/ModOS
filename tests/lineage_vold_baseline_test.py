#!/usr/bin/env python3
"""Regression gates for baseline-first unmodified pdx235 vold reproduction."""

import copy
import hashlib
import json
import os
import subprocess
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
        self.ota_digest = self.lock["source_ota_sha256"]

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
            reference = root / "reference-vold"
            reference.write_bytes(self.reference)

            def fake_original_build(source_root, lock, manifest, jobs):
                self.assertEqual(source_root, source)
                self.assertEqual(lock["build"]["release"], "cp2a")
                self.assertEqual(jobs, 2)
                out_file.write_bytes(self.reference)
                out_file.chmod(0o755)
                return out_file

            with (
                patch.object(build_lineage_vold.original, "compile_original_vold", side_effect=fake_original_build),
                patch.object(build_lineage_vold.subprocess, "run") as patch_runner,
            ):
                artifact = root / "out"
                evidence = build_lineage_vold.command_baseline(
                    source, artifact, reference, self.ota_digest, 2
                )
                self.assertTrue(evidence["byte_identical"])
                self.assertEqual((artifact / "baseline-vold").read_bytes(), self.reference)
                self.assertTrue((artifact / "baseline-report.json").exists())
                patch_runner.assert_not_called()

            # Even if the baseline command succeeded, a caller without the
            # actual baseline receipt/reference is not authorized to patch.
            with self.assertRaisesRegex(build_lineage_vold.BuildError, "gated on exact baseline"):
                build_lineage_vold.command_build(
                    source, root / "patched", 2,
                    root / "nonexistent-report.json", reference, reference,
                )

    def test_patched_build_reuses_guarded_setup_and_never_accepts_stale_output(self):
        original = build_lineage_vold.original
        original_lock, manifest = original.load_contract()
        for outcome in ("fresh", "no-output", "unexpected-source", "dependency-bypass"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "source"
                (source / ".repo").mkdir(parents=True)
                (source / ".repo/manifest.xml").write_bytes(manifest)
                release = source / original_lock["build"]["release_config"]["path"]
                release.parent.mkdir(parents=True)
                release.write_text("# Updated manually\naosp_target_release=cp2a\n")
                vold = source / "system/vold"
                (vold / "fs").mkdir(parents=True)
                utils = "static constexpr std::chrono::seconds kUntrustedFsckSleepTime(45);\nstatic constexpr std::chrono::seconds kUntrustedMountSleepTime(20);\n"
                exfat = "    int rc = ForkExecvpTimeout(cmd, kUntrustedFsckSleepTime, sFsckUntrustedContext);\n    if (rc == 0) {\n"
                (vold / "Utils.h").write_text("\n" * 40 + utils)
                (vold / "fs/Exfat.cpp").write_text("\n" * 46 + exfat)
                subprocess.run(["git", "init", "--quiet", str(vold)], check=True)
                (vold / "fixture-vold").write_bytes(self.reference)
                artifact = source / self.lock["build"]["artifact"]
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(self.reference)
                artifact.chmod(0o755)
                setup = source / "build/make"
                setup.mkdir(parents=True)
                (source / "build/envsetup.sh").symlink_to("make/envsetup.sh")
                build = "return 0;"
                if outcome != "no-output":
                    build = "mkdir -p out/target/product/pdx235/system/bin; cp system/vold/fixture-vold out/target/product/pdx235/system/bin/vold; chmod +x out/target/product/pdx235/system/bin/vold;"
                if outcome == "unexpected-source":
                    build += " mkdir -p vendor/unreviewed; touch vendor/unreviewed/Android.bp;"
                (setup / "envsetup.sh").write_text(
                    ': "${UNSET_OPTIONAL_VOLD_TEST_VARIABLE}"\n'
                    'function check_product() { [[ "$*" == "lineage_pdx235 cp2a" ]]; }\n'
                    'function _lunch_meat() { [[ "$*" == "lineage_pdx235 cp2a userdebug" ]]; }\n'
                    'function lunch() { return 99; }\n'
                    'function m() { [[ "$OUT_DIR" == out && "$ROOMSERVICE_DRYRUN" == true && -z "${OUT_DIR_COMMON_BASE}" ]] || return 98; '
                    + build + ' }\n'
                )
                reference = root / "reference-vold"
                reference.write_bytes(self.reference)
                report = root / "baseline-report.json"
                baseline.write_report(report, baseline.comparison_report(
                    self.reference, self.reference, self.lock, self.ota_digest
                ))
                destination = root / "patched"
                environment = {"OUT_DIR": "/foreign-build", "OUT_DIR_COMMON_BASE": "/foreign-common"}
                if outcome == "dependency-bypass":
                    environment["SOONG_ALLOW_MISSING_DEPENDENCIES"] = "true"
                with (
                    patch.object(original, "verify_checkout"),
                    patch.object(original, "verified_vendor_inputs", return_value={"fixture": True}),
                    patch.dict(os.environ, environment),
                ):
                    if outcome == "fresh":
                        build_lineage_vold.command_build(source, destination, 2, report, reference, reference)
                        self.assertEqual((destination / "vold").read_bytes(), self.reference)
                        self.assertEqual(json.loads((destination / "provenance.json").read_text())["baseline_reference_sha256"], self.digest)
                    else:
                        with self.assertRaises((build_lineage_vold.BuildError, original.BuildError)):
                            build_lineage_vold.command_build(source, destination, 2, report, reference, reference)
                        self.assertFalse((destination / "provenance.json").exists())
                        self.assertFalse((destination / "vold").exists())
                self.assertEqual((vold / "Utils.h").read_text(), "\n" * 40 + utils)
                self.assertEqual((vold / "fs/Exfat.cpp").read_text(), "\n" * 46 + exfat)
                if outcome == "no-output":
                    self.assertFalse(artifact.exists())

    def test_archived_ota_fallback_is_sha_pinned_and_cannot_hide_disagreement(self):
        expected_url = self.lock["source_ota_url"]
        expected_sha = self.lock["source_ota_sha256"]
        self.assertEqual(
            ota.resolve_source(None, self.lock), (expected_url, expected_sha)
        )
        self.assertEqual(
            ota.resolve_source([], self.lock), (expected_url, expected_sha)
        )
        current = [{
            "type": "nightly",
            "files": [{
                "type": "nightly", "filename": ota.EXPECTED,
                "url": expected_url,
                "sha256": expected_sha,
            }],
        }]
        self.assertEqual(
            ota.resolve_source(current, self.lock), (expected_url, expected_sha)
        )
        forged = copy.deepcopy(current)
        forged[0]["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ota.SourceSelectionError, "disagrees"):
            ota.resolve_source(forged, self.lock)
        bad_lock = {**self.lock, "source_ota_url": "https://evil.invalid/" + ota.EXPECTED}
        with self.assertRaisesRegex(ota.SourceSelectionError, "locked official archive"):
            ota.resolve_source(None, bad_lock)

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
