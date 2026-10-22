#!/usr/bin/env python3
"""Report the proprietary inputs blocking pinned Sony makefile preparation.

At the locked extract-utils revision, --regenerate_makefiles skips extraction
but still opens proprietary ELF files to discover architecture and dependencies.
It cannot regenerate the Sony makefiles from public file lists alone. Until an
authenticated vendor-input contract exists, stop before running that generator
or writing partial vendor declarations. This diagnostic never qualifies vold.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys

from build_original_vold import VENDOR_PREREQUISITE_PROJECTS, load_contract, verify_checkout


class VendorPreparationError(ValueError):
    pass


INPUTS = (
    "device/sony/pdx235/extract-files.py",
    "device/sony/pdx235/proprietary-files.txt",
    "device/sony/sm6375-common/extract-files.py",
    "device/sony/sm6375-common/proprietary-files.txt",
    "tools/extract-utils/extract_utils/main.py",
    "tools/extract-utils/extract_utils/module.py",
    "tools/extract-utils/extract_utils/makefiles.py",
    "tools/extract-utils/extract_utils/elf.py",
)
# This is the first absent ELF opened in a replay of the unchanged, exact
# pinned scripts and public lists. It is an example, not a complete blob set.
REQUIRED_ELF_EXAMPLE = (
    "vendor/sony/pdx235/proprietary/"
    "vendor/lib64/camera/com.qti.eeprom.hi846_back_eeprom.so"
)
EXTRACT_UTILS_REVISION = "ff6589c6b0a4a00c592e98940e9e6b6a50c76569"
EVIDENCE_BASE = (
    "https://github.com/LineageOS/android_tools_extract-utils/blob/"
    + EXTRACT_UTILS_REVISION
)


def safe_file(path: Path, max_bytes: int = 4_000_000) -> bytes:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or not 0 <= st.st_size <= max_bytes:
        raise VendorPreparationError(f"diagnostic receipt or source is unsafe: {path}")
    content = path.read_bytes()
    if len(content) != st.st_size:
        raise VendorPreparationError(f"diagnostic receipt or source changed while reading: {path}")
    return content


def blocked_report(source: Path, input_hashes: dict[str, str]) -> dict:
    for path in (source / "vendor", source / "vendor/sony"):
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise VendorPreparationError("Sony vendor output root is unsafe")
    sony_root = source / "vendor/sony"
    preexisting = sony_root.exists() and any(sony_root.iterdir())
    return {
        "schema_version": 1,
        "kind": "blocked-unqualified-proprietary-vendor-inputs",
        "status": "blocked",
        "does_not_extract_blobs": True,
        "does_not_qualify_original_vold": True,
        "makefiles_generated": False,
        "preexisting_vendor_tree_unqualified": preexisting,
        "input_sha256": input_hashes,
        "required_input_example": REQUIRED_ELF_EXAMPLE,
        "required_input_example_is_complete_list": False,
        "reason": (
            "The pinned --regenerate_makefiles path skips extraction, but "
            "write_elfs_package still opens proprietary ELF files for machine, "
            "bitness and DT_NEEDED metadata. Public source lists are insufficient."
        ),
        "required_next_step": (
            "Authenticate and pin the required proprietary vendor inputs and "
            "their build provenance in a reviewed contract before enabling "
            "vendor preparation. Preexisting files and synthetic receipts "
            "are not accepted as provenance."
        ),
        "upstream_evidence": [
            EVIDENCE_BASE + "/extract_utils/main.py#L182-L219",
            EVIDENCE_BASE + "/extract_utils/makefiles.py#L256-L271",
            EVIDENCE_BASE + "/extract_utils/elf.py#L38-L57",
        ],
        "public_source_replay": {
            "scope": "Review-time replay of pinned public source, not performed by this diagnostic",
            "command": "python3 -B device/sony/pdx235/extract-files.py --regenerate_makefiles",
            "exit_status": 1,
            "exception": "FileNotFoundError",
            "first_missing_file": REQUIRED_ELF_EXAMPLE,
            "original_scripts_modified": False,
        },
    }


def prepare(source: Path, receipt: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise VendorPreparationError("original source workspace is missing or linked")
    source = source.resolve(strict=True)
    _lock, manifest = load_contract()
    verify_checkout(source, manifest, clean=True, selected=list(VENDOR_PREREQUISITE_PROJECTS))
    hashes = {path: hashlib.sha256(safe_file(source / path)).hexdigest() for path in INPUTS}
    current = blocked_report(source, hashes)
    if receipt.is_symlink():
        raise VendorPreparationError("vendor diagnostic receipt must not be a symlink")
    if receipt.exists():
        if json.loads(safe_file(receipt)) != current:
            raise VendorPreparationError("existing vendor receipt is not the current blocked-input diagnostic")
    else:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as output:
            json.dump(current, output, indent=2, sort_keys=True)
            output.write("\n")
    raise VendorPreparationError(
        "pinned Sony makefile regeneration requires authenticated proprietary "
        f"ELF inputs (for example {REQUIRED_ELF_EXAMPLE}); no vendor-input "
        f"provenance contract exists. Blocked-input report: {receipt}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.source_root, args.report)
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: cannot prepare pinned Sony makefiles: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
