# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only device check for the optional source-built LineageOS vold.

Presence of /system/bin/vold alone does not demonstrate that our modified
binary was installed, that it boots safely or that its timeout was applied.
"""

from ..common import Context, item

ID = "lineage_vold"


def check(ctx: Context):
    return [
        ctx.file(ID, "vold-executable-present", "/system/bin/vold"),
        item(
            ID,
            "patch-identity-and-runtime-fsck",
            "NOT_TESTED",
            "ADB file presence cannot attest the installed vold digest, source "
            "provenance, exFAT timeout, device encryption or mount behavior",
        ),
    ]
