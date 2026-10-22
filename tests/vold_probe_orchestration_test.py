#!/usr/bin/env python3
"""Exercise probe ordering at process boundaries without downloading Android."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProbeOrchestrationTest(unittest.TestCase):
    def run_probe(self, *, stage="probe", vendor_blocked=False,
                  static_additions=1, build_failures=0, diagnosed=True,
                  reference=True):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            binaries = work / "bin"
            binaries.mkdir()
            shim = binaries / "python3"
            shim.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
                import json
                import os
                from pathlib import Path
                import sys

                args = sys.argv[1:]
                work = Path(os.environ["VOLD_WORK"])
                history = work / "commands.jsonl"
                with history.open("a") as output:
                    output.write(json.dumps(args) + "\\n")
                previous = [json.loads(line) for line in history.read_text().splitlines()]

                def option(name):
                    return Path(args[args.index(name) + 1])

                if args[0].endswith("vold_source_closure.py"):
                    action = args[1]
                    option("--output").write_text("{}")
                    if action == "plan":
                        option("--output").write_text('{"selected_projects": ["system/vold"]}')
                    elif action == "index":
                        scans = sum(x[0].endswith("vold_source_closure.py") and x[1] == "index" for x in previous)
                        if scans <= int(os.environ["TEST_STATIC_ADDITIONS"]):
                            option("--next-selection").write_text('{"selected_projects": ["system/vold", "system/libbase"]}')
                    elif action == "diagnose" and os.environ["TEST_DIAGNOSED"] == "1":
                        option("--next-selection").write_text('{"selected_projects": ["system/vold", "system/libbase", "system/gsid"]}')
                elif args[0].endswith("prepare_vold_vendor_makefiles.py"):
                    option("--report").write_text('{"qualified": false}')
                    if os.environ["TEST_VENDOR_BLOCKED"] == "1":
                        print("missing proprietary ELF input", file=sys.stderr)
                        sys.exit(1)
                elif args[0].endswith("build_original_vold.py"):
                    if args[1] in ("sync", "sync-vendor-sources"):
                        option("--source-root").mkdir(exist_ok=True)
                    else:
                        builds = sum(x[0].endswith("build_original_vold.py") and x[1] in ("probe", "compare") for x in previous)
                        if builds <= int(os.environ["TEST_BUILD_FAILURES"]):
                            print('error: module "vold" depends on undefined module "libgsi"')
                            sys.exit(1)
                else:
                    sys.exit("unexpected command")
                '''))
            shim.chmod(0o755)
            environment = os.environ.copy()
            environment.pop("VOLD_SOURCE_REF", None)
            environment.update({
                "PATH": str(binaries) + os.pathsep + environment["PATH"],
                "VOLD_WORK": str(work),
                "VOLD_PROBE_STAGE": stage,
                "TEST_VENDOR_BLOCKED": str(int(vendor_blocked)),
                "TEST_STATIC_ADDITIONS": str(static_additions),
                "TEST_BUILD_FAILURES": str(build_failures),
                "TEST_DIAGNOSED": str(int(diagnosed)),
            })
            if stage == "compare" and reference:
                original = work / "authenticated-reference-fixture"
                original.write_bytes(b"reference fixture")
                environment["VOLD_SOURCE_REF"] = str(original)
            result = subprocess.run(
                ["bash", str(ROOT / "src/ci/probe_original_vold.sh")],
                cwd=ROOT, env=environment, text=True, capture_output=True,
            )
            history = work / "commands.jsonl"
            calls = [json.loads(line) for line in history.read_text().splitlines()] if history.exists() else []
            return result, calls

    @staticmethod
    def build_calls(calls):
        return [args for args in calls if args[0].endswith("build_original_vold.py")]

    def test_vendor_blocker_precedes_toolchain_sync_and_build(self):
        result, calls = self.run_probe(vendor_blocked=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing proprietary ELF", result.stderr)
        self.assertEqual([x[1] for x in self.build_calls(calls)], ["sync-vendor-sources"])
        self.assertFalse(any(x[1] == "index" for x in calls))

    def test_probe_needs_no_ota_and_scans_before_compiling(self):
        result, calls = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no authenticated binary equality", result.stdout)
        self.assertEqual([x[1] for x in self.build_calls(calls)],
                         ["sync-vendor-sources", "sync", "sync", "probe"])
        resume = [x for x in self.build_calls(calls) if x[1] == "sync"]
        self.assertTrue(all("--resume" in x and "--projects-file" in x for x in resume))
        actions = [x[1] for x in calls]
        self.assertLess(actions.index("index"), actions.index("probe"))

    def test_static_scans_are_bounded(self):
        result, calls = self.run_probe(static_additions=99)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(x[1] == "index" for x in calls), 3)
        self.assertEqual(sum(x[1] == "probe" for x in calls), 1)

    def test_build_error_expands_once_then_reuses_checkout(self):
        result, calls = self.run_probe(static_additions=0, build_failures=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([x[1] for x in self.build_calls(calls)],
                         ["sync-vendor-sources", "sync", "probe", "sync", "probe"])

    def test_unknown_errors_and_compile_budget_stop(self):
        for diagnosed, expected in ((False, 1), (True, 3)):
            with self.subTest(diagnosed=diagnosed):
                result, calls = self.run_probe(static_additions=0,
                                              build_failures=99, diagnosed=diagnosed)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sum(x[1] == "probe" for x in calls), expected)

    def test_compare_requires_reference_and_never_calls_probe(self):
        result, calls = self.run_probe(stage="compare", reference=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        result, calls = self.run_probe(stage="compare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([x[1] for x in self.build_calls(calls)][-1], "compare")
        self.assertFalse(any(x[1] == "probe" for x in calls))


if __name__ == "__main__":
    unittest.main()
