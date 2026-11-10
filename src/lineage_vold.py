#!/usr/bin/env python3
"""Give LineageOS vold a 120-second exFAT fsck limit by patching one instruction.

Stock vold runs ``fsck.exfat`` with the shared 45-second untrusted-fsck limit,
which large exFAT volumes can exceed. ``exfat::Check`` passes that limit to
``ForkExecvpTimeout`` as a seconds argument, which the compiler materialised as
``mov w1, #45``. This module changes that one immediate to 120 in the
``/system/bin/vold`` that is already in the OTA. ``vfat::Check`` and every other
caller keep the 45-second limit.

Nothing is rebuilt: every other byte is exactly what LineageOS shipped, so the
linker, ABI, dependencies and SELinux label cannot drift. The call site is
located through the symbols in the binary's ``.gnu_debugdata`` section (the
standard compressed mini-debuginfo of Android builds), not through a fixed file
offset, so it applies to any LineageOS build whose vold has the same structure.
It fails closed unless exactly one such call site is found.

Only the Python standard library is used, so the module can run inside the
pinned helper checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import lzma
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
from typing import Any, Iterable


MODULE_NAME = "lineage-vold-exfat-timeout"
TARGET = PurePosixPath("/system/bin/vold")
OLD_SECONDS = 45
NEW_SECONDS = 120
MAX_BINARY_SIZE = 32 * 1024 * 1024
MAX_DEBUGDATA_SIZE = 64 * 1024 * 1024
EXPECTED_LABEL = "u:object_r:vold_exec:s0"

EXFAT_CHECK_PREFIX = "_ZN7android4vold5exfat5CheckE"
FORK_EXEC_PREFIX = "_ZN7android4vold17ForkExecvpTimeoutE"
EXFAT_CHECK_SYMBOL = (
    EXFAT_CHECK_PREFIX
    + "RKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE"
)
FORK_EXEC_SYMBOL = (
    FORK_EXEC_PREFIX
    + "RKNSt3__16vectorINS1_12basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEENS6_IS8_EEEE"
    + "NS1_6chrono8durationIxNS1_5ratioILl1ELl1EEEEEPc"
)

# How many instructions after `mov w1, #45` may precede the `bl` to
# ForkExecvpTimeout. The compiler schedules the argument setup in between.
CALL_WINDOW = 8


class VoldModuleError(ValueError):
    """vold does not have the structure this patch can safely edit."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mov_w1(seconds: int) -> int:
    """Encode ``movz w1, #seconds`` (AArch64, 32-bit, no shift)."""
    if not 0 <= seconds <= 0xFFFF:
        raise VoldModuleError("immediate does not fit a movz instruction")
    return 0x52800000 | (seconds << 5) | 1


def _is_bl(word: int) -> bool:
    return word & 0xFC000000 == 0x94000000


def _bl_target(address: int, word: int) -> int:
    imm = word & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return address + imm * 4


def _is_other_branch(word: int) -> bool:
    """A control transfer other than BL, which ends the argument-setup window."""
    return (
        word & 0xFC000000 == 0x14000000  # b
        or word & 0xFF000010 == 0x54000000  # b.cond
        or word & 0x7E000000 == 0x34000000  # cbz / cbnz
        or word & 0x7E000000 == 0x36000000  # tbz / tbnz
        or word & 0xFFFFFC1F in (0xD61F0000, 0xD65F0000, 0xD63F0000)  # br / ret / blr
    )


def _is_safe_argument_setup(word: int) -> bool:
    """Accept only decoded instructions that neither read nor write x1/w1.

    Branch proximity alone is not a data-flow proof: a later instruction can
    overwrite the timeout or copy it into another argument. Keep this whitelist
    deliberately small. A new compiler sequence must be reviewed before it is
    allowed, even if its branch target and symbol names have stayed the same.
    """
    rd = word & 31
    rn = (word >> 5) & 31
    rm = (word >> 16) & 31
    if word == 0xD503201F:  # nop
        return True
    if word & 0x7FE0FFE0 == 0x2A0003E0:  # mov Wd,Wm / mov Xd,Xm (orr with ZR)
        return rd != 1 and rm != 1
    if word & 0x7F800000 in (0x12800000, 0x52800000, 0x72800000):  # movn/z/k
        valid_shift = bool(word & 0x80000000) or (word >> 21) & 3 < 2
        return valid_shift and rd != 1
    if word & 0x7F800000 in (0x11000000, 0x51000000):  # add/sub immediate, no flags
        return rd != 1 and rn != 1
    if word & 0x1F000000 == 0x10000000:  # adr / adrp
        return rd != 1
    if word & 0xFFC00000 in (0xB9000000, 0xF9000000, 0xB9400000, 0xF9400000):
        # str/ldr Wt/Xt,[Xn,#imm], without writeback. Stores must not read x1
        # either as their value or as the address base.
        return rd != 1 and rn != 1
    return False


