#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only ADB health audit: per-module checks aggregated into one report.

This does not flash, install, grant permissions, invoke root, collect full
device logs, or attest that a given OTA was installed. Device acceptance is
always a separate decision.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tomllib

from modos_health.common import Context, item
from modos_health.checks import (afsr, alterinstaller, android, bcr,
                                 boot_animation, custota, fdroid_privileged_extension,
                                 exfatprogs_fsck, fsck_sysfs_sepolicy, lineage_vold,
                                 magisk, microg,
                                 msd, oemunlockonboot, stock_updater)

# Static registry: every enabled module has exactly one owning check file.
# Keys refer to the checked-in scheduled TOML settings, not arbitrary caller
# input and not any installer script present on the device.
MODULES = (
    ("afsr", "ADDITIONALS[AFSR]", afsr),
    ("alterinstaller", "ADDITIONALS[ALTERINSTALLER]", alterinstaller),
    ("bcr", "ADDITIONALS[BCR]", bcr),
    ("custota", "ADDITIONALS[CUSTOTA]", custota),
    ("msd", "ADDITIONALS[MSD]", msd),
    ("oemunlockonboot", "ADDITIONALS[OEMUNLOCKONBOOT]", oemunlockonboot),
    ("microg", "ADDITIONALS[MICROG]", microg),
    ("boot_animation", "ADDITIONALS[BOOT_ANIMATION]", boot_animation),
    ("stock_updater", "ADDITIONALS[DISABLE_SYSTEM_UPDATER]", stock_updater),
    ("exfatprogs_fsck", "ADDITIONALS[EXFATPROGS_FSCK]", exfatprogs_fsck),
    ("fsck_sysfs_sepolicy", "ADDITIONALS[FSCK_SYSFS_SEPOLICY]", fsck_sysfs_sepolicy),
    ("lineage_vold", "ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]", lineage_vold),
    ("fdroid_privileged_extension", "ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]",
     fdroid_privileged_extension),
)

def load_config(path):
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    build = data.get("build")
    device = data.get("device")
    if not isinstance(build, dict) or not isinstance(device, dict):
        raise ValueError("expected [build] and [device] TOML tables")
    if device.get("DEVICE_NAME") != "pdx235" or device.get("ROM_FAMILY") != "lineageos":
        raise ValueError("audit is restricted to LineageOS pdx235")
    # Do not silently omit a newly enabled module introduced to the build
    # schedule without adding its own check file to the registry.
    known = {key for _, key, _ in MODULES}
    ignored = {"ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]"}
    unknown = sorted(k for k in build if
                     k.startswith("ADDITIONALS[") and k.endswith("]") and
                     k not in known and k not in ignored)
    if unknown:
        raise ValueError("unregistered build module(s): " + ", ".join(unknown))
    selections = {}
    for name, key, _ in MODULES:
        value = build.get(key)
        if type(value) is not bool:
            raise ValueError("required module selection must be a TOML boolean: " + key)
        selections[name] = value
    return selections

def inspect(ctx, selections, root_mode):
    results = []
    start = ctx.probe("cat /proc/sys/kernel/random/boot_id", refresh=True)
    results.extend(android.check(ctx))
    for name, _, module in MODULES:
        if selections[name]:
            try:
                checks = module.check(ctx)
                if not checks or any(c["module"] != name for c in checks):
                    raise ValueError("module returned invalid check identity")
                results.extend(checks)
            except (ValueError, KeyError, TypeError):
                results.append(item(name, "probe-error", "WARNING",
                                    "Module check could not be completed"))
        else:
            results.append(item(name, "disabled-in-build-config", "NOT_TESTED",
                                "Module disabled in selected build configuration"))
    if root_mode == "magisk":
        results.extend(magisk.check(ctx))
    else:
        results.append(item("magisk", "root-runtime", "NOT_TESTED",
                            "Rootless runtime selected; no Magisk or su operation attempted"))
    end = ctx.probe("cat /proc/sys/kernel/random/boot_id", refresh=True)
    if start.returncode == 0 and end.returncode == 0 and start.output.strip() == end.output.strip():
        results.append(item("android", "same-boot", "PASS",
                            "Boot identity remained stable during audit"))
    else:
        results.append(item("android", "same-boot", "WARNING",
                            "Cannot confirm a single stable boot throughout audit"))
    return results

def format_summary(report):
    lines = [
        "ModOS pdx235 read-only module health audit",
        "Automatic result: " + report["automatic_result"],
        "Device acceptance: NOT ESTABLISHED",
        "Runtime mode: " + report["root_mode"],
        "Checks: " + ", ".join(f"{s}={report['counts'][s]}" for s in
                            ("PASS", "FAIL", "WARNING", "NOT_TESTED")),
        "",
    ]
    for check in report["checks"]:
        lines.append(f"[{check['status']}] {check['module']}.{check['check']}: {check['detail']}")
    lines += [
        "",
        "A PASS only confirms an explicitly observed static or package condition.",
        "It never proves end-to-end module functionality, an OTA installation,",
        "signature-spoofing restriction, dm-verity verification or root operation.",
        "No full logs, account identifiers or raw package dumps are saved.",
    ]
    return "\n".join(lines) + "\n"

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="Optional already authorized ADB device serial")
    parser.add_argument("--config", type=Path,
                        default=Path(".github/schedules/lineageos-pdx235.toml"),
                        help="Checked-in build selection TOML (read only)")
    parser.add_argument("--root-mode", choices=("rootless", "magisk"),
                        default="rootless",
                        help="Installed device flavor; defaults to the tested rootless path")
    parser.add_argument("--output", type=Path, required=True,
                        help="New private directory containing report.json and summary.txt")
    args = parser.parse_args(argv)
    try:
        selections = load_config(args.config)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        parser.error(f"invalid build selection: {error}")
    adb = ["adb"] + (["-s", args.serial] if args.serial else [])
    try:
        state = subprocess.run(adb + ["get-state"], capture_output=True,
                               text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        parser.error("ADB is unavailable; connect an already authorized device")
    if state.returncode or state.stdout.strip() != "device":
        parser.error("Exactly one already-authorized device is required; audit will not authorize ADB")
    try:
        args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as error:
        parser.error(f"cannot create a new private output directory: {error}")
    ctx = Context(adb)
    checks = inspect(ctx, selections, args.root_mode)
    counts = Counter(check["status"] for check in checks)
    report = {
        "schema_version": 1,
        "type": "read-only-module-health",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "device": "pdx235",
        "root_mode": args.root_mode,
        "automatic_result": "FAIL" if counts["FAIL"] else
                             "WARNING" if counts["WARNING"] else "PASS",
        "device_acceptance": "not_established",
        "counts": {key: counts[key] for key in
                   ("PASS", "FAIL", "WARNING", "NOT_TESTED")},
        "checks": checks,
        "limitations": [
            "No claim of Android 17 first-boot, installed OTA artifact or A/B OTA persistence",
            "Not-tested functional checks need user action and an explicit assessment",
            "Privileged permission XML cannot replace analysis of all system_server crash logs",
            "This auditor never grants permissions, invokes su, starts daemons, or alters the device",
        ],
    }
    with (args.output / "report.json").open("x", encoding="utf-8") as stream:
        (args.output / "report.json").chmod(0o600)
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    with (args.output / "summary.txt").open("x", encoding="utf-8") as stream:
        (args.output / "summary.txt").chmod(0o600)
        stream.write(format_summary(report))
    print(format_summary(report))
    print("Saved private reports to " + str(args.output))
    return 1 if counts["FAIL"] else 0

if __name__ == "__main__":
    sys.exit(main())
