#!/usr/bin/env python3
"""Local host-tool bootstrap regressions; no downloads or Android builds."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ci"))
import setup_vold_local_tools as setup  # noqa: E402


class LocalVoldToolsTest(unittest.TestCase):
    def test_wrong_download_digest_is_rejected_without_execution_or_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "repo"
            executed = root / "untrusted-launcher-executed"
            payload = (f"#!{sys.executable}\nfrom pathlib import Path\n"
                       f"Path({str(executed)!r}).write_text('must not execute')\n").encode()

            def download(command, *, check):
                self.assertTrue(check)
                self.assertEqual(command[0], "curl")
                self.assertEqual(command[command.index("--proto") + 1], "=https")
                self.assertEqual(command[command.index("--proto-redir") + 1], "=https")
                self.assertIn(
                    "https://raw.githubusercontent.com/GerritCodeReview/git-repo/"
                    + setup.REPO_LAUNCHER_REV + "/repo", command,
                )
                Path(command[command.index("--output") + 1]).write_bytes(payload)
                return subprocess.CompletedProcess(command, 0)

            with patch.object(setup.subprocess, "run", side_effect=download) as run:
                with self.assertRaisesRegex(setup.LocalBuildError, "pinned SHA-256"):
                    setup.install_repo(binary)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(binary.exists())
            self.assertFalse(executed.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_verified_launcher_is_reused_without_download_or_execution(self):
        payload = b"synthetic reviewed launcher fixture\n"
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "repo"
            binary.write_bytes(payload)
            binary.chmod(0o600)
            with (
                patch.object(setup, "REPO_LAUNCHER_SHA256", hashlib.sha256(payload).hexdigest()),
                patch.object(setup.subprocess, "run", side_effect=AssertionError("reuse requires no command")),
            ):
                setup.install_repo(binary)
            self.assertEqual(binary.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(binary.stat().st_mode), 0o755)

    def test_corrupt_cached_launcher_and_symlink_fail_without_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "repo"
            binary.write_bytes(b"corrupt existing launcher")
            with patch.object(setup.subprocess, "run", side_effect=AssertionError("must fail before any command")):
                with self.assertRaisesRegex(setup.LocalBuildError, "reviewed SHA-256"):
                    setup.install_repo(binary)
                self.assertEqual(binary.read_bytes(), b"corrupt existing launcher")
                binary.rename(root / "target")
                binary.symlink_to(root / "target")
                with self.assertRaisesRegex(setup.LocalBuildError, "regular file"):
                    setup.install_repo(binary)
                self.assertTrue(binary.is_symlink())

    @staticmethod
    def shadow_environment(root: Path) -> tuple[dict[str, str], Path]:
        shadow = root / "path-shadow"
        shadow.mkdir()
        marker = root / "shadow-executed"
        payload = (f"#!{Path(sys.executable).resolve()}\nfrom pathlib import Path\n"
                   f"Path({str(marker)!r}).write_text('untrusted startup or PATH entry')\n"
                   "raise SystemExit(73)\n")
        for name in ("bash", "python", "python3", "avbroot"):
            path = shadow / name
            path.write_text(payload)
            path.chmod(0o755)
        (shadow / "sitecustomize.py").write_text(payload.split("\n", 1)[1])
        startup = root / "bash-startup"
        startup.write_text("printf 'untrusted shell startup' > " + str(marker) + "\nexit 73\n")
        environment = os.environ.copy()
        environment.update({
            "PATH": str(shadow) + os.pathsep + os.defpath,
            "PYTHONPATH": str(shadow),
            "BASH_ENV": str(startup),
        })
        return environment, marker

    def test_wrapper_preserves_arguments_and_ignores_path_and_startup_shadows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture-repository"
            (fixture / "src").mkdir(parents=True)
            # Exercise the real sealed-runner CLI parser at the wrapper's
            # execution boundary; cryptographic installation has separate tests.
            (fixture / "src/bootstrap_executable_tools.py").write_text(
                "import json, sys\n"
                f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
                "from bootstrap_executable_tools import parse_args\n"
                "args = parse_args(sys.argv[1:])\n"
                "print(json.dumps({'workdir': str(args.workdir), 'command': args.command,\n"
                " 'tool': args.tool, 'arguments': args.arguments, 'python': sys.executable,\n"
                " 'ignore_environment': sys.flags.ignore_environment,\n"
                " 'no_user_site': sys.flags.no_user_site,\n"
                " 'dont_write_bytecode': sys.flags.dont_write_bytecode}))\n"
            )
            store = root / "private-store"
            wrapper = root / "avbroot"
            with patch.object(setup, "ROOT", fixture):
                setup.write_executable(wrapper, setup.avbroot_wrapper(store))
            environment, marker = self.shadow_environment(root)
            arguments = [
                "ota", "verify", "--", "name with spaces", "quote'and\"double",
                "$(false)", "semi;colon", "line\nbreak",
            ]
            result = subprocess.run(
                [str(wrapper), *arguments], env=environment, cwd=root,
                check=True, capture_output=True, text=True,
            )
            observed = json.loads(result.stdout)
            self.assertEqual(observed["workdir"], str(store))
            self.assertEqual(observed["command"], "run")
            self.assertEqual(observed["tool"], "avbroot")
            self.assertEqual(observed["arguments"], arguments)
            self.assertEqual(Path(observed["python"]).resolve(), Path(sys.executable).resolve())
            self.assertEqual(observed["ignore_environment"], 1)
            self.assertEqual(observed["no_user_site"], 1)
            self.assertEqual(observed["dont_write_bytecode"], 1)
            self.assertFalse(marker.exists())

    def test_real_sealed_runner_refuses_missing_install_without_path_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "empty-store"
            store.mkdir(mode=0o700)
            wrapper = root / "avbroot"
            setup.write_executable(wrapper, setup.avbroot_wrapper(store))
            environment, marker = self.shadow_environment(root)
            result = subprocess.run(
                [str(wrapper), "--version"], env=environment, cwd=root,
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("executable bootstrap failed:", result.stderr)
            self.assertIn("cannot stat installed executable directory", result.stderr)
            self.assertFalse(marker.exists())

    def test_setup_does_not_claim_ready_if_sealed_execution_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            observed = []

            def invoke(command, **kwargs):
                observed.append(command)
                if "install" in command:
                    self.assertEqual(command[command.index("install") - 2], "--workdir")
                    self.assertEqual(command[-1], "avbroot")
                    return subprocess.CompletedProcess(command, 0)
                self.assertEqual(command, [str(Path(tmp) / "host-tools/bin/avbroot"), "--version"])
                raise subprocess.CalledProcessError(1, command)

            with (
                patch.object(setup.platform, "system", return_value="Linux"),
                patch.object(setup.platform, "machine", return_value="x86_64"),
                patch.object(setup, "sealed_runner_supported", return_value=True),
                patch.object(setup.shutil, "which", side_effect=lambda tool: "/usr/bin/" + tool),
                patch.object(setup, "install_repo"),
                patch.object(setup.subprocess, "run", side_effect=invoke),
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(setup.main(["--work-dir", tmp]), 1)
            self.assertEqual(len(observed), 2)
            self.assertIn("local tool setup failed", output.getvalue())
            self.assertNotIn("Private host tools are ready", output.getvalue())

    def test_unsupported_python_is_reported_before_any_tool_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with (
                patch.object(setup.platform, "system", return_value="Linux"),
                patch.object(setup.platform, "machine", return_value="x86_64"),
                patch.object(setup, "sealed_runner_supported", return_value=False),
                patch.object(setup, "setup", side_effect=AssertionError("unsupported host must not acquire tools")),
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(setup.main(["--work-dir", tmp]), 1)
            self.assertIn("/usr/bin/python3", output.getvalue())
            self.assertNotIn("Private host tools are ready", output.getvalue())


if __name__ == "__main__":
    unittest.main()
