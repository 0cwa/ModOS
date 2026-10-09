#!/usr/bin/env python3
"""Validate and inject a local Android boot-animation archive.

The module class is imported by the pinned my-avbroot-setup helper.  The
command-line entry point is intentionally independent of that helper so the
payload can be validated before patching starts.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import stat
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any


MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_COUNT = 4096
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_DESCRIPTION_BYTES = 4096
PAYLOAD_ENVIRONMENT = "PIXENEOS_BOOT_ANIMATION_PATH"
DARK_PAYLOAD_ENVIRONMENT = "PIXENEOS_BOOT_ANIMATION_DARK_PATH"
BOOT_ANIMATION_TARGETS = (
    ("product", "/media/bootanimation.zip"),
    ("product", "/media/bootanimation-dark.zip"),
)


class BootAnimationError(ValueError):
    """Raised when the user-supplied boot animation is unsafe or invalid."""


def _reject(message: str) -> None:
    raise BootAnimationError(message)


def _validate_member_name(name: str) -> tuple[str, ...]:
    if not name or "\x00" in name or "\\" in name:
        _reject("ZIP member has an unsafe name")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        _reject("ZIP member must be a relative POSIX path")
    if name.endswith("/"):
        raw_parts = name[:-1].split("/")
    else:
        raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        _reject("ZIP member contains an unsafe path component")
    # Keep this check explicit even though the split above is sufficient.  It
    # documents that archive paths are never normalized before validation.
    if str(PurePosixPath(name)) != name and not name.endswith("/"):
        _reject("ZIP member is not a canonical POSIX path")
    return tuple(raw_parts)


def _validate_member_type(info: zipfile.ZipInfo) -> None:
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        _reject(f"ZIP member is not a regular file or directory: {info.filename}")
    if info.is_dir() and not info.filename.endswith("/"):
        _reject(f"ZIP directory is not canonical: {info.filename}")


def _read_member(archive: zipfile.ZipFile, member: str | zipfile.ZipInfo) -> bytes:
    try:
        return archive.read(member)
    except (KeyError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BootAnimationError("boot animation contains an unreadable ZIP member") from exc


def _validate_description(data: bytes, part_names: set[str]) -> None:
    if not data or len(data) > MAX_DESCRIPTION_BYTES:
        _reject("desc.txt is empty or too large")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BootAnimationError("desc.txt is not valid UTF-8") from exc

    meaningful = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not meaningful or not re.fullmatch(r"[1-9][0-9]*\s+[1-9][0-9]*\s+[1-9][0-9]*", meaningful[0]):
        _reject("desc.txt has an invalid size or frame-rate line")
    for line in meaningful[1:]:
        match = re.fullmatch(
            r"[pc]\s+[0-9]+\s+[0-9]+\s+(part[0-9]+)"
            r"(?:\s+#[0-9A-Fa-f]{6}"
            r"(?:\s+(?:c|-?[0-9]+)(?:\s+(?:c|-?[0-9]+))?)?"
            r")?",
            line,
        )
        if not match or match.group(1) not in part_names:
            _reject("desc.txt references an invalid animation part")


def validate_payload(path: str | os.PathLike[str]) -> str:
    """Validate the exact local payload and return its SHA-256 digest."""

    payload = Path(path)
    try:
        before = payload.lstat()
    except FileNotFoundError as exc:
        raise BootAnimationError(f"boot animation is missing: {payload}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _reject("boot animation must be an exact regular file, not a symlink")
    if before.st_size <= 0 or before.st_size > MAX_ARCHIVE_BYTES:
        _reject("boot animation archive size is outside the allowed limit")
    try:
        resolved = payload.resolve(strict=True)
    except OSError as exc:
        raise BootAnimationError("boot animation path cannot be resolved") from exc
    lexical = Path(os.path.abspath(payload))
    if resolved != lexical:
        _reject("boot animation path must not resolve through a symlink")

    try:
        descriptor = os.open(
            payload,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size != before.st_size
        ):
            os.close(descriptor)
            _reject("boot animation changed before it was opened")
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
    except OSError as exc:
        raise BootAnimationError("boot animation cannot be read") from exc
    if len(data) != before.st_size or len(data) > MAX_ARCHIVE_BYTES:
        _reject("boot animation changed while it was being read")

    digest = hashlib.sha256(data).hexdigest()
    try:
        archive = zipfile.ZipFile(__import__("io").BytesIO(data))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise BootAnimationError("boot animation is not a valid ZIP archive") from exc

    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBER_COUNT:
            _reject("boot animation has an excessive or empty member list")
        names: set[str] = set()
        total_uncompressed = 0
        part_names: set[str] = set()
        for info in infos:
            if info.filename in names:
                _reject(f"boot animation contains a duplicate member: {info.filename}")
            names.add(info.filename)
            parts = _validate_member_name(info.filename)
            _validate_member_type(info)
            if info.flag_bits & 0x1:
                _reject(f"encrypted ZIP member is not allowed: {info.filename}")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                _reject(f"unsupported ZIP compression: {info.filename}")
            if info.file_size > MAX_MEMBER_BYTES:
                _reject(f"ZIP member is too large: {info.filename}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                _reject("boot animation expands beyond the allowed limit")
            if info.file_size and not info.compress_size:
                _reject(f"ZIP member has an invalid compressed size: {info.filename}")
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                _reject(f"ZIP member has an excessive compression ratio: {info.filename}")

            if info.is_dir():
                if len(parts) != 1 or not re.fullmatch(r"part[0-9]+", parts[0]):
                    _reject(f"unexpected ZIP directory: {info.filename}")
                part_names.add(parts[0])
                continue

            if info.filename == "desc.txt":
                if info.file_size > MAX_DESCRIPTION_BYTES:
                    _reject("desc.txt is too large")
                description = _read_member(archive, info)
                if len(description) != info.file_size:
                    _reject("desc.txt could not be read completely")
                continue
            if len(parts) != 2 or not re.fullmatch(r"part[0-9]+", parts[0]):
                _reject(f"unexpected boot-animation member: {info.filename}")
            part_names.add(parts[0])
            if not parts[1]:
                _reject("boot-animation frame has an empty name")
            # Reading every member verifies its CRC before the patch helper is
            # invoked and prevents a malformed archive from reaching it.
            _read_member(archive, info)

        if "desc.txt" not in names:
            _reject("boot animation is missing desc.txt")
        if not part_names:
            _reject("boot animation has no animation parts")
        description = _read_member(archive, "desc.txt")
        _validate_description(description, part_names)
        numbered_parts = sorted(int(name[4:]) for name in part_names)
        if numbered_parts != list(range(len(numbered_parts))):
            _reject("boot-animation parts must be numbered contiguously from part0")
        for part in part_names:
            if not any(name.startswith(f"{part}/") and not name.endswith("/") for name in names):
                _reject(f"boot-animation part is empty: {part}")
    return digest


def build_runtime_payload(path: str | os.PathLike[str]) -> bytes:
    """Return a deterministic Android-compatible, uncompressed animation ZIP."""

    validate_payload(path)
    output = io.BytesIO()
    with zipfile.ZipFile(Path(path), "r") as source:
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=False,
        ) as runtime:
            for info in source.infolist():
                if info.is_dir():
                    continue
                runtime_info = zipfile.ZipInfo(
                    info.filename,
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                runtime_info.compress_type = zipfile.ZIP_STORED
                runtime_info.create_system = 3
                runtime_info.external_attr = (stat.S_IFREG | 0o644) << 16
                runtime_info.extra = b""
                runtime_info.comment = b""
                runtime.writestr(
                    runtime_info,
                    _read_member(source, info),
                    compress_type=zipfile.ZIP_STORED,
                )
    return output.getvalue()


def _install_partition_context_aliases(
    fs: Any,
    partition: str,
    raw_targets: Iterable[str],
) -> None:
    """Map partition-relative image paths to their runtime SELinux labels."""

    contexts = list(fs.contexts)
    aliases: list[tuple[re.Pattern[str], str]] = []
    image_paths: set[PurePosixPath] = set()
    for raw_target in raw_targets:
        target = PurePosixPath(raw_target)
        image_paths.add(target.parent)
        image_paths.add(target)

    for image_path in sorted(image_paths, key=str):
        image_path_str = str(image_path)
        if any(pattern.fullmatch(image_path_str) for pattern, _ in contexts + aliases):
            continue

        runtime_path = PurePosixPath("/") / partition / image_path.relative_to("/")
        runtime_path_str = str(runtime_path)
        try:
            label = next(
                label
                for pattern, label in contexts
                if pattern.fullmatch(runtime_path_str)
            )
        except StopIteration as exc:
            raise RuntimeError(
                "no SELinux context maps boot animation runtime path: "
                f"{runtime_path_str}"
            ) from exc

        aliases.append((re.compile(re.escape(image_path_str)), label))

    if aliases:
        fs.contexts = aliases + contexts


def install_runtime_payload(
    ext_fs: dict[str, Any],
    light_payload: bytes,
    dark_payload: bytes | None = None,
) -> None:
    """Install theme payloads through ExtFs with single-file fallback."""

    if dark_payload is None:
        dark_payload = light_payload
    payload_by_target = {
        "/media/bootanimation.zip": light_payload,
        "/media/bootanimation-dark.zip": dark_payload,
    }

    targets_by_partition: dict[str, list[str]] = {}
    for partition, raw_target in BOOT_ANIMATION_TARGETS:
        targets_by_partition.setdefault(partition, []).append(raw_target)
    for partition, raw_targets in targets_by_partition.items():
        fs = ext_fs.get(partition)
        if fs is None:
            raise RuntimeError(
                f"boot animation target partition is missing: {partition}"
            )
        _install_partition_context_aliases(fs, partition, raw_targets)

    for partition, raw_target in BOOT_ANIMATION_TARGETS:
        fs = ext_fs.get(partition)
        if fs is None:
            raise RuntimeError(
                f"boot animation target partition is missing: {partition}"
            )
        target = PurePosixPath(raw_target)
        fs.mkdir(str(target.parent), mode=0o755, parents=True, exist_ok=True)
        with fs.open(str(target), "wb", mode=0o644) as stream:
            stream.write(payload_by_target[raw_target])


def resolve_runtime_payloads(
    light_path: str | os.PathLike[str] | None,
    dark_path: str | os.PathLike[str] | None,
) -> tuple[bytes, bytes]:
    """Resolve one or two source archives into light/dark runtime payloads."""

    if not light_path and not dark_path:
        raise RuntimeError("no boot animation payload is configured")
    light_source = light_path or dark_path
    dark_source = dark_path or light_path
    assert light_source is not None
    assert dark_source is not None
    return build_runtime_payload(light_source), build_runtime_payload(dark_source)


def verify_runtime_installation(
    light_source_path: str | os.PathLike[str],
    dark_source_path: str | os.PathLike[str],
    light_path: str | os.PathLike[str],
    dark_path: str | os.PathLike[str],
) -> None:
    """Verify theme payloads extracted from a finished product image."""

    expected_light, expected_dark = resolve_runtime_payloads(
        light_source_path,
        dark_source_path,
    )
    actual = (
        (Path(light_path), expected_light),
        (Path(dark_path), expected_dark),
    )
    for runtime_path, expected in actual:
        if runtime_path.read_bytes() != expected:
            raise RuntimeError(
                f"finished OTA boot animation does not match payload: {runtime_path}"
            )


def _module_class() -> type[Any]:
    # Import only when the pinned helper imports this file.  This keeps the
    # standalone validator usable before the helper is fetched.
    from lib.filesystem import CpioFs, ExtFs
    from lib.modules import LegacyCliModule, MissingArgs, ModuleRequirements

    class BootAnimationMod(LegacyCliModule):
        NAME = "boot-animation"

        def __init__(self, zip_path: Path, sig_path: Path) -> None:
            self.zip_path = zip_path
            self.sig_path = sig_path

        @classmethod
        def add_args(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument(
                f"--module-{cls.NAME}",
                type=Path,
            )
            # Keep the existing PixeneOS CLI argument shape. The payload is a
            # local, prevalidated build input and deliberately has no helper
            # signature, so from_args() does not verify this placeholder.
            parser.add_argument(
                f"--module-{cls.NAME}-sig",
                type=Path,
            )

        @classmethod
        def from_args(cls, args: argparse.Namespace) -> "BootAnimationMod":
            zip_path = getattr(args, "module_boot_animation", None)
            if zip_path is None:
                raise MissingArgs()
            sig_path = getattr(args, "module_boot_animation_sig", None)
            if sig_path is None:
                sig_path = Path(f"{zip_path}.sig")
            return cls(zip_path, sig_path)

        def requirements(self) -> ModuleRequirements:
            return ModuleRequirements(
                boot_images=set(),
                ext_images={"product"},
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
            payload_path = os.environ.get(PAYLOAD_ENVIRONMENT)
            dark_payload_path = os.environ.get(DARK_PAYLOAD_ENVIRONMENT)
            light_payload, dark_payload = resolve_runtime_payloads(
                payload_path,
                dark_payload_path,
            )
            install_runtime_payload(ext_fs, light_payload, dark_payload)

    return BootAnimationMod


if __name__ != "__main__":
    try:
        BootAnimationMod = _module_class()
    except ModuleNotFoundError as exc:
        # Standalone validator tests import this file without the downloaded
        # helper package. A real helper import must still provide lib.modules.
        if exc.name != "lib":
            raise


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[1] in {"validate", "digest"}:
            print(validate_payload(argv[2]))
            return 0
        if len(argv) == 6 and argv[1] == "verify-runtime":
            verify_runtime_installation(argv[2], argv[3], argv[4], argv[5])
            return 0
    except (BootAnimationError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"usage: {argv[0]} validate <bootanimation.zip> | "
        "verify-runtime <light-source.zip> <dark-source.zip> <light.zip> <dark.zip>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
