#!/usr/bin/env python3
"""Check the pinned, opt-in LineageOS vold source/build contract."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "ci"))
import lineage_vold  # noqa: E402
import build_lineage_vold  # noqa: E402


class LineageVoldContractTest(unittest.TestCase):
    @staticmethod
    def fixture_elf() -> bytes:
        binary = bytearray(0x500)
        ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
        struct.pack_into("<16sHHIQQQIHHHHHH", binary, 0,
                         ident, 3, 183, 1, 0, 64, 0, 0, 64, 56, 3, 0, 0, 0)
        headers = (
            (1, 5, 0x300, 0x1000, 0, 0x100, 0x100, 0x1000),
            (2, 6, 0x200, 0, 0, 64, 64, 8),
            (3, 4, 0x180, 0, 0, 21, 21, 1),
        )
        for index, header in enumerate(headers):
            struct.pack_into("<IIQQQQQQ", binary, 64 + index * 56, *header)
        binary[0x180:0x195] = b"/system/bin/linker64\0"
        strings = b"\0libc.so\0"
        binary[0x310:0x310 + len(strings)] = strings
        dynamic = ((5, 0x1010), (10, len(strings)), (1, 1), (0, 0))
        for index, entry in enumerate(dynamic):
            struct.pack_into("<qQ", binary, 0x200 + index * 16, *entry)
        return bytes(binary)

    def test_locked_manifest_and_patch_match_source_contract(self):
        lock, manifest_bytes, patch_bytes = build_lineage_vold.load_contract()
        manifest = build_lineage_vold.project_map(manifest_bytes)
        self.assertEqual(len(manifest), 1243)
        self.assertEqual(
            manifest["system/vold"],
            ("LineageOS/android_system_vold", lineage_vold.VOLD_REVISION),
        )
        self.assertEqual(
            manifest["device/sony/pdx235"],
            ("LineageOS/android_device_sony_pdx235", lineage_vold.DEVICE_REVISION),
        )
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), lock["manifest"]["sha256"])
        self.assertEqual(hashlib.sha256(patch_bytes).hexdigest(), lock["patch"]["sha256"])
        patch = patch_bytes.decode()
        self.assertIn("kUntrustedExfatFsckSleepTime(120)", patch)
        self.assertIn("kUntrustedFsckSleepTime(45)", patch)
        self.assertEqual(patch.count("diff --git "), 2)

    def test_lock_adapter_and_build_target_agree(self):
        lock = json.loads((ROOT / "locks/lineage-vold-exfat-timeout.json").read_text())
        self.assertEqual(lock["module"], lineage_vold.MODULE_NAME)
        self.assertEqual(lock["base_build_id"], lineage_vold.BASE_BUILD_ID)
        self.assertEqual(lock["projects"]["vold"]["revision"], lineage_vold.VOLD_REVISION)
        self.assertEqual(lock["projects"]["device"]["revision"], lineage_vold.DEVICE_REVISION)
        self.assertEqual(lock["build"]["target"], "lineage_pdx235-userdebug")
        self.assertEqual(lock["build"]["install_path"], "/system/bin/vold")
        self.assertEqual(lock["build"]["interpreter"], "/system/bin/linker64")

    def test_adapter_is_scoped_to_lineageos_pdx235(self):
        lineage_vold._require_target_profile("lineageos", "pdx235")
        for family, device in (("grapheneos", "pdx235"), ("lineageos", "other")):
            with self.subTest(family=family, device=device), self.assertRaises(lineage_vold.VoldModuleError):
                lineage_vold._require_target_profile(family, device)

    def test_artifact_requires_full_pinned_provenance_and_arm64_abi(self):
        binary = self.fixture_elf()
        digest = hashlib.sha256(binary).hexdigest()
        lock = json.loads((ROOT / "locks/lineage-vold-exfat-timeout.json").read_text())
        provenance = {
            "schema_version": 1,
            "module": lock["module"],
            "base_build_id": lock["base_build_id"],
            "manifest_sha256": lock["manifest"]["sha256"],
            "vold_revision": lock["projects"]["vold"]["revision"],
            "device_revision": lock["projects"]["device"]["revision"],
            "patch_sha256": lock["patch"]["sha256"],
            "build_target": lock["build"]["target"],
            "build_command": lock["build"]["command"],
            "artifact_path": lock["build"]["artifact"],
            "binary_sha256": digest,
            "elf": {
                "elf_class": 64,
                "machine": "AArch64",
                "interpreter": "/system/bin/linker64",
                "needed": ["libc.so"],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp)
            (artifact / "vold").write_bytes(binary)
            (artifact / "provenance.json").write_text(json.dumps(provenance))
            validated, parsed = lineage_vold._artifact(artifact)
            self.assertEqual(validated, binary)
            self.assertEqual(parsed["binary_sha256"], digest)
            provenance["base_build_id"] = "different-build"
            (artifact / "provenance.json").write_text(json.dumps(provenance))
            with self.assertRaises(lineage_vold.VoldModuleError):
                lineage_vold._artifact(artifact)

    def test_helper_module_imports_without_helper_runtime(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "lib"
            modules = lib / "modules"
            modules.mkdir(parents=True)
            (lib / "__init__.py").touch()
            (modules / "__init__.py").write_text(
                "class LegacyCliModule: pass\n"
                "class MissingArgs(Exception): pass\n"
                "class ModuleRequirements:\n"
                "    def __init__(self, **kwargs): self.__dict__.update(kwargs)\n"
            )
            (lib / "filesystem.py").write_text("class CpioFs: pass\nclass ExtFs: pass\n")
            (modules / "lineage_vold.py").write_bytes((ROOT / "src/lineage_vold.py").read_bytes())
            sys.path.insert(0, tmp)
            os.environ["PIXENEOS_ROM_FAMILY"] = "lineageos"
            os.environ["PIXENEOS_DEVICE_NAME"] = "pdx235"
            try:
                spec = importlib.util.find_spec("lib.modules.lineage_vold")
                self.assertIsNotNone(spec)
                loaded = __import__("lib.modules.lineage_vold", fromlist=["LineageVoldMod"])
                self.assertEqual(loaded.LineageVoldMod.NAME, lineage_vold.MODULE_NAME)
                self.assertEqual(
                    loaded.LineageVoldMod.requirements(loaded.LineageVoldMod()).ext_images,
                    {"system"},
                )
            finally:
                sys.path.remove(tmp)
                os.environ.pop("PIXENEOS_ROM_FAMILY", None)
                os.environ.pop("PIXENEOS_DEVICE_NAME", None)
                for name in list(sys.modules):
                    if name == "lib" or name.startswith("lib."):
                        del sys.modules[name]


if __name__ == "__main__":
    unittest.main()
