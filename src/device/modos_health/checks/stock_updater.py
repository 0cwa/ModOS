# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify stock LineageOS updater removal without uninstalling any package."""
from ..common import item
ID = "stock_updater"
def check(ctx):
    result = ctx.probe("pm path org.lineageos.updater")
    if result.returncode != 0:
        return [item(ID, "removed", "WARNING", "Stock-updater package query unavailable")]
    return [item(ID, "removed", "FAIL" if "package:/" in result.output else "PASS",
                 "Stock updater still installed" if "package:/" in result.output else
                 "Known LineageOS updater package not installed (other updater implementations not assessed)")]
