#!/usr/bin/env python3
"""Install the locked official Shizuku manager and rootless shell starter.

The adapter uses one common product/init layout across ModOS ROM profiles.
It checks required image partitions, shell executable metadata, native ABI
payloads, and file-context support, but target-device startup remains
unverified. Magisk builds install the same manager and leave server startup to
Shizuku's ROOT mode after user setup.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import stat
import zipfile
from collections.abc import Iterable
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


APK_SHA256 = "6e273ab0e991c4e79bc8b1bbb9b9dd739ccac1a8712a541a214078886b7b790f"
APK_ENVIRONMENT = "PIXENEOS_SHIZUKU_APK_PATH"
ROOT_MODE_ENVIRONMENT = "PIXENEOS_SHIZUKU_ROOT_MODE"
ROM_FAMILY_ENVIRONMENT = "PIXENEOS_ROM_FAMILY"
SUPPORTED_ROM_FAMILIES = ("grapheneos", "lineageos")
SUPPORTED_STARTER_ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64")
SERVER_NATIVE_LIBRARIES = ("libshizuku.so", "librish.so")
STARTER_LIBRARY_ABIS = {
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
    "x86": "x86",
    "x86_64": "x86_64",
}
MAX_NATIVE_LIBRARY_BYTES = 1024 * 1024


class ShizukuModuleError(RuntimeError):
    """Raised when the target or locked Shizuku payload is unsupported."""


def root_mode_from_environment() -> str:
    mode = os.environ.get(ROOT_MODE_ENVIRONMENT, "")
    if mode not in {"rootless", "magisk"}:
        raise ShizukuModuleError(
            "Shizuku supports separate rootless or Magisk builds; ROOT_MODE=both "
            "cannot distinguish their shared product image"
        )
    return mode


def _read_payload(path: Path) -> tuple[bytes, dict[str, dict[str, bytes]]]:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ShizukuModuleError("locked Shizuku APK is not a safe regular file")
        apk_bytes = path.read_bytes()
    except ShizukuModuleError:
        raise
    except OSError as exc:
        raise ShizukuModuleError("locked Shizuku APK is unavailable") from exc
    if hashlib.sha256(apk_bytes).hexdigest() != APK_SHA256:
        raise ShizukuModuleError("Shizuku APK does not match its locked SHA-256")

    native_libraries: dict[str, dict[str, bytes]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(apk_bytes)) as archive:
            for abi in SUPPORTED_STARTER_ABIS:
                archive_members = set(archive.namelist())
                present = {
                    name: f"lib/{abi}/{name}" in archive_members
                    for name in SERVER_NATIVE_LIBRARIES
                }
                if not any(present.values()):
                    continue
                if not all(present.values()):
                    raise ShizukuModuleError(
                        f"Shizuku APK has an incomplete server library set for {abi}"
                    )
                native_libraries[abi] = {}
                for name in SERVER_NATIVE_LIBRARIES:
                    library = archive.read(f"lib/{abi}/{name}")
                    if (
                        not library.startswith(b"\x7fELF")
                        or len(library) > MAX_NATIVE_LIBRARY_BYTES
                    ):
                        raise ShizukuModuleError(
                            f"Shizuku native library {name} has an invalid ELF payload for {abi}"
                        )
                    native_libraries[abi][name] = library
    except (OSError, zipfile.BadZipFile) as exc:
        raise ShizukuModuleError("Shizuku APK is not a readable ZIP archive") from exc

    if not native_libraries:
        raise ShizukuModuleError("Shizuku APK has no supported native server libraries")
    return apk_bytes, native_libraries


def _rootless_init_rc() -> bytes:
    return b"""# Start Shizuku from init with the ADB shell identity; client approval stays in the manager.
service shizuku /system/bin/sh /product/bin/shizuku
    user shell
    group shell
    disabled
    oneshot

on property:sys.boot_completed=1
    start shizuku
"""


def _rootless_launcher(abis: tuple[str, ...]) -> bytes:
    # Keep the runtime choice constrained to ABIs actually present in the
    # locked APK. The property is device supplied and is matched as a full
    # literal before it is used as a path component.
    cases = "\n".join(
        f"    {abi}) starter='{STARTER_LIBRARY_ABIS[abi]}' ;;" for abi in abis
    )
    script = f"""#!/system/bin/sh
abi=$(/system/bin/getprop ro.product.cpu.abi) || exit 1
case "$abi" in
{cases}
    *) exit 64 ;;