class _Elf:
    """The few ELF64 little-endian facts this patch needs, parsed defensively."""

    def __init__(self, data: bytes, limit: int):
        self.data = data
        if len(data) < 64 or len(data) > limit:
            raise VoldModuleError("ELF has an invalid size")
        if data[:4] != b"\x7fELF" or data[4:7] != b"\x02\x01\x01":
            raise VoldModuleError("not a little-endian ELF64 file")
        (
            _,
            self.type,
            self.machine,
            version,
            _,
            self.phoff,
            self.shoff,
            _,
            ehsize,
            self.phentsize,
            self.phnum,
            self.shentsize,
            self.shnum,
            self.shstrndx,
        ) = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
        if self.machine != 183 or version != 1 or ehsize != 64:
            raise VoldModuleError("ELF is not an AArch64 object")
        if self.phnum and (
            self.phentsize != 56 or self.phnum > 128
            or self.phoff + self.phentsize * self.phnum > len(data)
        ):
            raise VoldModuleError("ELF program headers are invalid or truncated")
        if self.shentsize != 64 or not 0 < self.shnum <= 4096:
            raise VoldModuleError("ELF section-header shape is unsupported")
        if self.shoff + self.shentsize * self.shnum > len(data):
            raise VoldModuleError("ELF section headers are truncated")
        self.sections = [self._section(i) for i in range(self.shnum)]
        if self.shstrndx >= self.shnum:
            raise VoldModuleError("ELF section-name table is missing")
        names = self.section_bytes(self.sections[self.shstrndx])
        for section in self.sections:
            section["name"] = self._cstring(names, section["name_offset"])

    def _section(self, index: int) -> dict[str, Any]:
        (name, kind, _, addr, offset, size, link, _, _, _) = struct.unpack_from(
            "<IIQQQQIIQQ", self.data, self.shoff + index * self.shentsize
        )
        return {
            "name_offset": name,
            "type": kind,
            "addr": addr,
            "offset": offset,
            "size": size,
            "link": link,
        }

    @staticmethod
    def _cstring(table: bytes, index: int) -> str:
        if index >= len(table):
            raise VoldModuleError("ELF string index is out of bounds")
        end = table.find(b"\0", index)
        if end < 0:
            raise VoldModuleError("ELF string is unterminated")
        try:
            return table[index:end].decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise VoldModuleError("ELF string is not ASCII") from exc

    def section_bytes(self, section: dict[str, Any]) -> bytes:
        if section["type"] == 8:  # NOBITS
            return b""
        end = section["offset"] + section["size"]
        if end > len(self.data):
            raise VoldModuleError("ELF section exceeds the file")
        return self.data[section["offset"]:end]

    def section(self, name: str) -> dict[str, Any] | None:
        found = [s for s in self.sections if s["name"] == name]
        if len(found) > 1:
            raise VoldModuleError(f"ELF has duplicate {name} sections")
        return found[0] if found else None

    def symbols(self) -> list[tuple[str, int, int]]:
        symtab = self.section(".symtab")
        if symtab is None:
            raise VoldModuleError("debug data has no symbol table")
        if symtab["link"] >= self.shnum:
            raise VoldModuleError("symbol table has an invalid string table")
        strtab = self.section_bytes(self.sections[symtab["link"]])
        raw = self.section_bytes(symtab)
        result = []
        for offset in range(0, len(raw) - 23, 24):
            name, info, _, _, value, size = struct.unpack_from("<IBBHQQ", raw, offset)
            if info & 0xF == 2 and size:  # STT_FUNC
                result.append((self._cstring(strtab, name), value, size))
        return result

    def file_offset(self, address: int, length: int) -> int:
        """Map a virtual address range to a file offset through PT_LOAD."""
        if self.phentsize != 56 or not 0 < self.phnum <= 128:
            raise VoldModuleError("ELF program-header shape is unsupported")
        if self.phoff + self.phentsize * self.phnum > len(self.data):
            raise VoldModuleError("ELF program headers are truncated")
        for index in range(self.phnum):
            kind, flags, offset, vaddr, _, filesz, _, _ = struct.unpack_from(
                "<IIQQQQQQ", self.data, self.phoff + index * self.phentsize
            )
            if kind == 1 and vaddr <= address and address + length <= vaddr + filesz:
                if not flags & 1:
                    raise VoldModuleError("the vold call site is not in an executable segment")
                result = offset + address - vaddr
                if result + length > len(self.data):
                    raise VoldModuleError("vold segment exceeds the file")
                return result
        raise VoldModuleError("the vold call site is outside every loadable segment")

    def interpreter(self) -> str:
        interpreters = []
        for index in range(self.phnum):
            kind, _, offset, _, _, filesz, _, _ = struct.unpack_from(
                "<IIQQQQQQ", self.data, self.phoff + index * self.phentsize
            )
            if kind == 3:
                value = self.data[offset:offset + filesz]
                if not value.endswith(b"\0") or b"\0" in value[:-1]:
                    raise VoldModuleError("ELF interpreter is malformed")
                interpreters.append(value[:-1].decode("ascii", errors="strict"))
        if len(interpreters) != 1:
            raise VoldModuleError("ELF does not have exactly one interpreter")
        return interpreters[0]


