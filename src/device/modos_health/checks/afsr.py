# SPDX-License-Identifier: AGPL-3.0-or-later
"""AFSR is a build/repack tool, not an on-device background service."""
from ..common import manual
ID = "afsr"
def check(ctx):
    return [manual(ID, "build-time-only",
                   "AFSR operates during image preparation; validate its signed tool and output filesystem in CI, not through ADB")]
