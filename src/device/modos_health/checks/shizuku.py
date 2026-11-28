# SPDX-License-Identifier: AGPL-3.0-or-later
"""Observe the preinstalled manager and the shared init service."""
from ..common import item, manual

ID = "shizuku"


def check(ctx):
    service = ctx.property("init.svc.shizuku")
    if service == "running":
        startup = item(ID, "init-service", "PASS", "Android init reports Shizuku running")
    elif service in {"stopped", "restarting", "stopping"}:
        startup = item(ID, "init-service", "FAIL", "Shizuku init service is " + service)
    else:
        startup = item(ID, "init-service", "WARNING", "Shizuku init state is unavailable")
    return [
        ctx.package(ID, "manager-package", "moe.shizuku.privileged.api"),
        ctx.file(ID, "init-config", "/product/etc/init/shizuku.rc"),
        startup,
        manual(ID, "cold-boot-and-client", "Verify startup with debugging off and normal client approval"),
    ]
