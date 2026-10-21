#!/usr/bin/env python3
"""Inject a vold built from the exact pinned LineageOS pdx235 source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from typing import Any, Iterable


MODULE_NAME = "lineage-vold-exfat-timeout"
BASE_BUILD_ID = "lineage-24.0-20261016-nightly-pdx235-signed"
MANIFEST_SHA256 = "d4ded1ca84de399497b68d7ac006808059b6be9cf76725029420a3495a2e5468"
VOLD_REVISION = "11d8982f824d52e8122c7aa732207ba66b8f23c8"
DEVICE_REVISION = "0559de69466e1e9af69b121985f592c4f4609283"
PATCH_SHA256 = "53129785ccea28d828b08766bddaaf594d5602ba819cb8e7abd281b9d5724a90"
ARTIFACT_DIR_ENV = "PIXENEOS_VOLD_ARTIFACT_DIR"
BASE_BUILD_ENV = "PIXENEOS_VOLD_BASE_BUILD_ID"
TARGET = PurePosixPath("/system/bin/vold")
MAX_BINARY_SIZE = 32 * 1024 * 1024


class VoldModuleError(ValueError):
    """The source-built vold or target OTA does not meet the pinned contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_target_profile(rom_family: str, device: str) -> None:
    if rom_family != "lineageos" or device != "pdx235":
        raise VoldModuleError("source-built vold is supported only for LineageOS pdx235")


