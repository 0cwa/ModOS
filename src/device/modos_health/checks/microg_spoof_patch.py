# SPDX-License-Identifier: AGPL-3.0-or-later
"""microG Companion signer patch: Companion present, stale services AOT removed."""
from ..common import item, manual

ID = "microg_spoof_patch"
# The patch replaces /system/framework/services.jar and removes the stock
# precompiled artifacts, which no longer match it. Their presence means the
# patched jar is not the one on the system image.
STALE_AOT = tuple(f"/system/framework/oat/arm64/services.{ext}"
                  for ext in ("odex", "vdex", "art"))

def check(ctx):
    results = [ctx.package(ID, "companion", "com.android.vending")]
    for path in STALE_AOT:
        probe = ctx.probe("test -f " + path)
        name = "stale-" + path.rsplit(".", 1)[1]
        if probe.returncode == 1:
            results.append(item(ID, name, "PASS",
                                "Stock services precompiled artifact is absent"))
        elif probe.returncode == 0:
            results.append(item(ID, name, "FAIL",
                                "Stock services precompiled artifact is still present; "
                                "the signer patch does not appear to be applied"))
        else:
            results.append(item(ID, name, "WARNING",
                                "Could not confirm the stock services artifact is absent"))
    results.append(manual(ID, "spoofing",
                          "Confirm the ModOS-signed Companion keeps restricted signature "
                          "spoofing and microG's self-check passes on device"))
    return results
