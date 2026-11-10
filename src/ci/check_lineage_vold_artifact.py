#!/usr/bin/env python3
"""Fail-closed provenance and publication gate for a source-built pdx235 vold.

The hash recorded by provenance.json is self-reported by the builder. A
production OTA additionally needs an independently reviewed SHA-256 pinned
in the checked-in source lock. Until one is approved, only local-unpublished
qualification builds may select this module.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lineage_vold import BASE_BUILD_ID, VoldModuleError, _artifact, _sha256  # noqa: E402
from build_lineage_vold import load_contract  # noqa: E402


def require_release_approval(lock: dict, actual_sha256: str, scope: str) -> None:
    if scope not in ("published", "local-unpublished"):
        raise VoldModuleError("vold only supports local-unpublished or approved published output")
    if scope != "published":
        return

    approved = lock.get("approved_binary_sha256", "")
    if not isinstance(approved, str) or not re.fullmatch(r"[0-9a-f]{64}", approved):
        raise VoldModuleError(
            "published vold is blocked until a separately qualified binary SHA-256 "
            "is pinned in locks/lineage-vold-exfat-timeout.json"
        )
    if actual_sha256 != approved:
        raise VoldModuleError("source-built vold binary differs from the approved publication digest")


def verify(
    artifact_dir: Path, scope: str, base_build_id: str, source_target: str
) -> str:
    lock, _manifest, _patch = load_contract()
    if (
        lock["base_build_id"] != BASE_BUILD_ID
        or source_target != BASE_BUILD_ID
        or base_build_id != BASE_BUILD_ID
    ):
        raise VoldModuleError("vold source OTA target does not match the pinned release")
    binary, _provenance = _artifact(artifact_dir)
    sha256 = _sha256(binary)
    require_release_approval(lock, sha256, scope)
    return sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--scope", required=True, choices=("published", "local-unpublished"))
    parser.add_argument("--base-build-id", required=True)
    parser.add_argument("--source-target", required=True)
    args = parser.parse_args()
    try:
        print(verify(args.artifact_dir, args.scope, args.base_build_id, args.source_target))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
