# SPDX-License-Identifier: AGPL-3.0-or-later
"""Never change OEM unlocking or bootloader state."""
from ..common import manual
ID = "oemunlockonboot"
def check(ctx):
    return [ctx.file(ID, "image-payload", "/system/bin/oemunlockonboot.apk"),
            manual(ID, "init-task", "Do not infer or change OEM-unlock state; validate init execution separately")]
