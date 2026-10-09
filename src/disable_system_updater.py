#!/usr/bin/env python3
"""Remove the stock ROM updater from a prepared Android filesystem.

This module is copied into the pinned my-avbroot-setup checkout at build time,
like the local boot-animation module. It intentionally targets only reviewed,
ROM-specific updater paths and performs a full metadata/tree preflight before
mutating the temporary unpacked filesystem.
"""

from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any


ROM_FAMILY_ENVIRONMENT = "PIXENEOS_ROM_FAMILY"

UPDATER_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "grapheneos": (
        "system",
        (
            "/system/priv-app/Updater/Updater.apk",
            "/system/etc/default-permissions/app.seamlessupdate.client.xml",
            "/system/etc/permissions/app.seamlessupdate.client.xml",
            "/system/etc/sysconfig/app.seamlessupdate.client.xml",
        ),
    ),
    "lineageos": (
        "system_ext",
        (
            "/system_ext/priv-app/Updater/Updater.apk",
            "/system_ext/etc/default-permissions/default-permissions_org.lineageos.updater.xml",
            "/system_ext/etc/permissions/org.lineageos.updater.xml",
        ),
    ),
}


class DisableSystemUpdaterError(RuntimeError):
    """Raised when the updater removal contract does not match the ROM."""


def target_for_family(rom_family: str) -> tuple[str, tuple[str, ...]]:
    try:
        return UPDATER_TARGETS[rom_family]
    except KeyError as exc:
        raise DisableSystemUpdaterError(
            f"unsupported ROM family for updater removal: {rom_family!r}"
        ) from exc


def _tree_path(fs: Any, path: PurePosixPath) -> Path:
    return fs.tree / path.relative_to("/")


def _metadata_entries(fs: Any, path: PurePosixPath) -> list[Any]:
    return [entry for entry in fs.info.entries if entry.path == path]


def remove_system_updater(rom_family: str, ext_fs: dict[str, Any]) -> tuple[str, ...]:
    """Remove the exact stock updater files for one reviewed ROM family."""

    partition, target_paths = target_for_family(rom_family)
    if partition not in ext_fs:
        raise DisableSystemUpdaterError(
            f"required updater partition is missing: {partition}"
        )

    fs = ext_fs[partition]
    planned: list[tuple[PurePosixPath, Any, Path]] = []

    for index, raw_path in enumerate(target_paths):
        path = PurePosixPath(raw_path)
        entries = _metadata_entries(fs, path)
        tree_path = _tree_path(fs, path)
        tree_exists = tree_path.exists() or tree_path.is_symlink()

        if not entries:
            if tree_exists:
                raise DisableSystemUpdaterError(
                    f"updater path exists only in unpacked tree: {path}"
                )
            if index == 0:
                raise DisableSystemUpdaterError(
                    f"expected stock updater APK is missing: {path}"
                )
            # Companion permission/sysconfig files may change between ROM
            # releases. Their absence is harmless once the APK is absent.
            continue

        if len(entries) != 1:
            raise DisableSystemUpdaterError(
                f"duplicate updater filesystem metadata: {path}"
            )

        entry = entries[0]
        if entry.file_type != "RegularFile":
            raise DisableSystemUpdaterError(
                f"updater target is not a regular file in metadata: {path}"
            )

        try:
            metadata = tree_path.lstat()
        except FileNotFoundError as exc:
            raise DisableSystemUpdaterError(
                f"updater path exists only in filesystem metadata: {path}"
            ) from exc

        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise DisableSystemUpdaterError(
                f"updater target is not a safe regular tree file: {path}"
            )

        planned.append((path, entry, tree_path))

    if not planned or planned[0][0] != PurePosixPath(target_paths[0]):
        raise DisableSystemUpdaterError("stock updater APK did not pass preflight")

    # The unpacked filesystem is disposable until the helper successfully
    # creates the output OTA. Preflight every target first; an unlink failure
    # aborts the build and no patched OTA is emitted.
    removed: list[str] = []
    for path, entry, tree_path in planned:
        tree_path.unlink()
        fs.info.entries.remove(entry)
        removed.append(str(path))

    return tuple(removed)


def _module_class() -> type[Any]:
    # Imported only after PixeneOS copies this module into the pinned helper.
    from lib.filesystem import CpioFs, ExtFs
    from lib.modules import LegacyCliModule, MissingArgs, ModuleRequirements

    class DisableSystemUpdaterMod(LegacyCliModule):
        NAME = "disable-system-updater"

        @classmethod
        def add_args(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument(
                f"--module-{cls.NAME}",
                type=Path,
            )
            parser.add_argument(
                f"--module-{cls.NAME}-sig",
                type=Path,
            )

        @classmethod
        def from_args(cls, args: argparse.Namespace) -> "DisableSystemUpdaterMod":
            marker = getattr(args, "module_disable_system_updater", None)
            if marker is None:
                raise MissingArgs()
            return cls()

        def requirements(self) -> ModuleRequirements:
            rom_family = os.environ.get(ROM_FAMILY_ENVIRONMENT, "")
            partition, _ = target_for_family(rom_family)
            return ModuleRequirements(
                boot_images=set(),
                ext_images={partition},
                selinux_patching=False,
            )

        def inject(
            self,
            boot_fs: dict[str, CpioFs],
            ext_fs: dict[str, ExtFs],
            sepolicies: Iterable[Path],
            compatible_sepolicy: bool = False,
        ) -> None:
            del boot_fs, sepolicies, compatible_sepolicy
            rom_family = os.environ.get(ROM_FAMILY_ENVIRONMENT, "")
            remove_system_updater(rom_family, ext_fs)

    return DisableSystemUpdaterMod


if __name__ != "__main__":
    try:
        DisableSystemUpdaterMod = _module_class()
    except ModuleNotFoundError as exc:
        # Unit tests import the removal helpers without the downloaded helper.
        if exc.name != "lib":
            raise
