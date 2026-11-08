#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hermetic tests for per-module read-only health checks."""
from pathlib import Path
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "device"))
from modos_health.common import Context, MAX_PROBE_OUTPUT, STATUSES
from modos_health.checks import microg, msd

spec = importlib.util.spec_from_file_location(
    "check_pdx235_modules", ROOT / "src/device/check_pdx235_modules.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

GOOD_XML = """<permissions>
<privapp-permissions package="com.google.android.gms">
<deny-permission name="android.permission.DUMP"/>
<permission name="android.permission.FAKE_PACKAGE_SIGNATURE"/>
</privapp-permissions></permissions>"""
GADGET = "/vendor/bin/hw/android.hardware.usb.gadget-service.qti"
QTI_PROCESS = f"LABEL COMMAND\nu:r:hal_usb_qti:s0 {GADGET}\n"

class FakeADB:
    def __init__(self):
        self.calls = []
        self.overrides = {}
        self.boot_ids = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"] * 2

    def __call__(self, args, *, capture_output, text, timeout, check=False):
        self.calls.append(args)
        if args[-1] == "get-state":
            return subprocess.CompletedProcess(args, 0, "device\n", "")
        assert args[0] == "adb"
        rest = args[1:]
        if rest[0] == "-s":
            rest = rest[2:]
        assert len(rest) == 2 and rest[0] == "shell"
        cmd = rest[1]
        if cmd in self.overrides:
            code, out = self.overrides[cmd]
        elif cmd == "cat /proc/sys/kernel/random/boot_id":
            code, out = 0, self.boot_ids.pop(0) + "\n"
        elif cmd == "getenforce":
            code, out = 0, "Enforcing\n"
        elif cmd == "getprop sys.boot_completed":
            code, out = 0, "1\n"
        elif cmd == "getprop ro.product.device":
            code, out = 0, "pdx235\n"
        elif cmd == "getprop ro.boot.flash.locked":
            code, out = 0, "1\n"
        elif cmd == "getprop ro.boot.verifiedbootstate":
            code, out = 0, "yellow\n"
        elif cmd == "getprop init.svc.vendor.usbgadget-hal":
            code, out = 0, "running\n"
        elif cmd == "ps -A -o LABEL,ARGS":
            code, out = 0, QTI_PROCESS
        elif cmd == "bootctl get-current-slot":
            code, out = 0, "0\n"
        elif cmd == "bootctl is-slot-marked-successful 0":
            code, out = 0, "1\n"
        elif cmd == "pm list packages -d":
            code, out = 0, ""
        elif cmd == "pm path org.lineageos.updater":
            code, out = 0, ""
        elif cmd.startswith("pm path "):
            code, out = 0, "package:/system/priv-app/Fixture.apk\n"
        elif cmd.startswith("test -f /"):
            code, out = 0, ""
        elif cmd.startswith("cat /product/etc/permissions/"):
            code, out = 0, GOOD_XML
        else:
            raise AssertionError(f"Unexpected ADB command: {cmd}")
        return subprocess.CompletedProcess(args, code, out, "")

def fake_ctx(fake=None):
    mock = fake or FakeADB()
    return Context(["adb"], runner=mock), mock

class HealthAuditTests(unittest.TestCase):
    def test_build_selections_match_all_registry_modules(self):
        selections = audit.load_config(ROOT / ".github/schedules/lineageos-pdx235.toml")
        self.assertEqual(set(selections), {entry[0] for entry in audit.MODULES})
        self.assertTrue(selections["microg"])
        self.assertTrue(selections["custota"])
        self.assertFalse(selections["fdroid_privileged_extension"])

    def test_every_enabled_module_owns_results_and_disabled_is_not_failure(self):
        ctx, fake = fake_ctx()
        selected = audit.load_config(ROOT / ".github/schedules/lineageos-pdx235.toml")
        # Exercise MSD explicitly even when scheduled pdx235 builds disable it.
        selected["msd"] = True
        checks = audit.inspect(ctx, selected, "rootless")
        modules = {c["module"] for c in checks}
        self.assertEqual(modules,
                         {"android", "magisk"} | set(selected))
        self.assertTrue(all(c["status"] in STATUSES for c in checks))
        self.assertEqual(next(c["status"] for c in checks
                              if c["check"] == "privapp-dump"), "PASS")
        self.assertEqual(next(c["status"] for c in checks
                              if c["module"] == "fdroid_privileged_extension"),
                         "NOT_TESTED")
        self.assertEqual(next(c["status"] for c in checks
                              if c["module"] == "fsck_sysfs_sepolicy"),
                         "NOT_TESTED")
        self.assertEqual(next(c["check"] for c in checks
                              if c["module"] == "fsck_sysfs_sepolicy"),
                         "compiled-policy-allow-rule")
        self.assertFalse(any(c["status"] == "FAIL" for c in checks))
        self.assertEqual(next(c["status"] for c in checks
                              if c["check"] == "same-boot"), "PASS")
        self.assertTrue(any(c["status"] == "NOT_TESTED" for c in checks))
        calls = [" ".join(c) for c in fake.calls]
        self.assertFalse(any(" su " in c or "reboot" in c or
                             " shell pm grant " in c or "install " in c
                             for c in calls))
        self.assertEqual(calls.count("adb shell cat /proc/sys/kernel/random/boot_id"), 2)

    def test_msd_qti_observation_does_not_qualify_a_replacement_domain(self):
        ctx, fake = fake_ctx()
        checks = {c["check"]: c for c in msd.check(ctx)}
        self.assertEqual(checks["binary"]["status"], "PASS")
        self.assertEqual(checks["usb-gadget-service"]["status"], "PASS")
        self.assertEqual(checks["usb-gadget-domain"]["status"], "WARNING")
        self.assertIn("hal_usb_qti", checks["usb-gadget-domain"]["detail"])
        self.assertIn("requires policy and device qualification",
                      checks["usb-gadget-domain"]["detail"])
        self.assertEqual(checks["daemon"]["status"], "NOT_TESTED")
        self.assertEqual([call[-1] for call in fake.calls], [
            "test -f /system/bin/msd-tool", "getprop init.svc.vendor.usbgadget-hal",
            "ps -A -o LABEL,ARGS",
        ])

    def test_msd_target_domain_matches_for_known_aidl_and_hidl_service_names(self):
        for executable in (GADGET, "android.hardware.usb.gadget-service",
                           "/vendor/bin/hw/android.hardware.usb.gadget@1.2-service",
                           "/system/vendor/bin/hw/android.hardware.usb.gadget@1.1-service-qti"):
            with self.subTest(executable=executable):
                ctx, fake = fake_ctx()
                fake.overrides[msd._PROCESSES] = (
                    0, f"LABEL CMDLINE\nu:r:hal_usb_gadget_impl:s0 {executable}\n")
                checks = {c["check"]: c for c in msd.check(ctx)}
                self.assertEqual(checks["usb-gadget-domain"]["status"], "PASS")
                self.assertIn("process-label observation only",
                              checks["usb-gadget-domain"]["detail"])
                self.assertEqual(checks["daemon"]["status"], "NOT_TESTED")

    def test_msd_su_domain_is_a_failure(self):
        ctx, fake = fake_ctx()
        fake.overrides[msd._PROCESSES] = (0, f"LABEL COMMAND\nu:r:su:s0 {GADGET}\n")
        checks = {c["check"]: c for c in msd.check(ctx)}
        self.assertEqual(checks["usb-gadget-domain"]["status"], "FAIL")
        self.assertIn("su debug domain", checks["usb-gadget-domain"]["detail"])
        self.assertEqual(checks["daemon"]["status"], "NOT_TESTED")

    def test_msd_absent_unknown_or_unrelated_process_does_not_pass(self):
        for output in ("", "LABEL COMMAND\n",
                       "u:r:su:s0 /vendor/bin/hw/android.hardware.usb-service.qti\n",
                       "u:r:hal_usb_gadget_impl:s0 /vendor/bin/hw/android.hardware.usb.gadget-service.unknown\n",
                       "u:r:su:s0 /data/local/tmp/android.hardware.usb.gadget-service.qti\n",
                       "u:r:untrusted_app:s0 com.example.app android.hardware.usb.gadget-service.qti\n"):
            with self.subTest(output=output):
                ctx, fake = fake_ctx()
                fake.overrides[msd._PROCESSES] = (0, output)
                result = msd._gadget_domain(ctx)
                self.assertEqual(result["status"], "WARNING")
                self.assertIn("No recognized", result["detail"])

    def test_msd_multiple_and_duplicate_process_rows_are_ambiguous(self):
        for second in (f"u:r:hal_usb_qti:s0 {GADGET}\n",
                       "u:r:hal_usb_gadget_impl:s0 /vendor/bin/hw/android.hardware.usb.gadget-service\n"):
            with self.subTest(second=second):
                ctx, fake = fake_ctx()
                fake.overrides[msd._PROCESSES] = (0, QTI_PROCESS + second)
                result = msd._gadget_domain(ctx)
                self.assertEqual(result["status"], "WARNING")
                self.assertIn("ambiguous", result["detail"])

    def test_msd_unreadable_or_malformed_label_does_not_pass(self):
        for label in ("?", "u:object_r:hal_usb_gadget_impl:s0", "u:r:hal_usb_gadget_impl"):
            with self.subTest(label=label):
                ctx, fake = fake_ctx()
                fake.overrides[msd._PROCESSES] = (0, f"LABEL COMMAND\n{label} {GADGET}\n")
                result = msd._gadget_domain(ctx)
                self.assertEqual(result["status"], "WARNING")
                self.assertIn("unavailable or malformed", result["detail"])

    def test_msd_truncated_capture_never_establishes_unique_target(self):
        ctx, fake = fake_ctx()
        fake.overrides[msd._PROCESSES] = (
            0, f"LABEL COMMAND\nu:r:hal_usb_gadget_impl:s0 {GADGET}\n"
               + "u:r:untrusted_app:s0 private.app --secret=PRIVATE\n" * 1000)
        result = msd._gadget_domain(ctx)
        self.assertEqual(result["status"], "WARNING")
        self.assertIn("truncated", result["detail"])
        self.assertNotIn("PRIVATE", result["detail"])
        probe = ctx.probe(msd._PROCESSES)
        self.assertTrue(probe.truncated)
        self.assertEqual(len(probe.output), MAX_PROBE_OUTPUT)

    def test_msd_incomplete_final_process_row_is_not_accepted(self):
        ctx, fake = fake_ctx()
        fake.overrides[msd._PROCESSES] = (
            0, f"LABEL COMMAND\nu:r:hal_usb_gadget_impl:s0 {GADGET}")
        result = msd._gadget_domain(ctx)
        self.assertEqual(result["status"], "WARNING")
        self.assertIn("truncated", result["detail"])
        self.assertNotIn("Complete matching rows:", result["detail"])

    def test_msd_complete_su_observation_is_failure_even_when_later_rows_are_truncated(self):
        ctx, fake = fake_ctx()
        fake.overrides[msd._PROCESSES] = (
            0, f"LABEL COMMAND\nu:r:su:s0 {GADGET}\n" + "X" * MAX_PROBE_OUTPUT)
        self.assertEqual(msd._gadget_domain(ctx)["status"], "FAIL")

    def test_msd_unavailable_process_query_is_warning(self):
        for error in (OSError("private detail"),
                      subprocess.TimeoutExpired("ps", 20, output="private detail")):
            with self.subTest(error=type(error).__name__):
                ctx = Context(["adb"], runner=unittest.mock.Mock(side_effect=error))
                result = msd._gadget_domain(ctx)
                self.assertEqual(result["status"], "WARNING")
                self.assertNotIn("private detail", result["detail"])
        ctx, fake = fake_ctx()
        fake.overrides[msd._PROCESSES] = (1, "private permission-error output")
        result = msd._gadget_domain(ctx)
        self.assertEqual(result["status"], "WARNING")
        self.assertNotIn("permission-error", result["detail"])

    def test_msd_init_state_unavailable_or_not_running_is_warning(self):
        for code, output in ((0, "stopped\n"), (0, "restarting\n"), (0, ""),
                             (1, "private error"), (0, "running\nprivate detail"),
                             (0, "running" + " " * MAX_PROBE_OUTPUT)):
            with self.subTest(code=code, size=len(output)):
                ctx, fake = fake_ctx()
                fake.overrides[msd._SERVICE] = (code, output)
                result = msd._gadget_service(ctx)
                self.assertEqual(result["status"], "WARNING")
                self.assertNotIn("private", result["detail"])

    def test_microg_missing_deny_is_a_failure(self):
        fake = FakeADB()
        fake.overrides[microg._POLICY] = (
            0, '<permissions><privapp-permissions package="com.google.android.gms">'
            '<permission name="android.permission.FAKE_PACKAGE_SIGNATURE"/>'
            '</privapp-permissions></permissions>')
        checks = microg.check(fake_ctx(fake)[0])
        self.assertEqual(next(c["status"] for c in checks if
                              c["check"] == "privapp-dump"), "FAIL")

    def test_microg_conflicting_dump_grant_is_a_failure(self):
        fake = FakeADB()
        fake.overrides[microg._POLICY] = (
            0, GOOD_XML.replace('</privapp-permissions>',
                    '<permission name="android.permission.DUMP"/></privapp-permissions>'))
        checks = microg.check(fake_ctx(fake)[0])
        self.assertEqual(next(c["status"] for c in checks if
                              c["check"] == "privapp-dump"), "FAIL")

    def test_unavailable_policy_is_warning_not_asserted_failure(self):
        fake = FakeADB()
        fake.overrides[microg._POLICY] = (1, "")
        checks = microg.check(fake_ctx(fake)[0])
        self.assertEqual(next(c["status"] for c in checks if
                              c["check"] == "privapp-dump"), "WARNING")

    def test_failed_boot_completion_and_unlocked_state_are_detected(self):
        fake = FakeADB()
        fake.overrides["getprop sys.boot_completed"] = (0, "0\n")
        fake.overrides["getprop ro.boot.flash.locked"] = (0, "0\n")
        selections = {name: False for name, _, _ in audit.MODULES}
        checks = audit.inspect(fake_ctx(fake)[0], selections, "rootless")
        self.assertEqual(next(c["status"] for c in checks
                              if c["check"] == "boot-complete"), "FAIL")
        self.assertEqual(next(c["status"] for c in checks
                              if c["check"] == "bootloader-lock"), "FAIL")

    def test_changed_boot_does_not_claim_stable_observation(self):
        fake = FakeADB()
        fake.boot_ids = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                         "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
        checks = audit.inspect(fake_ctx(fake)[0],
                               {name: False for name, _, _ in audit.MODULES},
                               "rootless")
        self.assertEqual(next(c["status"] for c in checks
                              if c["check"] == "same-boot"), "WARNING")

    def test_disabled_app_not_reported_as_unhealthy(self):
        selections = {name: False for name, _, _ in audit.MODULES}
        ctx, fake = fake_ctx()
        checks = audit.inspect(ctx, selections, "rootless")
        self.assertEqual(sum(c["check"] == "disabled-in-build-config"
                             for c in checks), len(selections))
        self.assertFalse(any("pm path" in " ".join(x) for x in fake.calls))
        self.assertFalse(any(x[-1] in (msd._PROCESSES, msd._SERVICE) for x in fake.calls))

    def test_reject_commands_other_than_static_read_only_probes(self):
        ctx, _ = fake_ctx()
        for cmd in ("su -c id", "pm grant com.google.android.gms android.permission.DUMP",
                    "reboot", "rm -rf /data", "cat /data/system/accounts.db",
                    "getprop ro.x; reboot", "ps -A -o ARGS",
                    "ps -A -o LABEL,ARGS; reboot", "ps -A -o LABEL,ARGS -w"):
            with self.assertRaises(ValueError):
                ctx.probe(cmd)

    def test_report_contains_only_summaries_and_not_raw_xml(self):
        fake = FakeADB()
        fake.overrides[msd._PROCESSES] = (
            0, f"LABEL COMMAND\nu:r:hal_usb_qti:s0 {GADGET} --secret=PRIVATE_ARG\n"
               "u:r:untrusted_app:s0:c123,c456 private.account.app --token=PRIVATE_TOKEN\n")
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "private"
            config = Path(temp) / "msd-enabled.toml"
            original = (ROOT / ".github/schedules/lineageos-pdx235.toml").read_text()
            explicit, count = re.subn(r"('ADDITIONALS\[MSD\]'\s*=\s*)(?:true|false)\b",
                                      r"\g<1>true", original)
            self.assertEqual(count, 1)
            config.write_text(explicit)
            with patch("subprocess.run", side_effect=fake):
                rc = audit.main(["--config", str(config), "--serial", "PRIVATE_SERIAL",
                                 "--output", str(dest)])
            self.assertEqual(rc, 0)
            report = json.loads((dest / "report.json").read_text())
            self.assertEqual(report["device_acceptance"], "not_established")
            self.assertEqual(report["schema_version"], 1)
            self.assertGreater(report["counts"]["PASS"], 5)
            self.assertEqual(report["counts"]["FAIL"], 0)
            self.assertNotIn("serial", report)
            self.assertNotIn("android.permission.DUMP", (dest / "report.json").read_text())
            self.assertEqual(next(c["status"] for c in report["checks"]
                                  if c["check"] == "usb-gadget-domain"), "WARNING")
            for filename in ("report.json", "summary.txt"):
                saved = (dest / filename).read_text()
                for private in ("PRIVATE_SERIAL", "PRIVATE_ARG", "PRIVATE_TOKEN",
                                "private.account.app", "LABEL COMMAND", "c123,c456"):
                    self.assertNotIn(private, saved)
                self.assertIn("hal_usb_qti", saved)
            self.assertIn("Device acceptance: NOT ESTABLISHED",
                          (dest / "summary.txt").read_text())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((dest / "report.json").stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE((dest / "summary.txt").stat().st_mode), 0o600)

    def test_new_module_requires_its_own_registered_check_file(self):
        original = (ROOT / ".github/schedules/lineageos-pdx235.toml").read_text()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "future.toml"
            path.write_text(original + "\n'ADDITIONALS[FUTURE_MODULE]' = true\n")
            with self.assertRaisesRegex(ValueError, "unregistered build module"):
                audit.load_config(path)

    def test_invalid_config_rejected_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.toml"
            path.write_text('[device]\nDEVICE_NAME="other"\nROM_FAMILY="lineageos"\n'
                            '[build]\n')
            with self.assertRaises(ValueError):
                audit.load_config(path)

if __name__ == "__main__":
    unittest.main()
