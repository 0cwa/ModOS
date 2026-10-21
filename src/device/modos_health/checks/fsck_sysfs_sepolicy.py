# SPDX-License-Identifier: AGPL-3.0-or-later
"""Avoid claiming runtime verification of the fsck sysfs allow rule."""
from ..common import item

ID = "fsck_sysfs_sepolicy"


def check(_ctx):
    return [item(ID, "compiled-policy-allow-rule", "NOT_TESTED",
                 "The runtime audit cannot inspect the compiled SELinux allow rule")]
