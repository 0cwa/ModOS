#!/usr/bin/env python3
"""Grant fsck_untrusted read access to sysfs directories in compiled policy."""

from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any


PATCHER_ENVIRONMENT = "PIXENEOS_FSCK_SYSFS_POLICY_PATCHER"


class FsckSysfsSePolicyError(RuntimeError):
    """Raised when the policy patcher or compiled policies are unavailable."""


def _module_class() -> type[Any]:
    # Imported only after this adapter is copied into the pinned helper.
    from lib.filesystem import CpioFs, ExtFs
    from lib.modules import LegacyCliModule, MissingArgs, ModuleRequirements
    from lib.linux import linux_run

    class FsckSysfsSePolicyMod(LegacyCliModule):
        NAME = "fsck-sysfs-sepolicy"

        @classmethod
        def add_args(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument(f"--module-{cls.NAME}", type=Path)
            parser.add_argument(f"--module-{cls.NAME}-sig", type=Path)

        @classmethod
        def from_args(cls, args: argparse.Namespace) -> "FsckSysfsSePolicyMod":
            if getattr(args, "module_fsck_sysfs_sepolicy", None) is None:
                raise MissingArgs()
            return cls()

        def requirements(self) -> ModuleRequirements:
            if (
                os.environ.get("PIXENEOS_ROM_FAMILY", "") != "lineageos"
                or os.environ.get("PIXENEOS_DEVICE_NAME", "") != "pdx235"
            ):
                raise FsckSysfsSePolicyError(
                    "fsck sysfs policy patch is supported only for LineageOS pdx235"
                )
            return ModuleRequirements(
                boot_images=set(),
                ext_images=set(),
                selinux_patching=True,
            )

        def inject(
            self,
            boot_fs: dict[str, CpioFs],
            ext_fs: dict[str, ExtFs],
            sepolicies: Iterable[Path],
            compatible_sepolicy: bool = False,
        ) -> None:
            del boot_fs, ext_fs, compatible_sepolicy
            patcher_value = os.environ.get(PATCHER_ENVIRONMENT, "")
            if not patcher_value:
                raise FsckSysfsSePolicyError("compiled-policy patcher path is not configured")
            patcher = Path(patcher_value)
            try:
                metadata = patcher.lstat()
            except OSError as exc:
                raise FsckSysfsSePolicyError("compiled-policy patcher is unavailable") from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or not metadata.st_mode & 0o111
            ):
                raise FsckSysfsSePolicyError("compiled-policy patcher is not a safe executable")

            policies = tuple(sepolicies)
            if not policies:
                raise FsckSysfsSePolicyError("compiled SELinux policy files are unavailable")
            for policy in policies:
                try:
                    policy_metadata = policy.lstat()
                except OSError as exc:
                    raise FsckSysfsSePolicyError("compiled SELinux policy is unavailable") from exc
                if stat.S_ISLNK(policy_metadata.st_mode) or not stat.S_ISREG(policy_metadata.st_mode):
                    raise FsckSysfsSePolicyError("compiled SELinux policy is not a regular file")
                linux_run([patcher, policy], inputs=[patcher, policy], outputs=[policy])

    return FsckSysfsSePolicyMod


if __name__ != "__main__":
    try:
        from lib.filesystem import ExtFs  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "lib":
            raise
    else:
        FsckSysfsSePolicyMod = _module_class()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print("fsck sysfs SELinux policy changes are applied by the image helper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
