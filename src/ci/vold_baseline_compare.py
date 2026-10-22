#!/usr/bin/env python3
"""Compare a clean, unmodified LineageOS vold with the official OTA's bytes.

This file has no import or dependency on the optional patched-vold adapter.
Matching ELF metadata is not sufficient; the entire binary must match.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import struct


class BaselineError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_vold(path: Path) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= 32 * 1024 * 1024:
        raise BaselineError("vold must be a bounded regular file")
    with path.open("rb") as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    if len(data) != metadata.st_size:
        raise BaselineError("vold changed while being read")
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise BaselineError("vold is not a little-endian ELF64")
    elf_type, machine = struct.unpack_from("<HH", data, 16)
    if elf_type != 3 or machine != 183:
        raise BaselineError("vold must be an AArch64 position-independent executable")
    return data


def comparison(reference: bytes, built: bytes, contract: dict) -> dict:
    if contract.get("kind") != "lineageos-unmodified-vold-baseline":
        raise BaselineError("unrecognized unmodified-build lock")
    if not reference or not built:
        raise BaselineError("vold binary comparison inputs must not be empty")
    first_difference = next(
        (i for i, (a, b) in enumerate(zip(reference, built)) if a != b),
        None,
    )
    if first_difference is None and len(reference) != len(built):
        first_difference = min(len(reference), len(built))
    return {
        "schema_version": 1,
        "kind": "unmodified-vold-exact-comparison",
        "rom_family": "lineageos",
        "device": "pdx235",
        "base_build_id": contract["base_build_id"],
        "official_ota_sha256": contract["source_ota_sha256"],
        "manifest_sha256": contract["manifest"]["sha256"],
        "source_vold_revision": contract["projects"]["vold"]["revision"],
        "build_target": contract["build"]["target"],
        "reference_sha256": sha256(reference),
        "built_sha256": sha256(built),
        "reference_size": len(reference),
        "built_size": len(built),
        "byte_identical": reference == built,
        "first_difference_offset": first_difference,
    }


def require_identical(report: dict) -> None:
    if report.get("byte_identical") is not True or (
        report.get("reference_sha256") != report.get("built_sha256")
    ):
        raise BaselineError(
            "unmodified vold differs from authenticated OTA: "
            "no timeout patch or replacement is authorized"
        )


def write_receipt(path: Path, receipt: dict) -> None:
    if path.is_symlink():
        raise BaselineError("baseline report path must not be a symlink")
    with path.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, sort_keys=True, indent=2)
        output.write("\n")
