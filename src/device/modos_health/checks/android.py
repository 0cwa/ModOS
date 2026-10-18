# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core boot and security-state observations."""
from ..common import item, manual

ID = "android"
def check(ctx):
    results = []
    completed = ctx.property("sys.boot_completed")
    results.append(item(ID, "boot-complete",
                        "PASS" if completed == "1" else
                        "FAIL" if completed == "0" else "WARNING",
                        "Boot completion confirmed" if completed == "1" else
                        "Android has not reported completed boot"))
    enforcing = ctx.probe("getenforce")
    value = enforcing.output.strip()
    results.append(item(ID, "selinux",
                        "PASS" if enforcing.returncode == 0 and value == "Enforcing"
                        else "FAIL" if enforcing.returncode == 0 and value == "Permissive"
                        else "WARNING",
                        "SELinux enforcing confirmed" if value == "Enforcing"
                        else "SELinux enforcing not confirmed"))
    device = ctx.property("ro.product.device")
    results.append(item(ID, "device",
                        "PASS" if device in ("pdx235", "XQ-DC72") else "WARNING",
                        "Expected Sony/LineageOS device identity" if
                        device in ("pdx235", "XQ-DC72") else
                        "Device property unavailable or unexpected"))
    locked = ctx.property("ro.boot.flash.locked")
    results.append(item(ID, "bootloader-lock",
                        "PASS" if locked == "1" else
                        "FAIL" if locked == "0" else "WARNING",
                        "Bootloader locked" if locked == "1" else
                        "Bootloader lock not confirmed"))
    verified = ctx.property("ro.boot.verifiedbootstate")
    results.append(item(ID, "verified-boot",
                        "PASS" if verified in ("yellow", "green") else
                        "FAIL" if verified == "orange" else "WARNING",
                        "Expected verified-boot state" if verified in
                        ("yellow", "green") else "Verified-boot state not confirmed"))
    slot = ctx.probe("bootctl get-current-slot")
    value = slot.output.strip()
    if slot.returncode == 0 and value in ("0", "1"):
        status = ctx.probe("bootctl is-slot-marked-successful " + value)
        results.append(item(ID, "successful-slot",
                            "PASS" if status.returncode == 0 and
                            status.output.strip() in ("1", "true") else "WARNING",
                            "Bootloader reports slot successful" if
                            status.returncode == 0 and status.output.strip()
                            in ("1", "true") else
                            "Could not confirm successful slot"))
    else:
        results.append(item(ID, "successful-slot", "WARNING",
                            "Boot-control slot information not available"))
    results.append(manual(ID, "verity-and-snapshots",
                          "Slot success does not prove dm-verity/snapuserd and post-update block verification; collect separate device evidence"))
    return results
