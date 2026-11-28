#!/usr/bin/env python3
"""Image payload and foreground-launch regression checks for Shizuku."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.shizuku as shizuku


@dataclass
class InstallRequest:
    path: str
    file_type: str
    mode: int
    uid: int
    gid: int
    contents: bytes | None = None


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
                return_value=(b"apk", {"arm64-v8a": {
                    name: b"elf" for name in shizuku.APK_NATIVE_LIBRARIES
                }}),
            ):
                with self.assertRaisesRegex(shizuku.ShizukuModuleError, "label"):
                    shizuku.inject_payload(
                        "lineageos",
                        "rootless",
                        Path("unused.apk"),
                        {"product": product, "system": system},
                        lambda *args, **kwargs: SimpleNamespace(path=args[0]),
                    )

    def test_rootless_service_has_shell_domain_and_supervised_lifetime(self) -> None:
        lines = [line.strip() for line in shizuku._rootless_init_rc().decode().splitlines()]
        self.assertIn("service shizuku /system/bin/sh /product/bin/shizuku", lines)
        self.assertIn("user shell", lines)
        self.assertIn("seclabel u:r:shell:s0", lines)
        self.assertIn("disabled", lines)
        self.assertIn("restart_period 10", lines)
        self.assertIn("on property:sys.boot_completed=1", lines)
        self.assertIn("start shizuku", lines)
        self.assertNotIn("oneshot", lines)
        self.assertNotIn("critical", lines)
        groups = next(line.split()[1:] for line in lines if line.startswith("group "))
        self.assertEqual("shell", groups[0])
        self.assertEqual(
            {"adb", "log", "input", "inet", "net_bt", "net_bt_admin",
             "sdcard_r", "sdcard_rw", "net_bw_stats", "readproc", "uhid",
             "ext_data_rw", "ext_obb_rw", "readtracefs"},
            set(groups[1:]),
        )

    def test_installs_all_manager_libraries_in_both_modes(self) -> None:
        payload = {
            abi: {name: b"\x7fELF" + name.encode() for name in shizuku.APK_NATIVE_LIBRARIES}
            for abi in shizuku.SUPPORTED_STARTER_ABIS
        }
        for mode in ("rootless", "magisk"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                product = FakeProductFs()
                filesystems = {"product": product}
                if mode == "rootless":
                    filesystems["system"] = FakeSystemFs(Path(temporary))
                with patch.object(shizuku, "_read_payload", return_value=(b"locked APK", payload)):
                    installed_paths = shizuku.inject_payload(
                        "lineageos", mode, Path("manager.apk"), filesystems, InstallRequest
                    )
                files = {entry.path: entry for entry in product.requests}
                self.assertEqual(
                    installed_paths,
                    tuple(str(entry.path) for entry in product.requests),
                    "Shizuku must report every installed image-relative path",
                )
                self.assertEqual(b"locked APK", files["/app/Shizuku/Shizuku.apk"].contents)
                for abi, libraries in payload.items():
                    for name, contents in libraries.items():
                        entry = files[f"/app/Shizuku/lib/{shizuku.STARTER_LIBRARY_ABIS[abi]}/{name}"]
                        self.assertEqual(contents, entry.contents)
                        self.assertEqual((0, 0), (entry.uid, entry.gid))
                        self.assertEqual(0o755 if name == "libshizuku.so" else 0o644, entry.mode)
                if mode == "rootless":
                    self.assertIn("/etc/init/shizuku.rc", files)
                    self.assertIn("/bin/shizuku", files)
                    config = ET.fromstring(files["/etc/sysconfig/modos-shizuku.xml"].contents)
                    override = config.find("component-override")
                    self.assertIsNotNone(override)
                    self.assertEqual("moe.shizuku.privileged.api", override.attrib["package"])
                    self.assertEqual(
                        [{"class": "moe.shizuku.manager.receiver.BootCompleteReceiver", "enabled": "false"}],
                        [component.attrib for component in override],
                    )
                else:
                    self.assertFalse(any(path.startswith(("/etc/", "/bin/")) for path in files))

    def _run_launcher(self, runtime_abi: str, *, missing_library: bool = False):
        """Execute the generated script with only Android's paths substituted.

        The app_process stand-in records the real PID, argv, and environment,
        so a fork-and-exit launcher cannot masquerade as a supervised service.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            system_bin = root / "system/bin"
            system_bin.mkdir(parents=True)
            getprop = system_bin / "getprop"
            getprop.write_text('#!/bin/sh\nprintf \'%s\\n\' "$TEST_ABI"\n')
            getprop.chmod(0o755)
            app_process = system_bin / "app_process"
            app_process.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "print(json.dumps({'pid': os.getpid(), 'args': sys.argv[1:], "
                "'classpath': os.environ.get('CLASSPATH')}))\n"
            )
            app_process.chmod(0o755)
            app = root / "product/app/Shizuku"
            app.mkdir(parents=True)
            (app / "Shizuku.apk").write_bytes(b"locked APK")
            for abi in shizuku.STARTER_LIBRARY_ABIS.values():
                native = app / "lib" / abi
                native.mkdir(parents=True)
                if not missing_library:
                    (native / "librish.so").write_bytes(b"native library")
            launcher = root / "launcher.sh"
            launcher.write_text(
                shizuku._rootless_launcher(shizuku.SUPPORTED_STARTER_ABIS).decode()
                .replace("/system/bin", str(system_bin))
                .replace("/product/", str(root / "product") + "/")
            )
            with subprocess.Popen(
                ["/bin/sh", str(launcher)],
                env={**os.environ, "TEST_ABI": runtime_abi},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ) as process:
                output, error = process.communicate(timeout=10)
                self.assertEqual("", error)
                return process.returncode, process.pid, output, str(root)

    def test_rootless_launcher_execs_the_pinned_server_in_the_foreground(self) -> None:
        for abi, library_abi in shizuku.STARTER_LIBRARY_ABIS.items():
            with self.subTest(abi=abi):
                status, pid, output, root = self._run_launcher(abi)
                self.assertEqual(0, status)
                invocation = json.loads(output)
                self.assertEqual(pid, invocation["pid"])
                apk = f"{root}/product/app/Shizuku/Shizuku.apk"
                self.assertEqual(apk, invocation["classpath"])
                self.assertEqual([
                    f"-Djava.class.path={apk}",
                    f"-Dshizuku.library.path={root}/product/app/Shizuku/lib/{library_abi}",
                    f"{root}/system/bin", "--nice-name=shizuku_server",
                    "rikka.shizuku.server.ShizukuService",
                ], invocation["args"])

    def test_rootless_launcher_rejects_unknown_abi_or_missing_native_payload(self) -> None:
        for abi in ("", "riscv64", "arm64-v8a/../../evil", "arm64-v8a; false"):
            with self.subTest(abi=abi):
                status, _, output, _ = self._run_launcher(abi)
                self.assertEqual(64, status)
                self.assertEqual("", output)
        status, _, output, _ = self._run_launcher("arm64-v8a", missing_library=True)
        self.assertEqual(66, status)
        self.assertEqual("", output)

    def test_locked_apk_must_include_a_complete_supported_server_abi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / "manager.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                for name in shizuku.APK_NATIVE_LIBRARIES:
                    archive.writestr(f"lib/arm64-v8a/{name}", b"\x7fELFserver")
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            with patch.object(shizuku, "APK_SHA256", digest):
                _, libraries = shizuku._read_payload(apk)
            self.assertEqual(
                set(shizuku.APK_NATIVE_LIBRARIES),
                set(libraries["arm64-v8a"]),
            )

            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("lib/riscv64/libshizuku.so", b"\x7fELFserver")
                archive.writestr("lib/riscv64/librish.so", b"\x7fELFserver")
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            with patch.object(shizuku, "APK_SHA256", digest):
                with self.assertRaisesRegex(shizuku.ShizukuModuleError, "supported native"):
                    shizuku._read_payload(apk)

    def test_payload_requires_the_manager_adb_library_and_locked_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / "manager.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                for name in ("libshizuku.so", "librish.so"):
                    archive.writestr(f"lib/arm64-v8a/{name}", b"\x7fELFserver")
            with self.assertRaisesRegex(shizuku.ShizukuModuleError, "SHA-256"):
                shizuku._read_payload(apk)
            with patch.object(shizuku, "APK_SHA256", hashlib.sha256(apk.read_bytes()).hexdigest()):
                with self.assertRaisesRegex(shizuku.ShizukuModuleError, "incomplete native"):
                    shizuku._read_payload(apk)


if __name__ == "__main__":
    unittest.main()
