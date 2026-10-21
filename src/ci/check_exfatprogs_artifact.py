#!/usr/bin/env python3
"""Fetch and verify the pinned exfatprogs package used by the image adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "locks/exfatprogs-termux-v1.4.3.json"
ADAPTER_PATH = ROOT / "src/exfatprogs_fsck.py"


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    import exfatprogs_fsck as adapter

    lock = json.loads(LOCK_PATH.read_text())
    modules = lock.get("modules")
    if not isinstance(modules, list) or len(modules) != 1:
        raise SystemExit("exfatprogs lock must contain exactly one module")
    module = modules[0]
    artifacts = module.get("artifacts")
    if module.get("id") != adapter.MODULE_NAME or not isinstance(artifacts, list):
        raise SystemExit("exfatprogs lock module does not match the adapter")
    packages = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("id") == "exfatprogs-aarch64-deb"
    ]
    if len(packages) != 1:
        raise SystemExit("exfatprogs lock must contain one AArch64 package")
    package = packages[0]
    url = package.get("immutable_url")
    parsed = urlsplit(url or "")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "packages.termux.dev"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or origin not in package.get("allowed_origins", [])
        or package.get("sha256") != adapter.PACKAGE_SHA256
        or package.get("size") != adapter.PACKAGE_SIZE
    ):
        raise SystemExit("exfatprogs lock URL or digest differs from the reviewed adapter")

    with urlopen(url, timeout=30) as response:
        redirected = urlsplit(response.geturl())
        if redirected.scheme != "https" or redirected.netloc != parsed.netloc:
            raise SystemExit("exfatprogs package redirected to an unexpected host")
        payload = response.read(adapter.PACKAGE_SIZE + 1)
    if len(payload) != adapter.PACKAGE_SIZE:
        raise SystemExit("downloaded exfatprogs package has an unexpected size")
    if hashlib.sha256(payload).hexdigest() != adapter.PACKAGE_SHA256:
        raise SystemExit("downloaded exfatprogs package failed its SHA-256 check")

    with tempfile.TemporaryDirectory(prefix="exfatprogs-ci-") as temp_dir:
        package_path = Path(temp_dir) / "exfatprogs-aarch64.deb"
        package_path.write_bytes(payload)
        subprocess.run(
            [sys.executable, str(ADAPTER_PATH), "verify-package", str(package_path)],
            check=True,
        )
    print("Pinned exfatprogs AArch64 package passed adapter verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
