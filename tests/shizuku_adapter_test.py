#!/usr/bin/env python3
"""Focused source-contract checks for the Shizuku image adapter."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.shizuku as shizuku


class FakeSystemFs:
    def __init__(self, root: Path) -> None:
        self.tree = root
        shell_path = PurePosixPath("/system/bin/sh")
        self.contexts = [
            (re.compile(r"/system/bin/sh"), "u:object_r:shell_exec:s0")
        ]
        self.info = SimpleNamespace(
            entries=[SimpleNamespace(path=shell_path, file_type="RegularFile")]
        )
        shell = root / "system/bin/sh"
        shell.parent.mkdir(parents=True)
        shell.write_text("shell")


class FakeProductFs:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests = []

    def install(self, requests, *, label_mountpoint: str):
        self.requests = list(requests)
        if label_mountpoint != "/product":
            raise AssertionError("product paths must be labeled against /product")
        if self.error is not None:
            raise self.error
        return [SimpleNamespace(path=request.path) for request in requests]


class ShizukuAdapterTest(unittest.TestCase):
    def test_accepts_each_canonical_rom_profile(self) -> None:
        shizuku.validate_target("grapheneos")
        shizuku.validate_target("lineageos")

    def test_rejects_unknown_rom_profile(self) -> None:
        with self.assertRaises(shizuku.ShizukuModuleError):
            shizuku.validate_target("unsupported")

    def test_root_mode_requires_separate_outputs(self) -> None:
        for mode in ("rootless", "magisk"):
            with self.subTest(mode=mode), patch.dict(
                os.environ, {shizuku.ROOT_MODE_ENVIRONMENT: mode}
            ):
                self.assertEqual(mode, shizuku.root_mode_from_environment())
        with patch.dict(
            os.environ, {shizuku.ROOT_MODE_ENVIRONMENT: "both"}
        ):
            with self.assertRaises(shizuku.ShizukuModuleError):
                shizuku.root_mode_from_environment()

    def test_missing_product_and_rootless_system_images_fail_closed(self) -> None:
        with self.assertRaisesRegex(shizuku.ShizukuModuleError, "product ext"):
            shizuku.inject_payload(
                "lineageos", "magisk", Path("unused.apk"), {}, object
            )
        with self.assertRaisesRegex(shizuku.ShizukuModuleError, "system image"):
            shizuku.inject_payload(
                "grapheneos",
                "rootless",
                Path("unused.apk"),
                {"product": FakeProductFs()},
                object,
            )

    def test_rootless_shell_path_and_label_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            system = FakeSystemFs(Path(temporary))
            system.contexts = []
            with self.assertRaisesRegex(shizuku.ShizukuModuleError, "shell_exec"):
                shizuku.inject_payload(
                    "lineageos",
                    "rootless",
                    Path("unused.apk"),
                    {"product": FakeProductFs(), "system": system},
                    object,
                )

    def test_product_file_context_failure_is_not_ignored(self) -> None:
        product = FakeProductFs(ValueError("missing product context"))
        with tempfile.TemporaryDirectory() as temporary:
            system = FakeSystemFs(Path(temporary))
            with patch.object(
                shizuku,
                "_read_payload",
                return_value=(b"apk", {"arm64-v8a": {"libshizuku.so": b"elf", "librish.so": b"elf"}}),
            ):
                with self.assertRaisesRegex(shizuku.ShizukuModuleError, "label"):
                    shizuku.inject_payload(
                        "lineageos",
                        "rootless",
                        Path("unused.apk"),
                        {"product": product, "system": system},
                        lambda *args, **kwargs: SimpleNamespace(path=args[0]),
                    )

    def test_rootless_launcher_fails_closed_for_unrecognized_runtime_abi(self) -> None:
        launcher = shizuku._rootless_launcher(("arm64-v8a",))
        self.assertIn(b'arm64-v8a) starter=\'arm64\' ;;', launcher)
        self.assertIn(b'*) exit 64 ;;', launcher)
        self.assertIn(
            b'exec "/product/app/Shizuku/lib/$starter/libshizuku.so"', launcher
        )

    def test_locked_apk_must_include_a_complete_supported_server_abi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / "manager.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                for name in shizuku.SERVER_NATIVE_LIBRARIES:
                    archive.writestr(f"lib/arm64-v8a/{name}", b"\x7fELFserver")
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            with patch.object(shizuku, "APK_SHA256", digest):
                _, libraries = shizuku._read_payload(apk)
            self.assertEqual(
                set(shizuku.SERVER_NATIVE_LIBRARIES),
                set(libraries["arm64-v8a"]),
            )

            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("lib/riscv64/libshizuku.so", b"\x7fELFserver")
                archive.writestr("lib/riscv64/librish.so", b"\x7fELFserver")
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            with patch.object(shizuku, "APK_SHA256", digest):
                with self.assertRaisesRegex(shizuku.ShizukuModuleError, "supported native"):
                    shizuku._read_payload(apk)


if __name__ == "__main__":
    unittest.main()
