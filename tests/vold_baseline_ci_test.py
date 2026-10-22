#!/usr/bin/env python3
"""The original vold CI must compare genuine bytes, never patch or publish."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ci"))
import build_original_vold as builder  # noqa: E402
import vold_baseline_compare as comparator  # noqa: E402


class OriginalVoldBaselineTest(unittest.TestCase):
    @staticmethod
    def original_elf() -> bytes:
        data = bytearray(256)
        data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", data, 16, 3, 183)
        return bytes(data)

    def test_pinned_source_manifest_and_publisher_lock(self):
        lock, manifest = builder.load_contract()
        self.assertEqual(lock["kind"], "lineageos-unmodified-vold-baseline")
        self.assertEqual(lock["source_ota_sha256"], "8af46661a1440e98018bb2abb155317d6d4ae54e051160f0786b75c1e6806c82")
        self.assertEqual(lock["source_ota_url"], (
            "https://mirrorbits.lineageos.org/full/pdx235/20261016/"
            "lineage-24.0-20261016-nightly-pdx235-signed.zip"
        ))
        self.assertEqual(len(builder.project_map(manifest)), 1243)
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), lock["manifest"]["sha256"])
        self.assertEqual(lock["projects"]["vold"]["revision"], "11d8982f824d52e8122c7aa732207ba66b8f23c8")

    def test_exact_bytes_required_even_when_size_and_abi_match(self):
        lock, _ = builder.load_contract()
        original = self.original_elf()
        report = comparator.comparison(original, original, lock)
        self.assertEqual(report["first_difference_offset"], None)
        self.assertTrue(report["byte_identical"])
        comparator.require_identical(report)
        changed = bytearray(original)
        changed[-1] = 1
        mismatch = comparator.comparison(original, bytes(changed), lock)
        self.assertEqual(mismatch["first_difference_offset"], len(original) - 1)
        self.assertFalse(mismatch["byte_identical"])
        with self.assertRaisesRegex(comparator.BaselineError, "no timeout patch"):
            comparator.require_identical(mismatch)
        shortened = comparator.comparison(original, original[:-1], lock)
        self.assertEqual(shortened["first_difference_offset"], len(original) - 1)

    def test_reader_rejects_non_elf_and_symlinked_vold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "vold"
            target.write_bytes(self.original_elf())
            self.assertEqual(comparator.read_vold(target), self.original_elf())
            (root / "linked").symlink_to(target)
            with self.assertRaises(comparator.BaselineError):
                comparator.read_vold(root / "linked")
            target.write_bytes(b"not an elf")
            with self.assertRaises(comparator.BaselineError):
                comparator.read_vold(target)

    def test_receipt_is_written_but_mismatch_fails(self):
        lock, _ = builder.load_contract()
        orig = self.original_elf()
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "baseline-report.json"
            mismatch = comparator.comparison(orig, orig[:-1], lock)
            comparator.write_receipt(evidence, mismatch)
            self.assertFalse(json.loads(evidence.read_text())["byte_identical"])
            with self.assertRaises(comparator.BaselineError):
                comparator.require_identical(mismatch)
            with self.assertRaises(FileExistsError):
                comparator.write_receipt(evidence, mismatch)

    def test_repo_uses_selected_immutable_manifest_not_internal_override(self):
        lock, manifest = builder.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            calls = []

            def fake_command(argv, cwd, **_kwargs):
                calls.append(argv)
                repo_dir = cwd / ".repo"
                if argv[1] == "init" and "-m" not in argv:
                    (repo_dir / "manifests").mkdir(parents=True)
                    (repo_dir / "manifest.xml").write_text("old default")
                if argv[1] == "init" and "-m" in argv:
                    manifest_name = argv[argv.index("-m") + 1]
                    (repo_dir / "manifest.xml").write_bytes(
                        (repo_dir / "manifests" / manifest_name).read_bytes()
                    )

            with (
                patch.object(builder.shutil, "which", return_value="/trusted/repo"),
                patch.object(builder, "command", side_effect=fake_command),
                patch.object(builder, "verify_checkout"),
            ):
                builder.sync(source, lock, manifest, jobs=2)
            self.assertEqual([call[1] for call in calls], ["init", "init", "sync"])
            self.assertEqual(calls[1][-2:], ["-m", "modos-vold-original-20261016.xml"])
            self.assertEqual(
                (source / ".repo/manifests/modos-vold-original-20261016.xml").read_bytes(),
                manifest,
            )

    def test_baseline_build_removes_stale_output_and_rejects_nonidentical_bytes(self):
        lock, manifest = builder.load_contract()
        original = self.original_elf()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            (source / ".repo").mkdir(parents=True)
            (source / ".repo/manifest.xml").write_bytes(manifest)
            out = source / lock["build"]["artifact"]
            out.parent.mkdir(parents=True)
            out.write_bytes(b"stale build must be deleted")
            reference = root / "official-vold"
            reference.write_bytes(original)

            def fake_compile(argv, cwd, **_kwargs):
                self.assertEqual(argv[:2], ["bash", "-euc"])
                self.assertFalse(out.exists(), "stale binary must be removed first")
                self.assertIn("m -j2 vold", argv[2])
                out.write_bytes(original)
                out.chmod(0o755)

            with (
                patch.object(builder, "verify_checkout"),
                patch.object(builder, "command", side_effect=fake_compile),
            ):
                report = builder.build_baseline(
                    source, root / "proof", reference, lock, manifest, 2
                )
                self.assertTrue(report["byte_identical"])
                self.assertEqual(report["reference_sha256"], report["built_sha256"])

            def fake_bad_compile(argv, cwd, **_kwargs):
                bad = bytearray(original)
                bad[-1] = 99
                out.write_bytes(bad)
                out.chmod(0o755)

            with (
                patch.object(builder, "verify_checkout"),
                patch.object(builder, "command", side_effect=fake_bad_compile),
                self.assertRaises(comparator.BaselineError),
            ):
                builder.build_baseline(
                    source, root / "mismatch", reference, lock, manifest, 2
                )
            bad_report = json.loads((root / "mismatch/baseline-report.json").read_text())
            self.assertFalse(bad_report["byte_identical"])

    def test_workflow_is_opt_in_readonly_original_only(self):
        flow = (ROOT / ".github/workflows/vold-baseline.yml").read_text()
        original = (ROOT / "src/ci/build_original_vold.py").read_text()
        auth = (ROOT / "src/ci/extract_original_vold.sh").read_text()
        self.assertIn("workflow_dispatch:", flow)
        self.assertNotIn("  pull_request:", flow)
        self.assertNotIn("  schedule:", flow)
        self.assertIn("runs-on: [self-hosted, linux, x64, modos-lineage-large]", flow)
        self.assertIn("MODOS_VOLD_BASELINE_RUNNER_READY", flow)
        self.assertIn("free >= 100000000000", flow)
        self.assertIn("contents: read", flow)
        self.assertIn("baseline-report.json", flow)
        self.assertIn("rm -rf -- \"$VOLD_WORK\"", flow)
        self.assertIn("src/ci/probe_original_vold.sh", flow)
        probe = (ROOT / "src/ci/probe_original_vold.sh").read_text()
        self.assertIn("src/ci/build_original_vold.py compare", probe)
        self.assertIn("src/ci/build_original_vold.py probe", probe)
        self.assertIn("source_care_map_require_lineageos_pdx235_publisher", auth)
        self.assertIn("avbroot ota verify", auth)
        self.assertIn("avbroot ota extract", auth)
        self.assertIn("comparison(official, built, lock)", original)
        self.assertIn("verify_checkout(source, manifest, clean=True, selected=selected)", original)
        for text in (flow, original, auth, probe):
            self.assertNotIn("git apply", text)
            self.assertNotIn("ota patch", text)
            self.assertNotIn("force-publish", text)
            self.assertNotIn("publish: true", text)
            self.assertNotIn("lineage_vold_exfat_timeout", text)
            self.assertNotIn("lineage-vold-exfat-timeout.json", text)


if __name__ == "__main__":
    unittest.main()
