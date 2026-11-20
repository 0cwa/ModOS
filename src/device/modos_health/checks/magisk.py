# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep manager setup, root authorization and runtime checks explicitly manual."""
from ..common import manual
ID = "magisk"

def check(ctx):
    return [
        manual(ID, "manager-setup",
               "Install the official manager matching the OTA as a user app; complete "
               "runtime Additional setup / Environment fix if requested and reboot. "
               "A full reinstall/reflash request needs a reviewed signed OTA, never "
               "Direct install or Install to inactive slot. Magisk v31.1 does not "
               "install a missing manager automatically"),
        manual(ID, "root-runtime",
               "For Magisk v31.1, grant Shell (com.android.shell, UID 2000) in "
               "Superuser > + / Grant Superuser before manually running adb shell su -c id; "
               "expect uid=0(root). Reactive permission prompts were removed. "
               "The audit never grants access or runs su"),
        manual(ID, "root-persistence",
               "Confirm the installed Magisk version matches the OTA and repeat the "
               "manual root check after reboot and the next ModOS OTA update"),
    ]
