# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional F-Droid privileged extension."""
from ..common import manual
ID = "fdroid_privileged_extension"
def check(ctx):
    return [ctx.package(ID, "package", "org.fdroid.fdroid.privileged"),
            manual(ID, "permissions", "Validate privileged extension permission and actual installs separately")]
