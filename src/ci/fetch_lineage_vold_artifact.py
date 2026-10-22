#!/usr/bin/env python3
"""Fetch only an independently approved vold binary and provenance release pair.

The release is a transport, not a source of trust: both asset digests are
reviewed and pinned in the checked-in lock, and the regular-file, ELF,
manifest and provenance checks run again after download. This script never
downloads unapproved binaries or initiates a source build.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from build_lineage_vold import load_contract
from check_lineage_vold_artifact import verify
from lineage_vold import BASE_BUILD_ID, _read_regular_file


class ArtifactFetchError(ValueError):
    pass


def distribution_contract(lock: dict) -> dict:
    config = lock.get("artifact_distribution")
    if not isinstance(config, dict):
        raise ArtifactFetchError("vold distribution contract is not configured")
    if config.get("repository") != "0cwa/ModOS":
        raise ArtifactFetchError("vold distribution repository is unexpected")
    if config.get("binary_asset") != "vold" or config.get("provenance_asset") != "provenance.json":
        raise ArtifactFetchError("vold release assets must have the reviewed exact names")
    tag = config.get("release_tag")
    if not isinstance(tag, str) or not re.fullmatch(r"vold-pdx235-20261016-[a-zA-Z0-9_.-]+", tag):
        raise ArtifactFetchError("vold staging release tag is not approved")
    for key, digest in (
        ("approved_binary_sha256", lock.get("approved_binary_sha256")),
        ("provenance_sha256", config.get("provenance_sha256")),
    ):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ArtifactFetchError(f"vold {key} has not been reviewed and pinned")
    return config


def fetch(output_dir: Path) -> str:
    lock, _manifest, _patch = load_contract()
    config = distribution_contract(lock)
    if lock.get("base_build_id") != BASE_BUILD_ID:
        raise ArtifactFetchError("vold staging release is not for the pinned base build")

    if output_dir.is_symlink() or (output_dir.exists() and (
        not output_dir.is_dir() or any(output_dir.iterdir())
    )):
        raise ArtifactFetchError("vold artifact destination must be absent or an empty real directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "gh", "release", "download", config["release_tag"],
                "--repo", config["repository"],
                "--pattern", config["binary_asset"],
                "--pattern", config["provenance_asset"],
                "--dir", str(output_dir),
            ],
            check=True,
        )
        metadata = _read_regular_file(output_dir / "provenance.json", 128 * 1024)
        if hashlib.sha256(metadata).hexdigest() != config["provenance_sha256"]:
            raise ArtifactFetchError("downloaded vold provenance digest does not match the source lock")
        return verify(output_dir, "published", BASE_BUILD_ID, BASE_BUILD_ID)
    except (OSError, ValueError, subprocess.SubprocessError):
        # A partial download cannot be reused by another build.
        shutil.rmtree(output_dir)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(fetch(args.output_dir))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
