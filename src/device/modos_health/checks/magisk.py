# SPDX-License-Identifier: AGPL-3.0-or-later
"""Root is never invoked by the health auditor."""
from ..common import manual
ID = "magisk"
def check(ctx):
    return [manual(ID, "root-runtime",
                   "Magisk manager and root bootstrap remain intentionally untested; audit never runs su")]
