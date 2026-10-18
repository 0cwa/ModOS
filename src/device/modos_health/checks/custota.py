# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installed updater is not itself proof of network/update readiness."""
from ..common import manual
ID = "custota"

def check(ctx):
    return [
        ctx.package(ID, "package", "com.chiller3.custota"),
        manual(ID, "update-cycle",
               "Confirm XQ-DC72.json rootless feed, certificate checks, install, reboot and next OTA via Custota CHECK logs"),
    ]
