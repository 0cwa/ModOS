# SPDX-License-Identifier: AGPL-3.0-or-later
"""AlterInstaller is an init-time tool, deliberately not a registered APK."""
from ..common import manual
ID = "alterinstaller"
def check(ctx):
    return [ctx.file(ID, "image-payload", "/system/bin/alterinstaller.apk"),
            manual(ID, "init-task", "Init-time package adjustments require separate boot-time evidence; audit never invokes the installer")]
