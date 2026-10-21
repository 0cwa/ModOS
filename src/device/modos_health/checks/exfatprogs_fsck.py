# SPDX-License-Identifier: AGPL-3.0-or-later
"""Confirm the selected image contains its exFAT filesystem checker."""
from ..common import Context, item

ID = "exfatprogs_fsck"


def check(ctx: Context):
    return [ctx.file(ID, "fsck-exfat-binary", "/system/bin/fsck.exfat")]
