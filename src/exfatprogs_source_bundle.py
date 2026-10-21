"""Validation shared by build policy and the exfat image adapter."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "locks/exfatprogs-termux-v1.4.3.json"
PROVENANCE_PATH = ROOT / "locks/exfatprogs-termux-v1.4.3-provenance.json"
ADAPTER_PATH = ROOT / "src/exfatprogs_fsck.py"
LICENSE_PATH = ROOT / "LICENSE"
MAX_BUNDLE_SIZE = 2_000_000
MAX_CONTENT_SIZE = 10_000_000
REQUIRED_FILES = {
    "source/exfatprogs-1.4.3.tar.gz",
    "build/termux-packages-exfatprogs-build.sh",
    "modos/src/exfatprogs_fsck.py",
    "provenance/exfatprogs-termux-v1.4.3.lock.json",
    "provenance/exfatprogs-termux-v1.4.3.json",
    "LICENSES/exfatprogs-COPYING",
    "LICENSES/ModOS-LICENSE",
    "LICENSES/Termux-Apache-2.0.txt",
    "NOTICE.txt",
}
FRAMEWORK_REQUIRED_PATHS = {
    "build-package.sh",
    "packages/exfatprogs/build.sh",
    "repo.json",
    "scripts/properties.sh",
    "scripts/config.guess",
    "scripts/config.sub",
    "scripts/buildorder.py",
    "scripts/utils/docker/docker.sh",
    "scripts/utils/termux/package/termux_package.sh",
    "LICENSE.md",
    "packages/termux-licenses/LICENSES/Apache-2.0.txt",
}
EXPECTED_FRAMEWORK_MEMBER_COUNT = 98
NOTICE_TEXT = (
    "This bundle contains the exact locked upstream exfatprogs 1.4.3 source archive, "
    "the pinned Termux package recipe, the bounded Termux build-control source closure, "
    "the ModOS adapter, provenance records, and applicable license texts. The upstream source and recipe are "
    "identified as GPL-2.0; the ModOS adapter is project-authored AGPL-3.0-or-later. "
    "The closure does not include the full Termux repository, compiler/toolchain, or dependency "
    "archives and does not claim full binary reproducibility.\n"
)


class SourceBundleError(ValueError):
    """Raised when an exfat corresponding-source bundle is incomplete or changed."""


def _locked_artifact(artifact_id: str) -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    items = [item for item in lock["modules"][0]["artifacts"] if item["id"] == artifact_id]
    if len(items) != 1:
        raise SourceBundleError(f"source lock must contain one {artifact_id} entry")
    return items[0]


def _framework_members() -> list[dict]:
    artifact = _locked_artifact("termux-exfatprogs-build-control-closure")
    members = artifact.get("members")
    if not isinstance(members, list) or not members:
        raise SourceBundleError("Termux framework closure is missing from the lock")
    paths = [item.get("path") for item in members if isinstance(item, dict)]
    if (len(paths) != len(members) or len(paths) != len(set(paths)) or
            any(not isinstance(path, str) or path.startswith("/") or ".." in PurePosixPath(path).parts
                for path in paths)):
        raise SourceBundleError("Termux framework closure lock is malformed")
    framework_paths = {path for path in paths if path.startswith("scripts/build/")}
    other_paths = set(paths) - framework_paths
    if (len(paths) != EXPECTED_FRAMEWORK_MEMBER_COUNT or len(framework_paths) != 87 or
            other_paths != FRAMEWORK_REQUIRED_PATHS):
        raise SourceBundleError("Termux framework closure differs from the reviewed pinned file set")
    return members


def _copying_from_source(source: bytes) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
            matches = [m for m in archive.getmembers()
                       if PurePosixPath(m.name).name == "COPYING" and m.isfile()]
            if len(matches) != 1 or matches[0].size > 100_000:
                raise SourceBundleError("locked source archive must contain one bounded COPYING file")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise SourceBundleError("locked upstream COPYING file could not be read")
            return stream.read(100_001)
    except (OSError, tarfile.TarError) as exc:
        raise SourceBundleError("locked upstream source archive is not a valid tar.gz") from exc


def validate_bundle_bytes(payload: bytes, expected_sha256: str, bundle_name: str | None = None) -> dict[str, bytes]:
    if len(payload) > MAX_BUNDLE_SIZE or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise SourceBundleError("source bundle size or expected SHA-256 is invalid")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise SourceBundleError("source bundle SHA-256 differs from the expected digest")
    expected_name = f"exfatprogs-1.4.3-corresponding-source-{actual}.tar.gz"
    if bundle_name is not None and bundle_name != expected_name:
        raise SourceBundleError("source bundle filename does not match its content digest")

    framework_members = _framework_members()
    expected_members = REQUIRED_FILES | {f"termux-build/{item['path']}" for item in framework_members}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or set(names) != expected_members | {"MANIFEST.json"}:
                raise SourceBundleError("source bundle has missing, duplicate, or unexpected members")
            contents: dict[str, bytes] = {}
            total = 0
            for member in members:
                safe = PurePosixPath(member.name)
                if (not member.isfile() or safe.is_absolute() or ".." in safe.parts or
                        member.size < 0 or member.size > MAX_CONTENT_SIZE):
                    raise SourceBundleError("source bundle contains an unsafe member")
                total += member.size
                if total > MAX_CONTENT_SIZE:
                    raise SourceBundleError("source bundle expands beyond its size limit")
                stream = archive.extractfile(member)
                if stream is None:
                    raise SourceBundleError("source bundle member could not be read")
                data = stream.read(member.size + 1)
                if len(data) != member.size:
                    raise SourceBundleError("source bundle member size changed while reading")
                contents[member.name] = data
    except (OSError, tarfile.TarError) as exc:
        raise SourceBundleError("source bundle is not a valid tar.gz") from exc

    try:
        manifest = json.loads(contents.pop("MANIFEST.json"))
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceBundleError("source bundle manifest is missing or invalid") from exc
    if set(manifest) != {"schema_version", "files"} or manifest["schema_version"] != 1:
        raise SourceBundleError("source bundle manifest schema is unsupported")
    records = manifest["files"]
    if not isinstance(records, list) or len(records) != len(expected_members):
        raise SourceBundleError("source bundle manifest file list is incomplete")
    manifest_names: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
            raise SourceBundleError("source bundle manifest record is malformed")
        path = record["path"]
        if not isinstance(path, str) or path in manifest_names or path not in contents:
            raise SourceBundleError("source bundle manifest path is missing or duplicated")
        manifest_names.add(path)
        data = contents[path]
        if (record["size"] != len(data) or
                record["sha256"] != hashlib.sha256(data).hexdigest()):
            raise SourceBundleError("source bundle manifest digest mismatch")
    if manifest_names != expected_members:
        raise SourceBundleError("source bundle manifest does not cover every payload file")

    source = contents["source/exfatprogs-1.4.3.tar.gz"]
    recipe = contents["build/termux-packages-exfatprogs-build.sh"]
    for artifact_id, data in (("exfatprogs-source", source),
                              ("termux-exfatprogs-recipe", recipe)):
        lock = _locked_artifact(artifact_id)
        if len(data) != lock["size"] or hashlib.sha256(data).hexdigest() != lock["sha256"]:
            raise SourceBundleError(f"bundle does not contain the exact locked {artifact_id}")
    expected_files = {
        "modos/src/exfatprogs_fsck.py": ADAPTER_PATH.read_bytes(),
        "provenance/exfatprogs-termux-v1.4.3.lock.json": LOCK_PATH.read_bytes(),
        "provenance/exfatprogs-termux-v1.4.3.json": PROVENANCE_PATH.read_bytes(),
        "LICENSES/ModOS-LICENSE": LICENSE_PATH.read_bytes(),
        "LICENSES/exfatprogs-COPYING": _copying_from_source(source),
        "LICENSES/Termux-Apache-2.0.txt": contents[
            "termux-build/packages/termux-licenses/LICENSES/Apache-2.0.txt"
        ],
    }
    for path, expected in expected_files.items():
        if contents[path] != expected:
            raise SourceBundleError(f"bundle member differs from the reviewed repository input: {path}")
    for item in framework_members:
        path = f"termux-build/{item['path']}"
        data = contents[path]
        if (len(data) != item.get("size") or
                hashlib.sha256(data).hexdigest() != item.get("sha256")):
            raise SourceBundleError(f"bundle member differs from the pinned Termux framework source: {path}")
    notice = contents["NOTICE.txt"].decode("utf-8", errors="strict")
    if notice != NOTICE_TEXT:
        raise SourceBundleError("source bundle notice omits its framework/reproducibility limitation")
    return contents


def validate_bundle_file(path: Path, expected_sha256: str) -> None:
    try:
        metadata = path.lstat()
        if not path.is_file() or path.is_symlink() or not 0 < metadata.st_size <= MAX_BUNDLE_SIZE:
            raise SourceBundleError("source bundle must be a bounded regular file")
        with path.open("rb") as stream:
            payload = stream.read(MAX_BUNDLE_SIZE + 1)
    except OSError as exc:
        raise SourceBundleError("source bundle file cannot be read") from exc
    validate_bundle_bytes(payload, expected_sha256, path.name)
