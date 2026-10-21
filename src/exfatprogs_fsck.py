#!/usr/bin/env python3
"""Opt-in replacement of the target system image's exFAT checker."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tarfile
from typing import Any, Iterable

MODULE_NAME = "exfatprogs-fsck"
TARGET_ROM_FAMILY = "lineageos"
TARGET_DEVICE = "pdx235"
ALLOWED_OUTPUT_SCOPES = {"local-unpublished", "published"}
TARGET_PATH = PurePosixPath("/system/bin/fsck.exfat")
PACKAGE_NAME = "exfatprogs"
PACKAGE_VERSION = "1.4.3"
PACKAGE_ARCH = "aarch64"
PACKAGE_SHA256 = "b177a2254245170dac3dab200e972e8dc7ac06777713d033da8e1516e32685e9"
PACKAGE_SIZE = 89540
PACKAGE_MEMBER = "./data/data/com.termux/files/usr/bin/fsck.exfat"
PACKAGE_BINARY_SHA256 = "6d88dd967484e80a7d8c5b4930109b8f3d9f46118a45d2071f5aa446859a0b93"
SYSTEM_BINARY_SHA256 = "06e018cc175e9702508b6a76e66486e28125fef434a0a5c0f7611f3cd3ad147e"
TERMUX_RUNPATH = "/data/data/com.termux/files/usr/lib"
SYSTEM_RUNPATH = "/system/lib64"
ANDROID_INTERPRETER = "/system/bin/linker64"
MAX_AR_MEMBER_SIZE = 1024 * 1024
MAX_ELF_SIZE = 512 * 1024


class ExfatFsckError(ValueError):
    """The locked package or target filesystem entry is unsafe or unexpected."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_package(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ExfatFsckError("locked exfatprogs package is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != PACKAGE_SIZE:
        raise ExfatFsckError("locked exfatprogs package is not the expected regular file")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or opened.st_size != PACKAGE_SIZE
        ):
            raise ExfatFsckError("locked exfatprogs package changed before it was opened")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            data = stream.read(PACKAGE_SIZE + 1)
    except OSError as exc:
        raise ExfatFsckError("locked exfatprogs package cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) != PACKAGE_SIZE or _sha256(data) != PACKAGE_SHA256:
        raise ExfatFsckError("locked exfatprogs package failed its size or SHA-256 check")
    return data


def _deb_data_tar(package: bytes) -> bytes:
    if not package.startswith(b"!<arch>\n"):
        raise ExfatFsckError("package is not a Debian ar archive")
    offset = 8
    data_tar: bytes | None = None
    saw_debian_binary = False
    while offset < len(package):
        header_end = offset + 60
        if header_end > len(package):
            raise ExfatFsckError("Debian package has a truncated ar header")
        header = package[offset:header_end]
        if header[58:60] != b"`\n":
            raise ExfatFsckError("Debian package has an invalid ar member header")
        try:
            member_size = int(header[48:58].decode("ascii").strip())
            member_name = header[:16].decode("ascii").strip().rstrip("/")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ExfatFsckError("Debian package has invalid ar member metadata") from exc
        if member_size < 0 or member_size > MAX_AR_MEMBER_SIZE:
            raise ExfatFsckError("Debian package ar member exceeds its size limit")
        member_start = header_end
        member_end = member_start + member_size
        if member_end > len(package):
            raise ExfatFsckError("Debian package has a truncated ar member")
        member_data = package[member_start:member_end]
        if member_name == "debian-binary":
            if saw_debian_binary or member_data != b"2.0\n":
                raise ExfatFsckError("Debian package format marker is unexpected")
            saw_debian_binary = True
        elif member_name == "data.tar.xz":
            if data_tar is not None:
                raise ExfatFsckError("Debian package contains duplicate data archives")
            data_tar = member_data
        offset = member_end + (member_size & 1)
    if not saw_debian_binary or data_tar is None:
        raise ExfatFsckError("Debian package lacks its expected data archive")
    return data_tar


def _extract_expected_binary(package: bytes) -> bytes:
    archive_bytes = _deb_data_tar(package)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:xz") as archive:
            matches = [item for item in archive.getmembers() if item.name == PACKAGE_MEMBER]
            if len(matches) != 1:
                raise ExfatFsckError("package does not contain exactly one fsck.exfat binary")
            member = matches[0]
            if not member.isfile() or member.size <= 0 or member.size > MAX_ELF_SIZE:
                raise ExfatFsckError("package fsck.exfat member is not a bounded regular file")
            stream = archive.extractfile(member)
            if stream is None:
                raise ExfatFsckError("package fsck.exfat member cannot be read")
            with stream:
                binary = stream.read(MAX_ELF_SIZE + 1)
    except (OSError, EOFError, lzma.LZMAError, tarfile.TarError) as exc:
        raise ExfatFsckError("package data archive is corrupt or unsupported") from exc
    if len(binary) != member.size or len(binary) > MAX_ELF_SIZE:
        raise ExfatFsckError("package fsck.exfat member has an unexpected size")
    if _sha256(binary) != PACKAGE_BINARY_SHA256:
        raise ExfatFsckError("package fsck.exfat member failed its SHA-256 check")
    return binary


def _map_vaddr(loads: list[tuple[int, int, int]], address: int) -> int:
    for file_offset, virtual_address, file_size in loads:
        if virtual_address <= address < virtual_address + file_size:
            return file_offset + address - virtual_address
    raise ExfatFsckError("ELF virtual address is outside file-backed load segments")


def _elf_details(binary: bytes) -> dict[str, Any]:
    if len(binary) > MAX_ELF_SIZE or len(binary) < 64:
        raise ExfatFsckError("fsck.exfat ELF has an invalid size")
    ident = binary[:16]
    if ident[:4] != b"\x7fELF" or ident[4] != 2 or ident[5] != 1 or ident[6] != 1:
        raise ExfatFsckError("fsck.exfat is not a little-endian ELF64 executable")
    try:
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", binary)
    except struct.error as exc:
        raise ExfatFsckError("fsck.exfat ELF header is truncated") from exc
    _, elf_type, machine, version, _, phoff, _, _, ehsize, phentsize, phnum, _, _, _ = header
    if elf_type != 3 or machine != 183 or version != 1:
        raise ExfatFsckError("fsck.exfat is not an AArch64 position-independent executable")
    if ehsize != 64 or phentsize != 56 or not 0 < phnum <= 64:
        raise ExfatFsckError("fsck.exfat ELF program-header shape is unsupported")
    if phoff + phentsize * phnum > len(binary):
        raise ExfatFsckError("fsck.exfat ELF program headers are truncated")

    loads: list[tuple[int, int, int]] = []
    dynamic: tuple[int, int] | None = None
    interpreter: str | None = None
    notes: list[tuple[int, int]] = []
    for index in range(phnum):
        try:
            p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(
                "<IIQQQQQQ", binary, phoff + index * phentsize
            )
        except struct.error as exc:
            raise ExfatFsckError("fsck.exfat ELF program header is malformed") from exc
        if p_offset + p_filesz > len(binary):
            raise ExfatFsckError("fsck.exfat ELF segment exceeds the file")
        if p_type == 1:
            loads.append((p_offset, p_vaddr, p_filesz))
        elif p_type == 2:
            if dynamic is not None:
                raise ExfatFsckError("fsck.exfat ELF has multiple dynamic segments")
            dynamic = (p_offset, p_filesz)
        elif p_type == 3:
            if interpreter is not None:
                raise ExfatFsckError("fsck.exfat ELF has multiple interpreters")
            interpreter_bytes = binary[p_offset:p_offset + p_filesz]
            if not interpreter_bytes.endswith(b"\0") or b"\0" in interpreter_bytes[:-1]:
                raise ExfatFsckError("fsck.exfat ELF interpreter is malformed")
            interpreter = interpreter_bytes[:-1].decode("ascii", errors="strict")
        elif p_type == 4:
            notes.append((p_offset, p_filesz))
    if interpreter != ANDROID_INTERPRETER or dynamic is None or not loads:
        raise ExfatFsckError("fsck.exfat ELF interpreter or dynamic segment is unsupported")

    dynamic_entries: list[tuple[int, int, int]] = []
    for offset in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        if offset + 16 > len(binary):
            raise ExfatFsckError("fsck.exfat ELF dynamic table is malformed")
        tag, value = struct.unpack_from("<qQ", binary, offset)
        dynamic_entries.append((offset, tag, value))
        if tag == 0:
            break
    if not dynamic_entries or dynamic_entries[-1][1] != 0:
        raise ExfatFsckError("fsck.exfat ELF dynamic table lacks a terminator")
    strtab_address = next((value for _, tag, value in dynamic_entries if tag == 5), None)
    strtab_size = next((value for _, tag, value in dynamic_entries if tag == 10), None)
    if not isinstance(strtab_address, int) or not isinstance(strtab_size, int):
        raise ExfatFsckError("fsck.exfat ELF lacks a dynamic string table")
    strtab_offset = _map_vaddr(loads, strtab_address)
    if strtab_size <= 0 or strtab_offset + strtab_size > len(binary):
        raise ExfatFsckError("fsck.exfat ELF dynamic string table is out of bounds")

    def get_string(index: int) -> str:
        if index >= strtab_size:
            raise ExfatFsckError("fsck.exfat ELF dynamic string index is out of bounds")
        start = strtab_offset + index
        end = binary.find(b"\0", start, strtab_offset + strtab_size)
        if end < 0:
            raise ExfatFsckError("fsck.exfat ELF dynamic string is unterminated")
        try:
            return binary[start:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ExfatFsckError("fsck.exfat ELF dynamic string is not ASCII") from exc

    needed = [get_string(value) for _, tag, value in dynamic_entries if tag == 1]
    runpaths = [(offset, value, get_string(value)) for offset, tag, value in dynamic_entries if tag == 29]
    rpaths = [get_string(value) for _, tag, value in dynamic_entries if tag == 15]
    android_api: int | None = None
    for note_offset, note_size in notes:
        cursor = note_offset
        limit = note_offset + note_size
        while cursor + 12 <= limit:
            name_size, desc_size, note_type = struct.unpack_from("<III", binary, cursor)
            cursor += 12
            name_start = cursor
            name_end = name_start + name_size
            desc_start = name_start + ((name_size + 3) & ~3)
            desc_end = desc_start + desc_size
            next_note = desc_start + ((desc_size + 3) & ~3)
            if name_end > limit or desc_end > limit or next_note > limit:
                raise ExfatFsckError("fsck.exfat ELF note segment is malformed")
            name = binary[name_start:name_end].rstrip(b"\0")
            if name == b"Android" and note_type == 1 and desc_size >= 4:
                android_api = struct.unpack_from("<I", binary, desc_start)[0]
            cursor = next_note
    if android_api != 24:
        raise ExfatFsckError("fsck.exfat was not built for the locked Android API 24 minimum")
    if needed != ["libc.so"] or rpaths or len(runpaths) != 1:
        raise ExfatFsckError("fsck.exfat ELF dependencies or search paths are unexpected")
    return {
        "interpreter": interpreter,
        "needed": needed,
        "runpath": runpaths[0][2],
        "runpath_dynamic_offset": runpaths[0][0],
        "runpath_string_index": runpaths[0][1],
        "string_table_offset": strtab_offset,
        "string_table_size": strtab_size,
        "android_api": android_api,
    }


def _system_image_binary(package_path: Path) -> bytes:
    package = _read_package(package_path)
    original = _extract_expected_binary(package)
    details = _elf_details(original)
    if details["runpath"] != TERMUX_RUNPATH:
        raise ExfatFsckError("package fsck.exfat has an unexpected Termux RUNPATH")
    offset = details["string_table_offset"] + details["runpath_string_index"]
    old = TERMUX_RUNPATH.encode("ascii") + b"\0"
    replacement = SYSTEM_RUNPATH.encode("ascii") + b"\0"
    if len(replacement) > len(old):
        raise ExfatFsckError("system RUNPATH does not fit the package's ELF string slot")
    mutable = bytearray(original)
    mutable[offset:offset + len(old)] = replacement + bytes(len(old) - len(replacement))
    result = bytes(mutable)
    final = _elf_details(result)
    if final["runpath"] != SYSTEM_RUNPATH or _sha256(result) != SYSTEM_BINARY_SHA256:
        raise ExfatFsckError("transformed fsck.exfat did not match its reviewed ELF identity")
    return result


def _metadata_snapshot(entry: Any) -> tuple[Any, ...]:
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


def _inject_system_binary(system_fs: Any, binary: bytes) -> None:
    entries = [entry for entry in system_fs.info.entries if entry.path == TARGET_PATH]
    if len(entries) != 1:
        raise ExfatFsckError("system image does not contain exactly one fsck.exfat entry")
    target_entry = entries[0]
    if (
        target_entry.file_type != "RegularFile"
        or target_entry.file_mode is None
        or target_entry.file_mode & 0o111 == 0
        or target_entry.uid is None
        or target_entry.gid is None
        or not target_entry.xattrs.get("security.selinux")
    ):
        raise ExfatFsckError("system image fsck.exfat metadata is incomplete or unexpected")

    tree_path = system_fs.tree / "system" / "bin" / "fsck.exfat"
    for path in (system_fs.tree, system_fs.tree / "system", system_fs.tree / "system" / "bin"):
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ExfatFsckError("system image fsck.exfat parent path is unavailable") from exc
        if not stat.S_ISDIR(mode):
            raise ExfatFsckError("system image fsck.exfat parent path is not a real directory")
    try:
        if not stat.S_ISREG(tree_path.lstat().st_mode):
            raise ExfatFsckError("system image fsck.exfat tree entry is not a regular file")
    except OSError as exc:
        raise ExfatFsckError("system image fsck.exfat tree entry is unavailable") from exc

    before = _metadata_snapshot(target_entry)
    try:
        with system_fs.open(str(TARGET_PATH), "wb", mode=target_entry.file_mode) as output:
            output.write(binary)
    except (OSError, ValueError) as exc:
        raise ExfatFsckError("system image fsck.exfat could not be replaced") from exc
    if _metadata_snapshot(target_entry) != before:
        raise ExfatFsckError("system image fsck.exfat metadata changed during replacement")
    try:
        metadata = tree_path.lstat()
        digest = hashlib.sha256()
        with tree_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ExfatFsckError("system image replacement could not be verified") from exc
    if not stat.S_ISREG(metadata.st_mode) or digest.hexdigest() != SYSTEM_BINARY_SHA256:
        raise ExfatFsckError("system image fsck.exfat replacement has an unexpected type or digest")


def _require_target_profile(rom_family: str, device: str) -> None:
    if rom_family != TARGET_ROM_FAMILY or device != TARGET_DEVICE:
        raise ExfatFsckError("exfatprogs fsck is supported only for LineageOS pdx235")
    scope = os.environ.get("PIXENEOS_OUTPUT_SCOPE", "")
    if scope not in ALLOWED_OUTPUT_SCOPES:
        raise ExfatFsckError(
            "exfatprogs fsck requires local-unpublished or source-verified published output"
        )
    if scope == "published":
        path = Path(os.environ.get("EXFAT_SOURCE_BUNDLE_PATH", ""))
        expected = os.environ.get("EXFAT_SOURCE_BUNDLE_SHA256", "")
        if (os.environ.get("EXFAT_SOURCE_BUNDLE_VERIFIED") != "true" or
                not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise ExfatFsckError("published exfatprogs output requires its verified source bundle")
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
                raise ExfatFsckError("published exfatprogs source bundle is not a regular file")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ExfatFsckError("published exfatprogs source bundle cannot be read") from exc
        if digest.hexdigest() != expected:
            raise ExfatFsckError("published exfatprogs source bundle digest does not match verification")


def _module_class() -> type[Any]:
    from lib.filesystem import CpioFs, ExtFs
    from lib.modules import LegacyCliModule, MissingArgs, ModuleRequirements

    class ExfatprogsFsckMod(LegacyCliModule):
        NAME = MODULE_NAME

        @classmethod
        def add_args(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument(f"--module-{cls.NAME}", type=Path)
            parser.add_argument(f"--module-{cls.NAME}-sig", type=Path)

        @classmethod
        def from_args(cls, args: argparse.Namespace) -> "ExfatprogsFsckMod":
            if getattr(args, "module_exfatprogs_fsck", None) is None:
                raise MissingArgs()
            return cls()

        def requirements(self) -> ModuleRequirements:
            _require_target_profile(
                os.environ.get("PIXENEOS_ROM_FAMILY", ""),
                os.environ.get("PIXENEOS_DEVICE_NAME", ""),
            )
            return ModuleRequirements(
                boot_images=set(),
                ext_images={"system"},
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
            _require_target_profile(
                os.environ.get("PIXENEOS_ROM_FAMILY", ""),
                os.environ.get("PIXENEOS_DEVICE_NAME", ""),
            )
            package_path = os.environ.get("PIXENEOS_EXFATPROGS_PACKAGE_PATH", "")
            if not package_path:
                raise ExfatFsckError("locked exfatprogs package path is not configured")
            system_fs = ext_fs.get("system")
            if system_fs is None:
                raise ExfatFsckError("system image is unavailable for the exFAT checker")
            _inject_system_binary(system_fs, _system_image_binary(Path(package_path)))

    return ExfatprogsFsckMod


def _verify_package_command(package_path: Path) -> dict[str, Any]:
    binary = _system_image_binary(package_path)
    info = _elf_details(binary)
    return {
        "package_sha256": PACKAGE_SHA256,
        "package_binary_sha256": PACKAGE_BINARY_SHA256,
        "system_binary_sha256": _sha256(binary),
        "elf_class": 64,
        "machine": "AArch64",
        "interpreter": info["interpreter"],
        "needed": info["needed"],
        "runpath": info["runpath"],
        "android_api": info["android_api"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify-package"])
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(_verify_package_command(args.package), sort_keys=True))
    except ExfatFsckError as exc:
        parser.error(str(exc))
else:
    try:
        from lib.filesystem import ExtFs  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "lib":
            raise
    else:
        ExfatprogsFsckMod = _module_class()
