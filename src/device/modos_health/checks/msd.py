# SPDX-License-Identifier: AGPL-3.0-or-later
"""MSD image tool presence, without invoking its daemon or sepolicy commands."""
from ..common import manual
ID = "msd"
def check(ctx):
    return [ctx.file(ID, "binary", "/system/bin/msd-tool"),
            manual(ID, "daemon", "Inspect MSD app/daemon behavior and SELinux AVC logs privately; image presence alone does not prove daemon health")]