esac
exec "/product/app/Shizuku/lib/$starter/libshizuku.so"
"""
    return script.encode("ascii")


def validate_target(rom_family: str) -> None:
    """Accept only ROM families resolved by ModOS's canonical profiles.

    Device-specific build support is determined by the OTA provider and the
    fail-closed image capability checks in ``inject_payload``; env.toml's
    selected device is not a support allowlist.
    """
    if rom_family not in SUPPORTED_ROM_FAMILIES:
        raise ShizukuModuleError(
            "Shizuku requires a supported ModOS ROM profile"
        )


def inject_payload(
    rom_family: str,
    root_mode: str,
    apk_path: Path,
    ext_fs: dict[str, Any],
    install_request: type[Any],
) -> tuple[str, ...]:
    validate_target(rom_family)
    if root_mode not in {"rootless", "magisk"}:
        raise ShizukuModuleError(
            "Shizuku supports separate rootless or Magisk builds; "
            "ROOT_MODE=both is unsupported"
        )
    if "product" not in ext_fs:
        raise ShizukuModuleError("Shizuku requires a mounted product ext image")

    if root_mode == "rootless":
        system = ext_fs.get("system")
        if system is None:
            raise ShizukuModuleError(
                "rootless Shizuku requires the system image to verify shell startup"
            )
        shell_path = PurePosixPath("/system/bin/sh")
        shell_labels = [
            label
            for pattern, label in system.contexts
            if pattern.fullmatch(str(shell_path))
        ]
        shell_entries = [
            entry for entry in system.info.entries if entry.path == shell_path
        ]
        shell_tree_path = system.tree / shell_path.relative_to("/")
        if (
            not shell_labels
            or shell_labels[0] != "u:object_r:shell_exec:s0"
            or len(shell_entries) != 1
            or shell_entries[0].file_type != "RegularFile"
            or not shell_tree_path.is_file()
            or shell_tree_path.is_symlink()
        ):
            raise ShizukuModuleError(
                "rootless Shizuku requires a regular /system/bin/sh with the "
                "standard shell_exec label"
            )

    apk_bytes, native_libraries = _read_payload(apk_path)
    product = ext_fs["product"]
    requests: list[Any] = []

    def directory(path: str) -> None:
        requests.append(install_request(path, "Directory", 0o755, 0, 0))

    directory("/app")
    directory("/app/Shizuku")
    requests.append(
        install_request(
            "/app/Shizuku/Shizuku.apk", "RegularFile", 0o644, 0, 0, apk_bytes
        )
    )

    if root_mode == "rootless":
        directory("/etc/init")
        requests.append(
            install_request(
                "/etc/init/shizuku.rc", "RegularFile", 0o644, 0, 0, _rootless_init_rc()
            )
        )
        directory("/bin")
        directory("/app/Shizuku/lib")
        for abi, libraries in native_libraries.items():
            library_abi = STARTER_LIBRARY_ABIS[abi]
            directory(f"/app/Shizuku/lib/{library_abi}")
            for name, library in libraries.items():
                requests.append(
                    install_request(
                        f"/app/Shizuku/lib/{library_abi}/{name}",
                        "RegularFile",
                        0o755 if name == "libshizuku.so" else 0o644,
                        0,
                        0,
                        library,
                    )
                )
        requests.append(
            install_request(
                "/bin/shizuku",
                "RegularFile",
                0o755,
                0,
                0,
                _rootless_launcher(tuple(native_libraries)),
            )
        )

    try:
        installed = product.install(requests, label_mountpoint="/product")
    except (OSError, ValueError) as exc:
        # ExtFs fails closed when a required file_contexts label is unavailable
        # or an existing entry differs. Do not invent labels or patch policy.
        raise ShizukuModuleError(
            "product image cannot safely label the Shizuku payload/init paths"
        ) from exc
    return tuple(str(result.path) for result in installed)


def _module_class() -> type[Any]:
    # Helper imports stay lazy so validation utilities can be imported alone.
    from lib.filesystem import CpioFs, ExtFs, ExtInstallRequest
    from lib.modules import LegacyCliModule, MissingArgs, ModuleRequirements

    class ShizukuMod(LegacyCliModule):
        NAME = "shizuku"

        @classmethod
        def add_args(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument(f"--module-{cls.NAME}", type=Path)
            parser.add_argument(f"--module-{cls.NAME}-sig", type=Path)

        @classmethod
        def from_args(cls, args: argparse.Namespace) -> "ShizukuMod":
            if getattr(args, "module_shizuku", None) is None:
                raise MissingArgs()
            return cls()

        def requirements(self) -> ModuleRequirements:
            root_mode_from_environment()
            ext_images = {"product"}
            if os.environ[ROOT_MODE_ENVIRONMENT] == "rootless":
                ext_images.add("system")
            return ModuleRequirements(
                boot_images=set(),
                ext_images=ext_images,
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
            root_mode = root_mode_from_environment()
            apk_value = os.environ.get(APK_ENVIRONMENT, "")
            if not apk_value:
                raise ShizukuModuleError("locked Shizuku APK path is not configured")
            inject_payload(
                os.environ.get(ROM_FAMILY_ENVIRONMENT, ""),
                root_mode,
                Path(apk_value),
                ext_fs,
                ExtInstallRequest,
            )

    return ShizukuMod


if __name__ != "__main__":
    try:
        from lib.filesystem import ExtInstallRequest
    except ModuleNotFoundError as exc:
        if exc.name != "lib":
            raise
    else:
        ShizukuMod = _module_class()
