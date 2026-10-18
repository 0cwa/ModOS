# SPDX-License-Identifier: AGPL-3.0-or-later
"""BCR package detection; never trigger or record a call."""
from ..common import manual
ID = "bcr"
def check(ctx):
    return [ctx.package(ID, "package", "com.chiller3.bcr"),
            manual(ID, "recording", "Confirm BCR permissions and a user-approved test recording in the app")]
