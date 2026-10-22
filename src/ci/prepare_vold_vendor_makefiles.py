#!/usr/bin/env python3
"""Generate missing Sony product makefiles from pinned public extraction lists.

This is a diagnostic build-probe convenience, not a proprietary-blob source.
LineageOS extract-utils --regenerate_makefiles explicitly skips all copying,
source image extraction, and blob fixups. The generated makefiles do NOT
establish provenance for missing proprietary binaries or runtime behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from build_original_vold import load_contract, verify_checkout


class VendorPreparationError(ValueError):
    pass


INPUTS = (
    "device/sony/pdx235/extract-files.py",
    "device/sony/pdx235/proprietary-files.txt",
    "device/sony/sm6375-common/extract-files.py",
    "device/sony/sm6375-common/proprietary-files.txt",
    "tools/extract-utils/extract_utils/main.py",
)
REQUIRED = (
    "vendor/sony/pdx235/pdx235-vendor.mk",
    "vendor/sony/pdx235/BoardConfigVendor.mk",
    "vendor/sony/pdx235/Android.bp",
    "vendor/sony/sm6375-common/sm6375-common-vendor.mk",
    "vendor/sony/sm6375-common/BoardConfigVendor.mk",
    "vendor/sony/sm6375-common/Android.bp",
)


def safe_file(path: Path, max_bytes: int = 4_000_000) -> bytes:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or not 0 <= st.st_size <= max_bytes:
        raise VendorPreparationError(f"generated makefile or source is unsafe: {path}")
    content = path.read_bytes()
    if len(content) != st.st_size:
        raise VendorPreparationError(f"generated makefile changed while reading: {path}")
    return content


def report(source: Path, input_hashes: dict[str, str]) -> dict:
    outputs = {}
    for path in REQUIRED:
        outputs[path] = hashlib.sha256(safe_file(source / path)).hexdigest()
    sony_root = source / "vendor/sony"
    for path in sony_root.rglob("*"):
        if path.is_symlink():
            raise VendorPreparationError("generated Sony vendor source contains an unsafe link")
        if not path.is_file():
            continue
        if path.suffix.lower() in (".so", ".apk", ".jar", ".img", ".bin"):
            raise VendorPreparationError("unexpected binary blob from makefiles-only mode")
        safe_file(path)
    return {
        "schema_version": 1,
        "kind": "synthetic-probe-vendor-makefiles-not-publisher-provenance",
        "method": "pinned device extract-files.py --regenerate_makefiles",
        "does_not_extract_blobs": True,
        "does_not_qualify_original_vold": True,
        "input_sha256": input_hashes,
        "generated_makefile_sha256": outputs,
        "warning": "Proprietary blobs remain absent. A real build may still fail; never treat these generated makefiles as verified official vendor blobs.",
    }


def prepare(source: Path, receipt: Path) -> dict:
    if source.is_symlink() or not source.is_dir():
        raise VendorPreparationError("original source workspace is missing or linked")
    source = source.resolve(strict=True)
    lock, manifest = load_contract()
    # Probe only already-synced immutable projects; no unpinned tools/scripts.
    required_projects = [
        "device/sony/pdx235", "device/sony/sm6375-common", "tools/extract-utils",
    ]
    verify_checkout(source, manifest, clean=True, selected=required_projects)
    hashes = {path: hashlib.sha256(safe_file(source / path)).hexdigest() for path in INPUTS}
    sony_root = source / "vendor/sony"
    if sony_root.is_symlink() or sony_root.exists() and not sony_root.is_dir():
        raise VendorPreparationError("Sony vendor output root is unsafe")

    if receipt.is_symlink():
        raise VendorPreparationError("vendor makefile receipt must not be a symlink")
    if receipt.exists():
        saved = json.loads(safe_file(receipt))
        current = report(source, hashes)
        if current != saved:
            raise VendorPreparationError("cached generated vendor makefiles differ from the pinned source receipt")
        return current

    if sony_root.exists() and any(sony_root.iterdir()):
        raise VendorPreparationError("unapproved preexisting Sony vendor workspace")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source / "tools/extract-utils")
    subprocess.run(
        [sys.executable, "device/sony/pdx235/extract-files.py", "--regenerate_makefiles"],
        cwd=source,
        env=environment,
        check=True,
    )
    current = report(source, hashes)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("x", encoding="utf-8") as output:
        json.dump(current, output, indent=2, sort_keys=True)
        output.write("\n")
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.source_root, args.report)
        print(json.dumps({"kind": result["kind"], "output_count": len(REQUIRED)}, sort_keys=True))
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: cannot produce pinned Sony makefiles: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