def _load(binary: bytes) -> _Elf:
    elf = _Elf(binary, MAX_BINARY_SIZE)
    if elf.type != 3:
        raise VoldModuleError("vold is not a position-independent executable")
    if elf.interpreter() != "/system/bin/linker64":
        raise VoldModuleError("vold does not use the Android 64-bit linker")
    return elf


def _only_symbol(symbols: list[tuple[str, int, int]], prefix: str, expected: str) -> tuple[int, int]:
    found = [(name, value, size) for name, value, size in symbols if name.startswith(prefix)]
    if len(found) != 1:
        raise VoldModuleError(f"expected exactly one {prefix} symbol, found {len(found)}")
    name, value, size = found[0]
    if name != expected:
        raise VoldModuleError(f"unsupported function signature for {prefix}")
    return value, size


def find_call_sites(binary: bytes, seconds: int) -> list[int]:
    """File offsets of ``mov w1, #seconds`` that feed ForkExecvpTimeout inside exfat::Check."""
    elf = _load(binary)
    debug = elf.section(".gnu_debugdata")
    if debug is None:
        raise VoldModuleError("vold has no .gnu_debugdata symbols to locate exfat::Check")
    try:
        decompressor = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
        inner = decompressor.decompress(elf.section_bytes(debug), MAX_DEBUGDATA_SIZE + 1)
    except lzma.LZMAError as exc:
        raise VoldModuleError("vold .gnu_debugdata is not valid XZ data") from exc
    if len(inner) > MAX_DEBUGDATA_SIZE or not decompressor.eof:
        raise VoldModuleError("vold .gnu_debugdata has an unexpected size")
    symbols = _Elf(inner, MAX_DEBUGDATA_SIZE).symbols()
    check_address, check_size = _only_symbol(symbols, EXFAT_CHECK_PREFIX, EXFAT_CHECK_SYMBOL)
    fork_address, fork_size = _only_symbol(symbols, FORK_EXEC_PREFIX, FORK_EXEC_SYMBOL)
    if check_address % 4 or check_size % 4 or not 0 < check_size <= 16 * 1024:
        raise VoldModuleError("exfat::Check has an unexpected size")
    if fork_address % 4 or fork_size % 4:
        raise VoldModuleError("ForkExecvpTimeout has an unexpected alignment")
    elf.file_offset(fork_address, fork_size)
    if not (fork_address + fork_size <= check_address or check_address + check_size <= fork_address):
        raise VoldModuleError("vold function symbols overlap")

    start = elf.file_offset(check_address, check_size)
    words = struct.unpack_from(f"<{check_size // 4}I", binary, start)
    wanted = _mov_w1(seconds)
    sites = []
    for index, word in enumerate(words):
        if word != wanted:
            continue
        for follow in range(index + 1, min(index + 1 + CALL_WINDOW, len(words))):
            candidate = words[follow]
            if _is_bl(candidate):
                address = check_address + follow * 4
                if _bl_target(address, candidate) == fork_address:
                    sites.append(start + index * 4)
                break
            if _is_other_branch(candidate):
                break
            if not _is_safe_argument_setup(candidate):
                break
    return sites


def patch_vold(binary: bytes) -> bytes:
    """Return ``binary`` with exFAT fsck's limit raised from 45 to 120 seconds."""
    sites = find_call_sites(binary, OLD_SECONDS)
    if len(sites) != 1:
        raise VoldModuleError(
            f"expected exactly one {OLD_SECONDS}-second exFAT fsck call site, found {len(sites)}"
        )
    offset = sites[0]
    patched = bytearray(binary)
    struct.pack_into("<I", patched, offset, _mov_w1(NEW_SECONDS))
    result = bytes(patched)
    verify_patched(binary, result)
    return result


