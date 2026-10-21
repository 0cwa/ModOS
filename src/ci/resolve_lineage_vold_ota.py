#!/usr/bin/env python3
"""Resolve an EXACT official LineageOS base OTA before baseline comparison.

Unlike the normal scheduled release provider, this selects one pinned
historical build and refuses to substitute a newer nightly.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen


ENDPOINT = "https://download.lineageos.org/api/v2/devices/pdx235/builds"
EXPECTED = "lineage-24.0-20261016-nightly-pdx235-signed.zip"
MAX_METADATA_BYTES = 8 * 1024 * 1024


class SourceSelectionError(ValueError):
    pass


def choose_official_file(builds: object, expected_sha256: str = "") -> tuple[str, str]:
    if not isinstance(builds, list):
        raise SourceSelectionError("official LineageOS API did not return a build list")
    matches = []
    for build in builds:
        if not isinstance(build, dict) or build.get("type") != "nightly":
            continue
        for artifact in build.get("files", []):
            if not isinstance(artifact, dict):
                continue
            if artifact.get("type") != "nightly" or artifact.get("filename") != EXPECTED:
                continue
            url, sha = artifact.get("url"), artifact.get("sha256")
            if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise SourceSelectionError("official base OTA lacks a valid SHA-256")
            if not isinstance(url, str):
                raise SourceSelectionError("official base OTA lacks a download URL")
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in ("download.lineageos.org", "mirrorbits.lineageos.org")
                or parsed.username or parsed.password or parsed.fragment
            ):
                raise SourceSelectionError("official base OTA URL has an unexpected origin")
            if not parsed.path.endswith("/" + EXPECTED) and not parsed.path.endswith("/" + EXPECTED + ".zip"):
                # LineageOS may use a routing URL instead of a direct ZIP path.
                # In that case the trusted API SHA-256 and actual OTA signature
                # remain decisive, but arbitrary unrelated paths are not allowed.
                if "/full/pdx235/20261016/" not in parsed.path:
                    raise SourceSelectionError("official base OTA URL has an unexpected path")
            matches.append((url, sha))
    unique = set(matches)
    if len(unique) != 1:
        raise SourceSelectionError("exact 20261016 pdx235 OTA is absent or ambiguous in official builds API")
    url, sha = unique.pop()
    if expected_sha256 and (not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or sha != expected_sha256):
        raise SourceSelectionError("official API source SHA-256 disagrees with the immutable source lock")
    return url, sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="newline-free GitHub Actions output file")
    args = parser.parse_args()
    try:
        # CLI never accepts caller-supplied API metadata. Tests exercise the
        # pure chooser with synthetic fixtures, not the production resolver.
        with urlopen(ENDPOINT, timeout=60) as response:
            payload = response.read(MAX_METADATA_BYTES + 1)
        if len(payload) > MAX_METADATA_BYTES:
            raise SourceSelectionError("official build metadata exceeds size limit")
        builds = json.loads(payload)
        contract = json.loads((Path(__file__).resolve().parents[2] / "locks/lineage-vold-exfat-timeout.json").read_text())
        url, sha = choose_official_file(builds, contract.get("source_ota_sha256", ""))
        if args.output.is_symlink():
            raise SourceSelectionError("output file is an unsafe symlink")
        with args.output.open("a", encoding="utf-8") as output:
            output.write(f"ota_url={url}\nota_sha256={sha}\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
