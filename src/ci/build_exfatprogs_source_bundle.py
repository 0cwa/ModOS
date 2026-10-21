#!/usr/bin/env python3
"""Build a deterministic corresponding-source bundle from locked inputs."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from exfatprogs_source_bundle import NOTICE_TEXT, validate_bundle_bytes
LOCK = ROOT / "locks/exfatprogs-termux-v1.4.3.json"
PROVENANCE = ROOT / "locks/exfatprogs-termux-v1.4.3-provenance.json"
LOCK_COPY = ROOT / "locks/exfatprogs-termux-v1.4.3.json"
ADAPTER = ROOT / "src/exfatprogs_fsck.py"
FRAMEWORK_ROOT = "termux-packages-"
MAX_TOTAL = 12_000_000


def fetch_locked(artifact: dict) -> bytes:
    url = artifact.get("immutable_url", "")
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = artifact.get("allowed_origins")
    size, expected = artifact.get("size"), artifact.get("sha256")
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.port not in (None, 443) or origin not in allowed or
            not isinstance(size, int) or not 0 < size <= MAX_TOTAL or
            not isinstance(expected, str) or len(expected) != 64):
        raise ValueError(f"unsafe or incomplete lock entry: {artifact.get('id')}")
    with urlopen(url, timeout=30) as response:
        final = urlsplit(response.geturl())
        final_origin = f"{final.scheme}://{final.netloc}"
        if final.scheme != "https" or final_origin not in allowed:
            raise ValueError(f"unexpected redirect for {artifact.get('id')}")
        data = response.read(size + 1)
    verify_bytes(artifact, data)
    return data


def verify_bytes(artifact: dict, data: bytes) -> None:
    if len(data) != artifact["size"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
        raise ValueError(f"locked size or SHA-256 mismatch: {artifact['id']}")


def _archive_entries(files: dict[str, bytes]) -> bytes:
    manifest = {
        "schema_version": 1,
        "files": [
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(files.items())
        ],
    }
    files = dict(files)
    files["MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def _extract_framework(framework: bytes, artifact: dict) -> dict[str, bytes]:
    prefix = f"{FRAMEWORK_ROOT}{artifact['version']}/"
    with tarfile.open(fileobj=io.BytesIO(framework), mode="r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        result = {}
        for item in artifact["members"]:
            archive_path = prefix + item["path"]
            member = members.get(archive_path)
            if member is None or not member.isfile() or member.size != item["size"]:
                raise ValueError(f"pinned Termux closure member is missing or unsafe: {item['path']}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"pinned Termux closure member could not be read: {item['path']}")
            data = stream.read(item["size"] + 1)
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"pinned Termux closure member hash mismatch: {item['path']}")
            result[f"termux-build/{item['path']}"] = data
    return result


def build_bundle(source: bytes, recipe: bytes, framework: bytes) -> tuple[str, bytes]:
    lock = json.loads(LOCK.read_text())
    artifacts = lock["modules"][0]["artifacts"]
    source_item = next(a for a in artifacts if a["id"] == "exfatprogs-source")
    recipe_item = next(a for a in artifacts if a["id"] == "termux-exfatprogs-recipe")
    framework_item = next(a for a in artifacts if a["id"] == "termux-exfatprogs-build-control-closure")
    verify_bytes(source_item, source)
    verify_bytes(recipe_item, recipe)
    verify_bytes(framework_item, framework)
    framework_files = _extract_framework(framework, framework_item)
    files = {
        "source/exfatprogs-1.4.3.tar.gz": source,
        "build/termux-packages-exfatprogs-build.sh": recipe,
        "modos/src/exfatprogs_fsck.py": ADAPTER.read_bytes(),
        "provenance/exfatprogs-termux-v1.4.3.lock.json": LOCK_COPY.read_bytes(),
        "provenance/exfatprogs-termux-v1.4.3.json": PROVENANCE.read_bytes(),
        "LICENSES/exfatprogs-COPYING": _extract_copying(source),
        "LICENSES/ModOS-LICENSE": (ROOT / "LICENSE").read_bytes(),
        "LICENSES/Termux-Apache-2.0.txt": framework_files[
            "termux-build/packages/termux-licenses/LICENSES/Apache-2.0.txt"
        ],
        "NOTICE.txt": NOTICE_TEXT.encode(),
    }
    files.update(framework_files)
    payload = _archive_entries(files)
    digest = hashlib.sha256(payload).hexdigest()
    name = f"exfatprogs-1.4.3-corresponding-source-{digest}.tar.gz"
    validate_bundle_bytes(payload, digest, name)
    return name, payload


def _extract_copying(source: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as tar:
        matches = [m for m in tar.getmembers() if PurePosixPath(m.name).name == "COPYING" and m.isfile()]
        if len(matches) != 1 or matches[0].size > 100_000:
            raise ValueError("upstream source archive must contain one bounded COPYING file")
        stream = tar.extractfile(matches[0])
        if stream is None:
            raise ValueError("could not read upstream COPYING file")
        return stream.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--framework-archive", type=Path,
                        help="use a local copy that must still match the immutable framework lock")
    parser.add_argument("--check-deterministic", action="store_true")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text())
    artifacts = lock["modules"][0]["artifacts"]
    source_item = next(a for a in artifacts if a["id"] == "exfatprogs-source")
    recipe_item = next(a for a in artifacts if a["id"] == "termux-exfatprogs-recipe")
    framework_item = next(a for a in artifacts if a["id"] == "termux-exfatprogs-build-control-closure")
    source, recipe = fetch_locked(source_item), fetch_locked(recipe_item)
    framework = args.framework_archive.read_bytes() if args.framework_archive else fetch_locked(framework_item)
    verify_bytes(framework_item, framework)
    name, payload = build_bundle(source, recipe, framework)
    if args.check_deterministic:
        second_name, second = build_bundle(source, recipe, framework)
        if (name, payload) != (second_name, second):
            raise SystemExit("source bundle is not deterministic")
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            members = tar.getmembers()
            manifest = json.load(tar.extractfile("MANIFEST.json"))
            actual = {m.name for m in members if m.name != "MANIFEST.json"}
            expected = {item["path"] for item in manifest["files"]}
            if actual != expected:
                raise SystemExit("source bundle manifest does not cover its files")
            for item in manifest["files"]:
                data = tar.extractfile(item["path"]).read()
                if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise SystemExit("source bundle manifest digest mismatch")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / name
    output.write_bytes(payload)
    bundle_sha = hashlib.sha256(payload).hexdigest()
    print(f"EXFAT_SOURCE_BUNDLE_PATH={output}")
    print(f"EXFAT_SOURCE_BUNDLE_NAME={name}")
    print(f"EXFAT_SOURCE_BUNDLE_SHA256={bundle_sha}")
    print("EXFAT_SOURCE_BUNDLE_VERIFIED=true")
    env_file = os.environ.get("GITHUB_ENV")
    if env_file:
        with open(env_file, "a", encoding="utf-8") as stream:
            stream.write(f"EXFAT_SOURCE_BUNDLE_PATH={output}\n")
            stream.write(f"EXFAT_SOURCE_BUNDLE_NAME={name}\n")
            stream.write(f"EXFAT_SOURCE_BUNDLE_SHA256={bundle_sha}\n")
            stream.write("EXFAT_SOURCE_BUNDLE_VERIFIED=true\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
