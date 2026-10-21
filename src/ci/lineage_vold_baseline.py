#!/usr/bin/env python3
"""Byte-exact, nonpublishing LineageOS vold reproduction evidence.

Unmodified vold MUST match the authenticated OTA's extracted executable
byte-for-byte. Matching ELF ABI/dependencies alone does not grant approval.
No timeout patch is loaded or applied by this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from lineage_vold import _elf_identity  # noqa: E402


class BaselineMismatch(ValueError):
    pass


def read_binary(path: Path) -> bytes:
    meta = path.lstat()
    if not stat.S_ISREG(meta.st_mode) or path.is_symlink() or not 0 < meta.st_size <= 32 * 1024 * 1024:
        raise BaselineMismatch("vold baseline input is not a bounded regular file")
    data = path.read_bytes()
    if len(data) != meta.st_size:
        raise BaselineMismatch("vold baseline input size changed")
    return data


def comparison_report(reference: bytes, built: bytes, lock: dict, source_sha256: str) -> dict:
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise BaselineMismatch("verified official OTA SHA-256 is required")
    base = lock["base_build_id"]
    if base != "lineage-24.0-20261016-nightly-pdx235-signed":
        raise BaselineMismatch("source lock refers to a different LineageOS baseline")
    # Parse both ELF files even when they differ: we need to know whether a
    # mismatch is an ABI problem as well as an exact byte mismatch.
    reference_elf = _elf_identity(reference)
    built_elf = _elf_identity(built)
    # Canonical JSON-safe shape, so a report round-trips without tuple/list
    # confusion when reviewed later by the patch gate.
    reference_elf["needed"] = list(reference_elf["needed"])
    built_elf["needed"] = list(built_elf["needed"])
    exact = reference == built
    offset = next((i for i, (a, b) in enumerate(zip(reference, built)) if a != b), None)
    if offset is None and not exact:
        offset = min(len(reference), len(built))
    return {
        "schema_version": 1,
        "kind": "unmodified-lineage-vold-exact-reproduction",
        "base_build_id": base,
        "source_ota_sha256": source_sha256,
        "manifest_sha256": lock["manifest"]["sha256"],
        "vold_revision": lock["projects"]["vold"]["revision"],
        "build_target": lock["build"]["target"],
        "reference_sha256": hashlib.sha256(reference).hexdigest(),
        "built_sha256": hashlib.sha256(built).hexdigest(),
        "reference_size": len(reference),
        "built_size": len(built),
        "elf_identity_matches": reference_elf == built_elf,
        "reference_elf": reference_elf,
        "built_elf": built_elf,
        "byte_identical": exact,
        "first_difference_offset": offset,
    }


def require_exact_match(report: dict) -> None:
    if report.get("byte_identical") is not True or report.get("elf_identity_matches") is not True:
        raise BaselineMismatch(
            "unmodified vold is NOT byte-identical to the authenticated OTA; "
            "no patched build may proceed"
        )
    if report["built_sha256"] != report["reference_sha256"]:
        raise BaselineMismatch("baseline reproduction report contradicts its SHA-256 digests")


def verify_baseline_inputs(
    report_path: Path, reference_path: Path, baseline_path: Path, lock: dict
) -> dict:
    try:
        report = json.loads(report_path.read_bytes())
    except (ValueError, UnicodeError, OSError) as exc:
        raise BaselineMismatch("reproduction evidence is missing or malformed") from exc
    if not isinstance(report, dict) or report.get("kind") != "unmodified-lineage-vold-exact-reproduction":
        raise BaselineMismatch("unexpected baseline evidence schema")
    if report.get("schema_version") != 1:
        raise BaselineMismatch("unsupported baseline evidence version")
    source_sha256 = report.get("source_ota_sha256")
    expected = comparison_report(
        read_binary(reference_path), read_binary(baseline_path), lock, source_sha256
    )
    if report != expected:
        raise BaselineMismatch("baseline evidence differs from the current binary bytes or source lock")
    require_exact_match(expected)
    return expected


def write_report(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise BaselineMismatch("baseline evidence path is a symbolic link")
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