def verify_patched(original: bytes, patched: bytes) -> None:
    """Prove ``patched`` is ``original`` with only the intended instruction changed."""
    if len(original) != len(patched):
        raise VoldModuleError("patched vold has a different size")
    changed = [i for i in range(len(original)) if original[i] != patched[i]]
    if not changed or changed[0] & ~3 != changed[-1] & ~3:
        raise VoldModuleError("patched vold differs in more than one instruction")
    (before,) = struct.unpack_from("<I", original, changed[0] & ~3)
    (after,) = struct.unpack_from("<I", patched, changed[0] & ~3)
    if before != _mov_w1(OLD_SECONDS) or after != _mov_w1(NEW_SECONDS):
        raise VoldModuleError("patched vold changed an unexpected instruction")
    if find_call_sites(patched, OLD_SECONDS):
        raise VoldModuleError("patched vold still has a 45-second exFAT fsck call site")
    if len(find_call_sites(patched, NEW_SECONDS)) != 1:
        raise VoldModuleError("patched vold does not have exactly one 120-second call site")


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
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise VoldModuleError(f"vold input changed before it was read: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            data = stream.read(maximum + 1)
    except OSError as exc:
        raise VoldModuleError(f"required vold input cannot be read: {path.name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) != metadata.st_size:
        raise VoldModuleError(f"required vold input has an unexpected size: {path.name}")
    return data


def inject_vold(system_fs: Any) -> str:
    """Patch /system/bin/vold in the unpacked system image; return the new SHA-256."""
    entries = [entry for entry in system_fs.info.entries if entry.path == TARGET]
    target_path = system_fs.tree / TARGET.relative_to("/")
    if len(entries) != 1 or entries[0].file_type != "RegularFile":
        raise VoldModuleError("target /system/bin/vold filesystem metadata is missing or not regular")
    if not entries[0].file_mode & 0o111:
        raise VoldModuleError("target /system/bin/vold is not executable in filesystem metadata")
    try:
        metadata = target_path.lstat()
    except OSError as exc:
        raise VoldModuleError("target /system/bin/vold is missing from the unpacked system image") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise VoldModuleError("target /system/bin/vold tree path is not a safe regular file")
    labels = [label for pattern, label in system_fs.contexts if pattern.fullmatch(str(TARGET))]
    if not labels or labels[0] != EXPECTED_LABEL:
        raise VoldModuleError("target /system/bin/vold lacks the expected vold_exec SELinux label")

    original = _read_regular_file(target_path, MAX_BINARY_SIZE)
    patched = patch_vold(original)

    # ExtFs reuses the existing image metadata for an existing regular file.
    # Do not unlink/recreate the path: that would change its mode/owner/context.
    with system_fs.open(str(TARGET), "wb") as stream:
        stream.write(patched)
    if _read_regular_file(target_path, MAX_BINARY_SIZE) != patched:
        raise VoldModuleError("injected vold differs from the verified patched binary")
    if len([entry for entry in system_fs.info.entries if entry.path == TARGET]) != 1:
        raise VoldModuleError("vold injection unexpectedly changed filesystem metadata entries")
    return sha256(patched)


def _module_class() -> type[Any]:
    # Imported only after ModOS copies this module into the pinned helper.
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
            return ModuleRequirements(boot_images=set(), ext_images={"system"}, selinux_patching=False)

        def inject(
            self,
            boot_fs: dict[str, CpioFs],
            ext_fs: dict[str, ExtFs],
            sepolicies: Iterable[Path],
            compatible_sepolicy: bool = False,
        ) -> None:
            del boot_fs, sepolicies, compatible_sepolicy
            if os.environ.get("PIXENEOS_ROM_FAMILY", "") != "lineageos":
                raise VoldModuleError("the vold exFAT timeout patch is supported only for LineageOS")
            if "system" not in ext_fs:
                raise VoldModuleError("system image is unavailable for vold patching")
            inject_vold(ext_fs["system"])

    return LineageVoldMod


if __name__ != "__main__":
    try:
        LineageVoldMod = _module_class()
    except ModuleNotFoundError as exc:
        # Unit tests import the helpers without the downloaded helper.
        if exc.name != "lib":
            raise


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    patch = sub.add_parser("patch", help="patch a vold binary file (offline use and testing)")
    patch.add_argument("vold", type=Path)
    patch.add_argument("output", type=Path)
    check = sub.add_parser("check", help="report whether a vold binary can be patched")
    check.add_argument("vold", type=Path)
    args = parser.parse_args(argv)
    try:
        original = _read_regular_file(args.vold, MAX_BINARY_SIZE)
        if args.command == "check":
            sites = find_call_sites(original, OLD_SECONDS)
            print(f"{len(sites)} patchable call site(s)")
            return 0 if len(sites) == 1 else 1
        patched = patch_vold(original)
        args.output.write_bytes(patched)
        print(f"{sha256(original)} -> {sha256(patched)}")
    except (VoldModuleError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
