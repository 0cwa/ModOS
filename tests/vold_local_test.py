#!/usr/bin/env python3
"""Local build orchestration/cache regressions; no Android or OTA downloads."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ci"))
import vold_local as local  # noqa: E402


class LocalVoldTest(unittest.TestCase):
    def setUp(self):
        self.contract, _ = local.load_contract()
        self.output = io.StringIO()

    @staticmethod
    def elf():
        result = bytearray(256)
        result[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", result, 16, 3, 183)
        return bytes(result)

    @staticmethod
    def healthy_report(work):
        return {
            "work_dir": str(work), "host": {"system": "Linux", "machine": "x86_64"},
            "jobs": 4, "free_disk_bytes": 50_000_000_000,
            "physical_memory_bytes": None, "notes": [], "errors": [],
            "missing_tools": [], "ready_for_requested_stage": True,
        }

    def test_plan_is_offline_and_preserves_an_expanded_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            selection = work / "source-selection.json"
            selection.write_text('{"expanded": "preserve me"}')
            with (
                patch.object(local, "run_logged", side_effect=AssertionError("no subprocess/download needed")),
                redirect_stdout(self.output),
            ):
                self.assertEqual(local.main(["plan", "--work-dir", tmp]), 0)
            self.assertEqual(json.loads(selection.read_text()), {"expanded": "preserve me"})
            plan = json.loads((work / "source-provisional-plan.json").read_text())
            self.assertGreater(len(plan["selected_projects"]), 3)
            self.assertEqual(plan["manifest_sha256"], self.contract["manifest"]["sha256"])

    def test_doctor_only_requires_explicit_disk_floor_and_stage_tools(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(local.platform, "system", return_value="Linux"),
            patch.object(local.platform, "machine", return_value="x86_64"),
            patch.object(local.shutil, "disk_usage", return_value=SimpleNamespace(free=40_000_000_000)),
            patch.object(local.shutil, "which", side_effect=lambda tool: None if tool == "avbroot" else "/usr/bin/" + tool),
        ):
            work = Path(tmp)
            probe = local.doctor(work, "probe", 2, None)
            self.assertTrue(probe["ready_for_requested_stage"])
            self.assertTrue(probe["notes"])
            self.assertNotIn("avbroot", probe["tool_paths"])
            self.assertFalse(local.doctor(work, "probe", 2, 100)["ready_for_requested_stage"])
            compare = local.doctor(work, "compare", 2, None)
            self.assertEqual(compare["missing_tools"], ["avbroot"])
            self.assertFalse(compare["ready_for_requested_stage"])
            self.assertTrue(local.doctor(work, "vendor", 2, None)["ready_for_requested_stage"])

    def test_workspace_rejects_links_spaces_and_concurrent_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(local.LocalBuildError, "whitespace"):
                local.work_directory(root / "source with spaces")
            (root / "linked").symlink_to(root / "actual")
            with self.assertRaisesRegex(local.LocalBuildError, "unsafe"):
                local.work_directory(root / "linked")
            work = local.work_directory(root / "actual")
            with local.exclusive_workspace(work):
                with self.assertRaisesRegex(local.LocalBuildError, "Another local vold"):
                    with local.exclusive_workspace(work):
                        self.fail("concurrent build entered workspace")
            with local.exclusive_workspace(work):
                pass

    def test_failed_probe_retains_outputs_and_gets_unique_per_run_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            cached = work / "source/out/preserved"
            cached.parent.mkdir(parents=True)
            cached.write_text("incremental output")
            observed = []

            def fail_stage(command, *, log, env):
                observed.append((command, log, env))
                self.assertEqual(env["VOLD_WORK"], tmp)
                self.assertEqual(env["VOLD_PROBE_STAGE"], "probe")
                self.assertEqual(env["VOLD_PROBE_JOBS"], "3")
                self.assertNotIn("VOLD_SOURCE_REF", env)
                self.assertEqual(Path(env["VOLD_RUN_DIR"]), log.parent)
                raise local.LocalBuildError("fixture dependency missing")

            with (
                patch.object(local, "doctor", return_value=self.healthy_report(work)),
                patch.object(local, "run_logged", side_effect=fail_stage),
                patch.dict(os.environ, {"VOLD_SOURCE_REF": "/untrusted/old-reference"}),
                redirect_stdout(self.output), redirect_stderr(self.output),
            ):
                for _ in range(2):
                    self.assertEqual(local.main(["probe", "--work-dir", tmp, "--jobs", "3"]), 1)
            self.assertEqual(cached.read_text(), "incremental output")
            self.assertEqual(len(observed), 2)
            self.assertNotEqual(observed[0][1], observed[1][1])
            records = list((work / "runs").glob("*/local-run.json"))
            self.assertEqual(len(records), 2)
            self.assertTrue(all(json.loads(path.read_text())["status"] == "interrupted-or-failed" for path in records))

    def test_compare_does_not_download_or_authenticate_until_probe_succeeds(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(local, "doctor", return_value=self.healthy_report(Path(tmp))),
            patch.object(local, "source_stage", side_effect=local.LocalBuildError("build blocked")) as stage,
            patch.object(local, "exact_ota") as download,
            patch.object(local, "authenticated_reference") as authenticate,
            redirect_stdout(self.output), redirect_stderr(self.output),
        ):
            self.assertEqual(local.main(["compare", "--work-dir", tmp]), 1)
            self.assertEqual(stage.call_args.args[0], "probe")
            download.assert_not_called()
            authenticate.assert_not_called()

    def test_cached_and_supplied_ota_still_require_exact_filename_and_digest(self):
        payload = b"synthetic OTA fixture, not a genuine release"
        contract = {**self.contract, "source_ota_sha256": hashlib.sha256(payload).hexdigest()}
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            cache = work / "cache"
            cache.mkdir()
            archive = cache / local.ota_filename(contract)
            archive.write_bytes(payload)
            with patch.object(local, "run_logged", side_effect=AssertionError("cached bytes require no download")), redirect_stdout(self.output):
                self.assertEqual(local.exact_ota(work, work, contract), archive)
                self.assertEqual(local.exact_ota(work, work, contract, archive), archive)
                renamed = work / "different-name.zip"
                renamed.write_bytes(payload)
                with self.assertRaisesRegex(local.LocalBuildError, "exact locked official"):
                    local.exact_ota(work, work, contract, renamed)
                archive.write_bytes(b"wrong digest")
                with self.assertRaisesRegex(local.LocalBuildError, "digest differs"):
                    local.exact_ota(work, work, contract)

    def test_partial_download_resumes_https_and_promotes_only_after_hash_match(self):
        payload = b"complete fixture OTA"
        contract = {**self.contract, "source_ota_sha256": hashlib.sha256(payload).hexdigest()}
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "cache").mkdir()
            partial = work / "cache" / (local.ota_filename(contract) + ".part")
            partial.write_bytes(payload[:4])

            def download(command, *, log):
                self.assertIn("--continue-at", command)
                self.assertEqual(command[command.index("--proto-redir") + 1], "=https")
                self.assertEqual(command[-1], contract["source_ota_url"])
                self.assertEqual(Path(command[command.index("--output") + 1]), partial)
                partial.write_bytes(payload)

            with patch.object(local, "run_logged", side_effect=download) as run:
                archive = local.exact_ota(work, work, contract)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(partial.exists())
            self.assertEqual(archive.read_bytes(), payload)
            archive.unlink()
            with patch.object(local, "run_logged", side_effect=lambda *a, **k: partial.write_bytes(b"bad")):
                with self.assertRaisesRegex(local.LocalBuildError, "failed the pinned SHA-256"):
                    local.exact_ota(work, work, contract)
            self.assertFalse(archive.exists())

    def test_reference_is_freshly_derived_from_authenticated_archive_each_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            archive = work / local.ota_filename(self.contract)
            archive.write_bytes(b"fixture validated by caller")
            run_dir = work / "logs"
            run_dir.mkdir()
            observed = []

            def authenticate(command, *, log):
                observed.append(command)
                self.assertIn("extract_original_vold.sh", command[1])
                self.assertEqual(command[-2], str(archive))
                directory = Path(command[-1])
                directory.mkdir(parents=True)
                (directory / "original-vold").write_bytes(self.elf())
                (directory / "source-otacert.pem").write_text("fixture certificate")

            with (
                patch.object(local, "authentication_inputs", return_value={"fixture": "stable reviewed policy"}),
                patch.object(local, "run_logged", side_effect=authenticate),
                redirect_stdout(self.output),
            ):
                reference = local.authenticated_reference(archive, work, run_dir, self.contract)
                saved = json.loads((reference.parent / "reference-receipt.json").read_text())
                self.assertTrue(saved["independent_publisher_and_ota_signature_verified"])
                # An editable receipt can be recomputed to match substituted
                # reference bytes; it must never become an authenticity gate.
                reference.write_bytes(self.elf()[:-1] + b"x")
                forged = local.reference_receipt(reference.parent, self.contract, {"fixture": "stable reviewed policy"})
                (reference.parent / "reference-receipt.json").write_text(json.dumps(forged))
                with self.assertRaisesRegex(local.LocalBuildError, "existing ELF files or receipts"):
                    local.authenticated_reference(archive, work, run_dir, self.contract)
                next_run = work / "next-run"
                next_run.mkdir()
                fresh = local.authenticated_reference(archive, work, next_run, self.contract)
                self.assertNotEqual(reference, fresh)
                self.assertEqual(fresh.read_bytes(), self.elf())
                self.assertEqual(len(observed), 2)

    def test_standalone_elf_without_receipt_is_never_accepted_as_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            directory = work / "cache" / (self.contract["base_build_id"] + "-reference")
            directory.mkdir(parents=True)
            (directory / "original-vold").write_bytes(self.elf())
            (directory / "source-otacert.pem").write_text("unverified certificate")
            with (
                patch.object(local, "authentication_inputs", return_value={"fixture": "policy"}),
                patch.object(local, "run_logged", side_effect=local.LocalBuildError("authentic extraction required")) as run,
                redirect_stdout(self.output),
                self.assertRaisesRegex(local.LocalBuildError, "authentic extraction required"),
            ):
                local.authenticated_reference(work / "official.zip", work, work, self.contract)
            self.assertIn("extract_original_vold.sh", run.call_args.args[0][1])

    def test_success_requires_this_runs_matching_byte_identity_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "original-vold"
            reference.write_bytes(self.elf())
            phase = root / "current-compare"
            (phase / "baseline-1").mkdir(parents=True)
            report = {
                "byte_identical": True,
                "official_ota_sha256": self.contract["source_ota_sha256"],
                "manifest_sha256": self.contract["manifest"]["sha256"],
                "reference_sha256": local.digest_file(reference),
                "built_sha256": local.digest_file(reference),
            }
            stale = root / "older-run/baseline-1"
            stale.mkdir(parents=True)
            (stale / "baseline-report.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(local.LocalBuildError, "this invocation"):
                local.require_comparison_receipt(phase, reference, self.contract)
            proof = phase / "baseline-1/baseline-report.json"
            proof.write_text(json.dumps(report))
            self.assertEqual(local.require_comparison_receipt(phase, reference, self.contract), proof)
            for change in ({"byte_identical": "true"}, {"built_sha256": "0" * 64}, {"official_ota_sha256": "0" * 64}):
                with self.subTest(change=change):
                    proof.write_text(json.dumps({**report, **change}))
                    with self.assertRaises(local.LocalBuildError):
                        local.require_comparison_receipt(phase, reference, self.contract)

    def test_one_command_compare_orders_probe_authentication_and_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            reference = work / "original-vold"
            reference.write_bytes(self.elf())
            events = []

            def stage(name, _work, run_dir, jobs, ref=None):
                events.append(name)
                phase = run_dir / name
                phase.mkdir()
                if name == "compare":
                    self.assertEqual(ref, reference)
                    (phase / "baseline-1").mkdir()
                    (phase / "baseline-1/baseline-report.json").write_text(json.dumps({
                        "byte_identical": True,
                        "official_ota_sha256": self.contract["source_ota_sha256"],
                        "manifest_sha256": self.contract["manifest"]["sha256"],
                        "reference_sha256": local.digest_file(reference),
                        "built_sha256": local.digest_file(reference),
                    }))
                return phase

            def ota(*_args):
                events.append("exact-ota")
                return work / "archive.zip"

            def auth(*_args):
                events.append("authenticate")
                return reference

            with (
                patch.object(local, "doctor", return_value=self.healthy_report(work)),
                patch.object(local, "source_stage", side_effect=stage),
                patch.object(local, "exact_ota", side_effect=ota),
                patch.object(local, "authenticated_reference", side_effect=auth),
                redirect_stdout(self.output),
            ):
                self.assertEqual(local.main(["compare", "--work-dir", tmp]), 0)
            self.assertEqual(events, ["probe", "exact-ota", "authenticate", "compare"])
            record = json.loads(next((work / "runs").glob("*/local-run.json")).read_text())
            self.assertEqual(record["status"], "completed")
            self.assertTrue(Path(record["baseline_report"]).is_file())

    def test_logged_command_preserves_failure_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "failure.log"
            with redirect_stdout(self.output), self.assertRaisesRegex(local.LocalBuildError, "status 7"):
                local.run_logged([sys.executable, "-c", "print('fixture failure'); raise SystemExit(7)"], log=log)
            self.assertIn("fixture failure", log.read_text())


if __name__ == "__main__":
    unittest.main()
