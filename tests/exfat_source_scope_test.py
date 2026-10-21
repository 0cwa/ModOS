#!/usr/bin/env python3
"""Verify source-scope checks from the copied helper module layout."""
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile


def expect_reject(adapter, label: str) -> None:
    try:
        adapter._require_target_profile("lineageos", "pdx235")
    except adapter.ExfatFsckError:
        return
    raise AssertionError(f"{label} unexpectedly passed")


repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    helper_root = Path(directory)
    modules = helper_root / "lib" / "modules"
    modules.mkdir(parents=True)
    (helper_root / "lib" / "__init__.py").touch()
    (modules / "__init__.py").write_text(
        "class LegacyCliModule: pass\n"
        "class MissingArgs(Exception): pass\n"
        "class ModuleRequirements:\n"
        "    def __init__(self, **kwargs): self.__dict__.update(kwargs)\n"
    )
    (helper_root / "lib" / "filesystem.py").write_text(
        "class CpioFs: pass\nclass ExtFs: pass\n"
    )
    copied_module = modules / "exfatprogs_fsck.py"
    copied_module.write_bytes((repo / "src" / "exfatprogs_fsck.py").read_bytes())
    sys.path.insert(0, str(helper_root))
    try:
        adapter = importlib.import_module("lib.modules.exfatprogs_fsck")
    finally:
        sys.path.remove(str(helper_root))

    bundle = helper_root / "corresponding-source.tar.gz"
    bundle.write_bytes(b"already-validated source bundle fixture")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    os.environ["PIXENEOS_OUTPUT_SCOPE"] = "local-unpublished"
    adapter._require_target_profile("lineageos", "pdx235")
    os.environ["PIXENEOS_OUTPUT_SCOPE"] = "published"
    os.environ.pop("EXFAT_SOURCE_BUNDLE_VERIFIED", None)
    expect_reject(adapter, "published output without verification signal")
    os.environ.update(
        EXFAT_SOURCE_BUNDLE_VERIFIED="true",
        EXFAT_SOURCE_BUNDLE_PATH=str(bundle),
        EXFAT_SOURCE_BUNDLE_SHA256=digest,
    )
    adapter._require_target_profile("lineageos", "pdx235")
    bundle.write_bytes(b"changed source bundle")
    expect_reject(adapter, "changed source bundle")
    bundle.unlink()
    bundle.symlink_to(copied_module)
    os.environ["EXFAT_SOURCE_BUNDLE_SHA256"] = hashlib.sha256(copied_module.read_bytes()).hexdigest()
    expect_reject(adapter, "symlink source bundle")
    for key in (
        "PIXENEOS_OUTPUT_SCOPE",
        "EXFAT_SOURCE_BUNDLE_VERIFIED",
        "EXFAT_SOURCE_BUNDLE_PATH",
        "EXFAT_SOURCE_BUNDLE_SHA256",
    ):
        os.environ.pop(key, None)
print("exfat source scope tests passed")