def _read_regular_file(path: Path, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise VoldModuleError(f"required vold input is unavailable: {path.name}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > maximum:
        raise VoldModuleError(f"required vold input is not a bounded regular file: {path.name}")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or opened.st_size != metadata.st_size
        ):
            raise VoldModuleError(f"vold input changed before it was read: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            data = stream.read(maximum + 1)
    except OSError as exc:
        raise VoldModuleError(f"required vold input cannot be read: {path.name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) != metadata.st_size or len(data) > maximum:
        raise VoldModuleError(f"required vold input has an unexpected size: {path.name}")
    return data


def _elf_identity(binary: bytes) -> dict[str, Any]:
    if len(binary) < 64 or len(binary) > MAX_BINARY_SIZE:
        raise VoldModuleError("vold ELF has an invalid size")
    if binary[:4] != b"\x7fELF" or binary[4:7] != b"\x02\x01\x01":
        raise VoldModuleError("vold is not a little-endian ELF64 executable")
    try:
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", binary)
    except struct.error as exc:
        raise VoldModuleError("vold ELF header is truncated") from exc
    _, elf_type, machine, version, _, phoff, _, _, ehsize, phentsize, phnum, _, _, _ = header
    if elf_type != 3 or machine != 183 or version != 1:
        raise VoldModuleError("vold is not an AArch64 position-independent executable")
    if ehsize != 64 or phentsize != 56 or not 0 < phnum <= 128:
        raise VoldModuleError("vold ELF program-header shape is unsupported")
    if phoff + phentsize * phnum > len(binary):
        raise VoldModuleError("vold ELF program headers are truncated")

    interpreter: str | None = None
    dynamic: tuple[int, int] | None = None
    loads: list[tuple[int, int, int]] = []
    for index in range(phnum):
        try:
            p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(
                "<IIQQQQQQ", binary, phoff + index * phentsize
            )
        except struct.error as exc:
            raise VoldModuleError("vold ELF program header is malformed") from exc
        if p_offset + p_filesz > len(binary):
            raise VoldModuleError("vold ELF segment exceeds the file")
        if p_type == 1:
            loads.append((p_offset, p_vaddr, p_filesz))
        elif p_type == 2:
            if dynamic is not None:
                raise VoldModuleError("vold ELF has multiple dynamic segments")
            dynamic = (p_offset, p_filesz)
        elif p_type == 3:
            if interpreter is not None:
                raise VoldModuleError("vold ELF has multiple interpreters")
            value = binary[p_offset:p_offset + p_filesz]
            if not value.endswith(b"\0") or b"\0" in value[:-1]:
                raise VoldModuleError("vold ELF interpreter is malformed")
            interpreter = value[:-1].decode("ascii", errors="strict")
    if interpreter != "/system/bin/linker64" or dynamic is None or not loads:
        raise VoldModuleError("vold ELF interpreter or dynamic segment is unsupported")

    entries: list[tuple[int, int]] = []
    for offset in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        if offset + 16 > len(binary):
            raise VoldModuleError("vold ELF dynamic table is malformed")
        tag, value = struct.unpack_from("<qQ", binary, offset)
        entries.append((tag, value))
        if tag == 0:
            break
    if not entries or entries[-1][0] != 0:
        raise VoldModuleError("vold ELF dynamic table lacks a terminator")
    strtab_address = next((value for tag, value in entries if tag == 5), None)
    strtab_size = next((value for tag, value in entries if tag == 10), None)
    if not isinstance(strtab_address, int) or not isinstance(strtab_size, int):
        raise VoldModuleError("vold ELF lacks a dynamic string table")
    strtab_offset = None
    for file_offset, virtual_address, file_size in loads:
        if virtual_address <= strtab_address < virtual_address + file_size:
            strtab_offset = file_offset + strtab_address - virtual_address
            break
    if strtab_offset is None or strtab_size <= 0 or strtab_offset + strtab_size > len(binary):
        raise VoldModuleError("vold ELF dynamic string table is out of bounds")

    def string_at(index: int) -> str:
        if index >= strtab_size:
            raise VoldModuleError("vold ELF dynamic string index is out of bounds")
        start = strtab_offset + index
        end = binary.find(b"\0", start, strtab_offset + strtab_size)
        if end < 0:
            raise VoldModuleError("vold ELF dynamic string is unterminated")
        return binary[start:end].decode("ascii", errors="strict")

    needed = tuple(string_at(value) for tag, value in entries if tag == 1)
    if not needed or len(needed) != len(set(needed)):
        raise VoldModuleError("vold ELF dependency list is empty or duplicated")
    return {
        "elf_class": 64,
        "machine": "AArch64",
        "interpreter": interpreter,
        "needed": needed,
    }


def _artifact(artifact_dir: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        metadata = artifact_dir.lstat()
    except OSError as exc:
        raise VoldModuleError("source-built vold artifact directory is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise VoldModuleError("source-built vold artifact path is not a safe directory")
    binary_path = artifact_dir / "vold"
    provenance_path = artifact_dir / "provenance.json"
    binary = _read_regular_file(binary_path, MAX_BINARY_SIZE)
    provenance_bytes = _read_regular_file(provenance_path, 128 * 1024)
    try:
        provenance = json.loads(provenance_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoldModuleError("source-built vold provenance is invalid JSON") from exc
    expected = {
        "schema_version": 1,
        "module": MODULE_NAME,
        "base_build_id": BASE_BUILD_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "vold_revision": VOLD_REVISION,
        "device_revision": DEVICE_REVISION,
        "patch_sha256": PATCH_SHA256,
        "build_target": "lineage_pdx235-userdebug",
        "build_command": "m vold",
        "artifact_path": "out/target/product/pdx235/system/bin/vold",
    }
    if not isinstance(provenance, dict) or any(provenance.get(key) != value for key, value in expected.items()):
        raise VoldModuleError("source-built vold provenance does not match the pinned source contract")
    if provenance.get("binary_sha256") != _sha256(binary):
        raise VoldModuleError("source-built vold failed its provenance SHA-256 check")
    identity = _elf_identity(binary)
    if provenance.get("elf") != {
        "elf_class": identity["elf_class"],
        "machine": identity["machine"],
        "interpreter": identity["interpreter"],
        "needed": list(identity["needed"]),
    }:
        raise VoldModuleError("source-built vold ELF metadata differs from its provenance")
    return binary, provenance


def _build_properties(system_fs: Any) -> dict[str, str]:
    path = system_fs.tree / "system" / "build.prop"
    data = _read_regular_file(path, 2 * 1024 * 1024)
    properties: dict[str, str] = {}
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise VoldModuleError("target system build properties are not UTF-8") from exc
    for line in lines:
        if not line or line.startswith(("#", "!")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def _metadata_snapshot(entry: Any) -> tuple[Any, ...]:
    """The pinned ExtFs entry must survive a binary overwrite unchanged."""
    return (
        entry.source,
        entry.file_type,
        entry.file_mode,
        entry.uid,
        entry.gid,
        entry.atime,
        entry.ctime,
        entry.mtime,
        entry.crtime,
        entry.device_major,
        entry.device_minor,
        entry.symlink_target,
        dict(entry.xattrs),
    )


def inject_vold(system_fs: Any, artifact_dir: Path, base_build_id: str) -> str:
    if base_build_id != BASE_BUILD_ID:
        raise VoldModuleError("selected OTA build does not match the pinned vold source build")
    binary, _ = _artifact(artifact_dir)
    properties = _build_properties(system_fs)
    # Modern Android partitions normally advertise ro.product.system.device,
    # whereas ro.product.device is resolved by init from several partitions.
    device_values = [
        properties[key]
        for key in ("ro.product.system.device", "ro.product.device", "ro.build.product")
        if key in properties
    ]
    if not device_values or any(value != "pdx235" for value in device_values):
        raise VoldModuleError("target system image does not identify as pdx235")
    if (
        properties.get("ro.lineage.build.version") != "24.0"
        or properties.get("ro.lineage.version") != "24.0-20261016-NIGHTLY-pdx235"
    ):
        raise VoldModuleError("target system image does not match the pinned 20261016 LineageOS build")

    entries = [entry for entry in system_fs.info.entries if entry.path == TARGET]
    if len(entries) != 1 or entries[0].file_type != "RegularFile":
        raise VoldModuleError("target /system/bin/vold filesystem metadata is missing or not regular")
    entry = entries[0]
    if not isinstance(entry.file_mode, int) or not entry.file_mode & 0o111:
        raise VoldModuleError("target /system/bin/vold is not executable in filesystem metadata")
    if entry.uid not in (None, 0) or entry.gid not in (None, 0):
        raise VoldModuleError("target /system/bin/vold must be root-owned")
    if not isinstance(entry.xattrs, dict):
        raise VoldModuleError("target /system/bin/vold lacks usable SELinux metadata")
    label = entry.xattrs.get("security.selinux")
    if not isinstance(label, str) or label.rstrip("\0") != "u:object_r:vold_exec:s0":
        raise VoldModuleError("target /system/bin/vold has an unexpected SELinux xattr")

    labels = [label for pattern, label in system_fs.contexts if pattern.fullmatch(str(TARGET))]
    if not labels or any(label != "u:object_r:vold_exec:s0" for label in labels):
        raise VoldModuleError("target /system/bin/vold lacks the expected context mapping")

    # ExtFs may represent a root owner as None, which gets packed back as 0.
    # Make that convention explicit before taking the metadata snapshot.
    if entry.uid is None:
        entry.uid = 0
    if entry.gid is None:
        entry.gid = 0

    target_path = system_fs.tree / TARGET.relative_to("/")
    for parent in (system_fs.tree, system_fs.tree / "system", system_fs.tree / "system" / "bin"):
        try:
            mode = parent.lstat().st_mode
        except OSError as exc:
            raise VoldModuleError("target /system/bin/vold parent directory is unavailable") from exc
        if not stat.S_ISDIR(mode):
            raise VoldModuleError("target /system/bin/vold parent directory is unsafe")
    old_binary = _read_regular_file(target_path, MAX_BINARY_SIZE)
    if _elf_identity(old_binary) != _elf_identity(binary):
        raise VoldModuleError("source-built vold ELF ABI or dependencies differ from the OTA vold")

    before = _metadata_snapshot(entry)
    try:
        with system_fs.open(str(TARGET), "wb", mode=entry.file_mode) as stream:
            stream.write(binary)
    except (OSError, ValueError) as exc:
        raise VoldModuleError("target /system/bin/vold could not be replaced") from exc
    if _metadata_snapshot(entry) != before:
        raise VoldModuleError("vold injection changed filesystem entry metadata")
    if _read_regular_file(target_path, MAX_BINARY_SIZE) != binary:
        raise VoldModuleError("injected vold differs from the verified source-built binary")
    if len([entry for entry in system_fs.info.entries if entry.path == TARGET]) != 1:
        raise VoldModuleError("vold injection unexpectedly changed filesystem metadata entries")
    return _sha256(binary)


def _module_class() -> type[Any]:
    from lib.filesystem import CpioFs, ExtFs
    from lib.modules import LegacyCliModule, MissingArgs, ModuleRequirements

    class LineageVoldMod(LegacyCliModule):
        NAME = MODULE_NAME

        @classmethod
        def add_args(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument(f"--module-{cls.NAME}", type=Path)
            parser.add_argument(f"--module-{cls.NAME}-sig", type=Path)

        @classmethod
        def from_args(cls, args: argparse.Namespace) -> "LineageVoldMod":
            if getattr(args, "module_lineage_vold_exfat_timeout", None) is None:
                raise MissingArgs()
            return cls()

        def requirements(self) -> ModuleRequirements:
            _require_target_profile(
                os.environ.get("PIXENEOS_ROM_FAMILY", ""),
                os.environ.get("PIXENEOS_DEVICE_NAME", ""),
            )
            return ModuleRequirements(boot_images=set(), ext_images={"system"}, selinux_patching=False)

        def inject(
            self,
            boot_fs: dict[str, CpioFs],
            ext_fs: dict[str, ExtFs],
            sepolicies: Iterable[Path],
            compatible_sepolicy: bool = False,
        ) -> None:
            del boot_fs, sepolicies, compatible_sepolicy
            _require_target_profile(
                os.environ.get("PIXENEOS_ROM_FAMILY", ""),
                os.environ.get("PIXENEOS_DEVICE_NAME", ""),
            )
            artifact_value = os.environ.get(ARTIFACT_DIR_ENV, "")
            if not artifact_value:
                raise VoldModuleError("source-built vold artifact directory is not configured")
            if "system" not in ext_fs:
                raise VoldModuleError("system image is unavailable for vold injection")
            inject_vold(
                ext_fs["system"],
                Path(artifact_value),
                os.environ.get(BASE_BUILD_ENV, ""),
            )

    return LineageVoldMod


if __name__ != "__main__":
    try:
        LineageVoldMod = _module_class()
    except ModuleNotFoundError as exc:
        if exc.name != "lib":
            raise
