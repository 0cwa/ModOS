#!/usr/bin/env python3
"""Exercise Shizuku device-check verdicts without requiring an Android device."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CHECK = Path(__file__).with_name("shizuku_post_boot_check.sh")
PACKAGE = "moe.shizuku.privileged.api"
GET_STATE = ("-d", "get-state")
PACKAGE_PATH = ("-d", "shell", "pm", "path", PACKAGE)
INIT_STATE = ("-d", "shell", "getprop", "init.svc.shizuku")
ENFORCING = ("-d", "shell", "getenforce")
BOOT_RECEIVERS = (
    "-d", "shell", "cmd", "package", "query-receivers", "--components", "--user", "0",
    "-a", "android.intent.action.LOCKED_BOOT_COMPLETED", "-p", PACKAGE,
)
ROOTLESS_PROCESS = ("-d", "shell", "ps", "-A", "-o", "LABEL,UID,PID,NAME")
MAGISK_PROCESS = ("-d", "shell", "ps", "-A", "-o", "UID,PID,NAME")
BINDER = (
    "-d", "shell", "content", "call",
    "--uri", f"content://{PACKAGE}.shizuku",
    "--method", "getBinder", "--extra", "modos_read_only_probe:s:1",
)
WIRELESS = ("-d", "shell", "settings", "get", "global", "adb_wifi_enabled")
ROOTLESS_ROWS = "LABEL UID PID NAME\nu:r:init:s0 0 1 init\nu:r:shell:s0 2000 42 shizuku_server\n"
MAGISK_ROWS = "UID PID NAME\n0 1 init\n0 42 shizuku_server\n"


class ShizukuPostBootCheckTest(unittest.TestCase):
    def run_check(
        self,
        mode: str = "rootless",
        overrides: dict[tuple[str, ...], tuple[int, str]] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[tuple[str, ...]]]:
        responses = {
            GET_STATE: (0, "device\n"),
            PACKAGE_PATH: (0, "package:/product/app/Shizuku/Shizuku.apk\n"),
            BINDER: (0, "Result: Bundle[{moe.shizuku.privileged.api.intent.extra.BINDER=binder}]\n"),
            WIRELESS: (0, "0\n"),
        }
        if mode == "rootless":
            responses.update({
                INIT_STATE: (0, "running\n"),
                ENFORCING: (0, "Enforcing\n"),
                BOOT_RECEIVERS: (0, "No receivers found\n"),
                ROOTLESS_PROCESS: (0, ROOTLESS_ROWS),
            })
        else:
            responses[MAGISK_PROCESS] = (0, MAGISK_ROWS)
        responses.update(overrides or {})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            response_file = root / "responses.json"
            response_file.write_text(json.dumps({
                json.dumps(command): response for command, response in responses.items()
            }))
            calls_file = root / "calls.jsonl"
            adb = root / "adb"
            adb.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['SHIZUKU_TEST_CALLS'], 'a') as output:\n"
                "    output.write(json.dumps(args) + '\\n')\n"
                "responses = json.loads(Path(os.environ['SHIZUKU_TEST_RESPONSES']).read_text())\n"
                "response = responses.get(json.dumps(args))\n"
                "if response is None:\n"
                "    print('UNEXPECTED ADB COMMAND: ' + repr(args))\n"
                "    sys.exit(97)\n"
                "sys.stdout.write(response[1])\n"
                "sys.exit(response[0])\n"
            )
            adb.chmod(0o755)
            result = subprocess.run(
                [shutil.which("bash") or "bash", str(CHECK), mode],
                env={
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                    "SHIZUKU_TEST_RESPONSES": str(response_file),
                    "SHIZUKU_TEST_CALLS": str(calls_file),
                },
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            calls = [tuple(json.loads(line)) for line in calls_file.read_text().splitlines()]
        # Every issued operation must match a read-only USB-only command we
        # explicitly modeled. A typo or unexpected mutation cannot be hidden
        # behind the checker's deliberately inconclusive exit status.
        self.assertTrue(all(call in responses for call in calls), calls)
        self.assertTrue(all(call[0] == "-d" for call in calls), calls)
        self.assertEqual("", result.stderr)
        return result, calls

    def test_rootless_pass_checks_init_domain_enforcing_and_wireless_off(self) -> None:
        result, calls = self.run_check()
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("init.svc.shizuku is running", result.stdout)
        self.assertIn("SELinux is Enforcing", result.stdout)
        self.assertIn("manager boot receiver is disabled for user 0", result.stdout)
        self.assertIn("SELinux domain u:r:shell:s0", result.stdout)
        self.assertEqual(
            {GET_STATE, PACKAGE_PATH, INIT_STATE, ENFORCING, BOOT_RECEIVERS, ROOTLESS_PROCESS, BINDER, WIRELESS},
            set(calls),
        )
        self.assertIn("manual approval check remains required", result.stdout)

    def test_magisk_keeps_root_uid_check_without_rootless_init_requirements(self) -> None:
        result, calls = self.run_check("magisk")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("UID 0", result.stdout)
        self.assertEqual({GET_STATE, PACKAGE_PATH, MAGISK_PROCESS, BINDER, WIRELESS}, set(calls))

    def test_unavailable_usb_device_stops_before_device_queries(self) -> None:
        result, calls = self.run_check(overrides={GET_STATE: (1, "error: no devices\n")})
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertEqual([GET_STATE], calls)

    def test_known_failures_never_pass(self) -> None:
        cases = [
            (PACKAGE_PATH, "", "no installed path"),
            (INIT_STATE, "stopped\n", "init.svc.shizuku is stopped"),
            (INIT_STATE, "restarting\n", "init.svc.shizuku is restarting"),
            (ENFORCING, "Permissive\n", "SELinux is Permissive"),
            (ENFORCING, "Disabled\n", "SELinux is Disabled"),
            (BOOT_RECEIVERS, f"{PACKAGE}/moe.shizuku.manager.receiver.BootCompleteReceiver\n", "turn off Start on boot"),
            (ROOTLESS_PROCESS, "LABEL UID PID NAME\nu:r:init:s0 0 1 init\n", "not running"),
            (ROOTLESS_PROCESS, ROOTLESS_ROWS.replace("2000 42", "0 42"), "unexpected UID"),
            (ROOTLESS_PROCESS, ROOTLESS_ROWS + "u:r:shell:s0 2000 43 shizuku_server\n", "found 2"),
            (ROOTLESS_PROCESS, ROOTLESS_ROWS.replace("u:r:shell:s0", "u:r:init:s0"), "expected u:r:shell:s0"),
            (BINDER, "Result: null\n", "no live Shizuku Binder"),
            (WIRELESS, "1\n", "adb_wifi_enabled is 1"),
        ]
        for command, output, message in cases:
            with self.subTest(command=command, output=output):
                result, _ = self.run_check(overrides={command: (0, output)})
                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn(message, result.stdout)
                self.assertIn("RESULT: FAIL", result.stdout)

    def test_unknown_responses_are_inconclusive(self) -> None:
        cases = [
            (PACKAGE_PATH, 1, "Package Manager unavailable\n"),
            (PACKAGE_PATH, 0, "Unexpected Package Manager output\n"),
            (INIT_STATE, 0, ""),
            (INIT_STATE, 1, "getprop unavailable\n"),
            (ENFORCING, 0, "unknown\n"),
            (ENFORCING, 1, "getenforce unavailable\n"),
            (BOOT_RECEIVERS, 1, "Unknown command: query-receivers\n"),
            (BOOT_RECEIVERS, 0, "Error: package query failed\n"),
            (BOOT_RECEIVERS, 0, ""),
            (BOOT_RECEIVERS, 0, f"{PACKAGE}/unrecognized.Receiver\n"),
            (ROOTLESS_PROCESS, 0, "USER PID NAME\nshell 42 shizuku_server\n"),
            (ROOTLESS_PROCESS, 0, ROOTLESS_ROWS.replace("2000 42", "shell 42")),
            (ROOTLESS_PROCESS, 0, ROOTLESS_ROWS.replace("u:r:shell:s0", "?")),
            (ROOTLESS_PROCESS, 1, "unsupported -o field\n"),
            (BINDER, 0, "[ERROR] Binding not well formed: s\n"),
            (BINDER, 0, "Error while accessing provider: BadParcelableException\n"),
            (BINDER, 0, "Error while reading Bundle[{value=parcel}]: BadParcelableException\n"),
            (BINDER, 0, "Result: Bundle[{}]\n"),
            (BINDER, 0, "Result: Bundle[EMPTY_PARCEL]\n"),
            (BINDER, 0, "Result: Bundle[{unrelated=value}]\n"),
            (BINDER, 0, "Result: Bundle[{moe.shizuku.privileged.api.intent.extra.BINDER=null}]\n"),
            (BINDER, 0, "Bundle[{moe.shizuku.privileged.api.intent.extra.BINDER=binder}]\n"),
            (BINDER, 0, "Result: Bundle[{moe.shizuku.privileged.api.intent.extra.BINDER=binder}]\nException\n"),
            (BINDER, 1, "provider unavailable\n"),
            (WIRELESS, 0, "null\n"),
            (WIRELESS, 0, ""),
            (WIRELESS, 0, "unexpected response\n0\n"),
            (WIRELESS, 1, "setting unavailable\n"),
        ]
        for command, status, output in cases:
            with self.subTest(command=command, status=status, output=output):
                result, _ = self.run_check(overrides={command: (status, output)})
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn("RESULT: INCONCLUSIVE", result.stdout)

    def test_failure_takes_precedence_over_inconclusive_check(self) -> None:
        result, _ = self.run_check(overrides={
            INIT_STATE: (0, "stopped\n"),
            WIRELESS: (0, "null\n"),
        })
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("1 required check(s) failed; 1 check(s) inconclusive", result.stdout)

    def test_android_carriage_returns_are_supported(self) -> None:
        result, _ = self.run_check(overrides={
            INIT_STATE: (0, "running\r\n"),
            ENFORCING: (0, "Enforcing\r\n"),
            BOOT_RECEIVERS: (0, "No receivers found\r\n"),
            ROOTLESS_PROCESS: (0, ROOTLESS_ROWS.replace("\n", "\r\n")),
            BINDER: (0, "Result: Bundle[{moe.shizuku.privileged.api.intent.extra.BINDER=rikka.shizuku.BinderContainer@1234}]\r\n"),
            WIRELESS: (0, "0\r\n"),
        })
        self.assertEqual(0, result.returncode, result.stdout)

    def test_magisk_rejects_a_shell_uid_server(self) -> None:
        result, _ = self.run_check("magisk", {
            MAGISK_PROCESS: (0, MAGISK_ROWS.replace("0 42", "2000 42")),
        })
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("expected UID 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
