#!/usr/bin/env python3
"""Acquire and verify immutable public Sony vendor candidates for original vold.

The two reviewed TheMuppets commits supply real proprietary inputs and their
declarations. Git identity, complete SHA-256 inventories and pinned-source
regeneration are checked. These are reproducible mirror candidates, not proof
of LineageOS publisher identity: authentic byte-identical vold remains required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "locks/lineage-vold-vendor-candidate-20261016.json"
BASELINE_LOCK = ROOT / "locks/lineage-vold-baseline-20261016.json"
VENDOR_PATHS = ("vendor/sony/pdx235", "vendor/sony/sm6375-common")
SOURCE_PATHS = ("device/sony/pdx235", "device/sony/sm6375-common", "tools/extract-utils")
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
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
MAX_FILE_BYTES = 512 * 1024 * 1024


class VendorInputError(ValueError):
    pass


# Preserve the earlier public exception name for callers.
VendorPreparationError = VendorInputError


def _environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
    })
    return env


def _git(directory: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            [
                "git", "--no-optional-locks", "-c", "core.hooksPath=" + os.devnull,
                "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
                "-c", "protocol.file.allow=never", "-C", str(directory), *args,
            ],
            env=_environment(), check=True, text=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "no Git diagnostic").strip()[-3000:]
        raise VendorInputError(f"Git operation failed in {directory}: {detail}") from exc
    return result.stdout.strip()


def _safe_path(root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in (".", "..") for p in parts):
        raise VendorInputError("unsafe path in vendor input contract")
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise VendorInputError(f"linked source or vendor path is forbidden: {relative}")
    return current


def _file_record(path: Path, relative: str) -> dict:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_FILE_BYTES:
        raise VendorInputError(f"vendor input must be a bounded regular file: {relative}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        first = stream.read(256)
        if first.startswith(LFS_POINTER):
            raise VendorInputError(f"unresolved Git LFS pointer is not a binary input: {relative}")
        digest.update(first)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise VendorInputError(f"vendor input changed while reading: {relative}")
    return {
        "path": relative,
        "mode": "100755" if before.st_mode & 0o111 else "100644",
        "size": before.st_size,
        "sha256": digest.hexdigest(),
    }


def inventory(path: Path) -> tuple[list[dict], str]:
    """Hash every worktree file, including extra/ignored files; skip only Git metadata."""
    records = []
    for parent, directories, filenames in os.walk(path, followlinks=False):
        parent = Path(parent)
        if parent == path and ".git" in directories:
            directories.remove(".git")
        for name in directories:
            candidate = parent / name
            if candidate.is_symlink():
                raise VendorInputError(f"linked directory in vendor input: {candidate}")
        for name in filenames:
            candidate = parent / name
            if parent == path and name == ".git":
                raise VendorInputError("vendor repository must have its own real Git directory")
            records.append(_file_record(candidate, candidate.relative_to(path).as_posix()))
    records.sort(key=lambda item: item["path"])
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return records, hashlib.sha256(encoded).hexdigest()


def load_vendor_contract() -> dict:
    if LOCK.is_symlink() or BASELINE_LOCK.is_symlink():
        raise VendorInputError("vendor and baseline locks must be regular checked-in files")
    contract = json.loads(LOCK.read_bytes())
    baseline = json.loads(BASELINE_LOCK.read_bytes())
    if (
        contract.get("schema_version") != 1
        or contract.get("kind") != "lineageos-pdx235-public-vendor-candidate"
        or contract.get("base_build_id") != baseline["base_build_id"]
        or contract.get("manifest_sha256") != baseline["manifest"]["sha256"]
        or contract.get("provenance", {}).get("publisher_authenticated") is not False
        or contract.get("provenance", {}).get("original_ota_vendor_identity_proven") is not False
    ):
        raise VendorInputError("vendor candidate contract does not match the original baseline")
    manifest = ROOT / baseline["manifest"]["path"]
    if manifest.is_symlink():
        raise VendorInputError("source manifest must not be linked")
    manifest_bytes = manifest.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != contract["manifest_sha256"]:
        raise VendorInputError("vendor candidate source manifest digest differs")
    expected = {
        item.get("path", item.get("name")): (item.get("name"), item.get("revision"))
        for item in ET.fromstring(manifest_bytes).findall("project")
    }
    sources = contract.get("source_projects", [])
    if [project.get("path") for project in sources] != list(SOURCE_PATHS):
        raise VendorInputError("unexpected public-source prerequisites in vendor contract")
    for project in sources:
        if expected.get(project["path"]) != (project["repository"], project["revision"]):
            raise VendorInputError("vendor generator source differs from the pinned manifest")
    hashes = contract.get("source_input_sha256", {})
    if set(hashes) != set(INPUTS) or any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in hashes.values()):
        raise VendorInputError("vendor generator source hashes are incomplete")
    projects = contract.get("vendor_projects", [])
    if [project.get("path") for project in projects] != list(VENDOR_PATHS):
        raise VendorInputError("unexpected supplemental vendor project paths")
    for project in projects:
        device = project["path"].split("/")[-1]
        repository = "TheMuppets/proprietary_vendor_sony_" + device
        if project.get("repository") != repository or project.get("url") != "https://github.com/" + repository + ".git":
            raise VendorInputError("unreviewed vendor mirror URL")
        for key in ("revision", "tree"):
            if not re.fullmatch(r"[a-f0-9]{40}", project.get(key, "")):
                raise VendorInputError("vendor mirror must use an immutable commit and tree")
        if not re.fullmatch(r"[a-f0-9]{64}", project.get("inventory_sha256", "")):
            raise VendorInputError("missing complete vendor SHA-256 inventory")
        if not isinstance(project.get("file_count"), int) or not 1 <= project["file_count"] <= 5000:
            raise VendorInputError("invalid vendor inventory file count")
        if not isinstance(project.get("content_bytes"), int) or not 1 <= project["content_bytes"] <= 2_000_000_000:
            raise VendorInputError("invalid vendor inventory size")
        declarations = project.get("declaration_sha256", {})
        if set(declarations) != {"Android.bp", "Android.mk", "BoardConfigVendor.mk", device + "-vendor.mk"}:
            raise VendorInputError("vendor declarations are not completely pinned")
        if any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in declarations.values()):
            raise VendorInputError("invalid vendor declaration digest")
    return contract


def _verify_git(path: Path, revision: str, tree: str | None = None) -> None:
    if path.is_symlink() or not path.is_dir():
        raise VendorInputError(f"missing or unsafe pinned Git project: {path}")
    if Path(_git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise VendorInputError(f"project is not its own Git worktree: {path}")
    if _git(path, "rev-parse", "HEAD") != revision:
        raise VendorInputError(f"vendor or generator source revision changed: {path}")
    if tree is not None and _git(path, "rev-parse", "HEAD^{tree}") != tree:
        raise VendorInputError(f"vendor Git tree differs from contract: {path}")
    # Status can otherwise hide modified imported source files in a resumed
    # checkout. Reject index flags that suppress ordinary worktree checks.
    tracked = _git(path, "ls-files", "-v", "-z").split("\0")
    if any(entry and (entry[0].islower() or entry[0] == "S") for entry in tracked):
        raise VendorInputError(f"pinned Git project has assume-unchanged or skip-worktree entries: {path}")
    if _git(path, "status", "--porcelain", "--untracked-files=all", "--ignored"):
        raise VendorInputError(f"vendor or generator source has unreviewed local content: {path}")


def verify_source_inputs(source: Path, contract: dict) -> dict[str, str]:
    for project in contract["source_projects"]:
        _verify_git(_safe_path(source, project["path"]), project["revision"])
    hashes = {}
    for relative, expected in contract["source_input_sha256"].items():
        record = _file_record(_safe_path(source, relative), relative)
        if record["sha256"] != expected:
            raise VendorInputError(f"pinned vendor generator input changed: {relative}")
        hashes[relative] = record["sha256"]
    return hashes


def verify_project(path: Path, project: dict) -> dict:
    if (path / ".git").is_symlink() or not (path / ".git").is_dir():
        raise VendorInputError("vendor repository must have its own real Git directory")
    _verify_git(path, project["revision"], project["tree"])
    if _git(path, "remote", "get-url", "origin") != project["url"]:
        raise VendorInputError(f"vendor repository origin differs from reviewed mirror: {project['path']}")
    records, digest = inventory(path)
    if (
        digest != project["inventory_sha256"]
        or len(records) != project["file_count"]
        or sum(record["size"] for record in records) != project["content_bytes"]
    ):
        raise VendorInputError(f"complete vendor SHA-256 inventory differs: {project['path']}")
    declarations = {record["path"]: record["sha256"] for record in records if "/" not in record["path"]}
    if declarations != project["declaration_sha256"]:
        raise VendorInputError(f"pinned vendor declarations differ: {project['path']}")
    return {
        "path": project["path"], "repository": project["repository"], "url": project["url"],
        "revision": project["revision"], "tree": project["tree"],
        "inventory_sha256": digest, "file_count": len(records),
        "content_bytes": project["content_bytes"], "declaration_sha256": declarations,
    }


def _root(source: Path) -> Path:
    if source.is_symlink() or not source.is_dir():
        raise VendorInputError("original source workspace is missing or linked")
    return source.resolve(strict=True)


def verify_vendor_checkout(source: Path) -> dict:
    """Verify candidate bytes without trusting a previous receipt or fetching anything."""
    source = _root(source)
    contract = load_vendor_contract()
    input_hashes = verify_source_inputs(source, contract)
    sony = _safe_path(source, "vendor/sony")
    if not sony.is_dir() or {p.name for p in sony.iterdir()} != {"pdx235", "sm6375-common"}:
        raise VendorInputError("Sony vendor tree contains missing or unreviewed project paths")
    projects = [verify_project(_safe_path(source, project["path"]), project)
                for project in contract["vendor_projects"]]
    return {
        "schema_version": 1,
        "kind": "pinned-public-vendor-candidate",
        "status": "ready-for-unmodified-build-probe",
        "publisher_authenticated": False,
        "original_ota_vendor_identity_proven": False,
        "byte_identical_vold_qualified": False,
        "base_build_id": contract["base_build_id"],
        "source_manifest_sha256": contract["manifest_sha256"],
        "vendor_lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
        "source_input_sha256": input_hashes,
        "projects": projects,
        "pinned_declarations_match_reviewed_regeneration": True,
        "acceptance": "An authenticated byte-identical original vold comparison is still required.",
    }


def _fetch_project(source: Path, project: dict) -> None:
    destination = _safe_path(source, project["path"])
    if destination.exists():
        verify_project(destination, project)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage outside the source tree. Failed downloads are never accepted as a
    # completed vendor checkout, and existing worktrees are never reset.
    with tempfile.TemporaryDirectory(prefix="modos-vendor-fetch-", dir=source.parent) as temp:
        checkout = Path(temp) / "checkout"
        checkout.mkdir()
        _git(checkout, "init", "--quiet")
        _git(checkout, "remote", "add", "origin", project["url"])
        _git(checkout, "-c", "fetch.fsckObjects=true", "fetch", "--depth=1",
             "--no-tags", "origin", project["revision"])
        _git(checkout, "checkout", "--detach", "--quiet", project["revision"])
        verify_project(checkout, project)
        checkout.rename(destination)


def prepare(source: Path, receipt: Path) -> dict:
    source = _root(source)
    if receipt.is_symlink():
        raise VendorInputError("vendor input receipt must not be a symlink")
    if receipt.resolve().is_relative_to(source):
        raise VendorInputError("vendor input receipt must be outside the verified source tree")
    contract = load_vendor_contract()
    verify_source_inputs(source, contract)
    sony = _safe_path(source, "vendor/sony")
    if sony.exists() and (not sony.is_dir() or any(p.name not in {"pdx235", "sm6375-common"} for p in sony.iterdir())):
        raise VendorInputError("Sony vendor tree has unreviewed project paths")
    for project in contract["vendor_projects"]:
        _fetch_project(source, project)
    # The exact pinned upstream code reads actual ELF inputs. Regeneration must
    # reproduce the already pinned mirror declarations without changing a byte.
    environment = _environment()
    environment.pop("PYTHONHOME", None)
    environment["PYTHONPATH"] = str(source / "tools/extract-utils")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [sys.executable, "-B", "-s", "device/sony/pdx235/extract-files.py", "--regenerate_makefiles"],
        cwd=source, env=environment, check=True,
    )
    result = verify_vendor_checkout(source)
    result["regeneration"] = {
        "method": "unchanged manifest-pinned extract-files.py --regenerate_makefiles",
        "exit_status": 0, "declarations_byte_identical": True,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=receipt.name + ".",
                                     dir=receipt.parent, delete=False) as output:
        temporary = Path(output.name)
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    try:
        temporary.replace(receipt)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.source_root, args.report)
        print(json.dumps({
            "status": result["status"], "projects": len(result["projects"]),
            "publisher_authenticated": False, "byte_identical_vold_qualified": False,
        }, sort_keys=True))
    except (OSError, KeyError, TypeError, ValueError, ET.ParseError, subprocess.CalledProcessError) as exc:
        print(f"Error: cannot prepare pinned Sony vendor inputs: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
