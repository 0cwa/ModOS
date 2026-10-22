#!/usr/bin/env python3
"""Build only an UNMODIFIED, pinned LineageOS pdx235 vold and compare to OTA.

This source is intentionally separate from the timeout patch/injection code.
It never creates, signs, modifies or publishes an OTA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

from vold_baseline_compare import BaselineError, comparison, read_vold, require_identical, write_receipt

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "locks/lineage-vold-baseline-20261016.json"


class BuildError(ValueError):
    pass


def load_contract() -> tuple[dict, bytes]:
    lock = json.loads(LOCK.read_bytes())
    if (
        lock.get("schema_version") != 1
        or lock.get("kind") != "lineageos-unmodified-vold-baseline"
        or lock.get("rom_family") != "lineageos"
        or lock.get("device") != "pdx235"
        or lock.get("base_build_id") != "lineage-24.0-20261016-nightly-pdx235-signed"
        or not re.fullmatch(r"[0-9a-f]{64}", lock.get("source_ota_sha256", ""))
    ):
        raise BuildError("unmodified vold source contract is incomplete")
    manifest_path = ROOT / lock["manifest"]["path"]
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BuildError("pinned manifest is unavailable")
    manifest = manifest_path.read_bytes()
    if hashlib.sha256(manifest).hexdigest() != lock["manifest"]["sha256"]:
        raise BuildError("pinned source manifest digest differs")
    if lock["build"]["target"] != "lineage_pdx235-userdebug" or lock["build"]["command"] != "m vold":
        raise BuildError("baseline build target differs from reviewed source")
    projects = project_map(manifest)
    if len(projects) != 1243:
        raise BuildError("unexpected number of projects in source manifest")
    for project in lock["projects"].values():
        if projects.get(project["path"]) != (project["name"], project["revision"]):
            raise BuildError("source lock project differs from checked-in manifest")
    return lock, manifest


def project_map(data: bytes) -> dict[str, tuple[str, str]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise BuildError("invalid complete source manifest") from exc
    if root.tag != "manifest":
        raise BuildError("unexpected source manifest root element")
    projects = {}
    for entry in root.findall("project"):
        name = entry.get("name", "")
        path = entry.get("path", name)
        rev = entry.get("revision", "")
        if not name or not path or not re.fullmatch(r"[a-f0-9]{40}", rev):
            raise BuildError("source manifest project has no immutable revision")
        if path in projects:
            raise BuildError("source manifest repeats a project path")
        projects[path] = (name, rev)
    if not projects:
        raise BuildError("source manifest contains no projects")
    return projects


def command(args: list[str], root: Path, **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=root, check=True, **kwargs)


def verify_checkout(source: Path, manifest: bytes, *, clean: bool = False) -> None:
    repo = shutil.which("repo")
    if repo is None:
        raise BuildError("repo is required to verify complete source revisions")
    result = subprocess.run(
        [repo, "manifest", "-r"], cwd=source, check=True, capture_output=True
    )
    expected = project_map(manifest)
    if project_map(result.stdout) != expected:
        raise BuildError("source checkout does not match the 1,243 pinned revisions")
    if clean:
        for relpath, (_name, revision) in sorted(expected.items()):
            path = source / relpath
            if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(source):
                raise BuildError(f"source project is missing or unsafe: {relpath}")
            head = command(["git", "rev-parse", "HEAD"], path, capture_output=True, text=True).stdout.strip()
            if head != revision:
                raise BuildError(f"source revision changed: {relpath}")
            dirt = command(
                ["git", "status", "--porcelain", "--untracked-files=all", "--ignored"],
                path, capture_output=True, text=True,
            ).stdout.strip()
            if dirt:
                raise BuildError(f"source project has unreviewed local content: {relpath}")


def sync(source: Path, lock: dict, manifest: bytes, jobs: int) -> None:
    if source.is_symlink() or (source.exists() and (
        not source.is_dir() or any(source.iterdir())
    )):
        raise BuildError("source directory must be new or empty and not a symlink")
    repo = shutil.which("repo")
    if repo is None:
        raise BuildError("repo tool is unavailable on source build runner")
    source.mkdir(parents=True, exist_ok=True)
    manifest_commit = lock["projects"]["manifest"]["revision"]
    command([
        repo, "init", "-u", "https://github.com/LineageOS/android.git",
        "-b", manifest_commit
    ], source)
    active_manifest = source / ".repo/manifest.xml"
    if not active_manifest.exists() and not active_manifest.is_symlink():
        raise BuildError("repo init did not create the active manifest")
    if active_manifest.is_dir():
        raise BuildError("unexpected repo manifest directory")
    active_manifest.unlink()
    active_manifest.write_bytes(manifest)
    command([repo, "sync", "--no-clone-bundle", f"-j{jobs}"], source)
    verify_checkout(source, manifest, clean=True)


def build_baseline(
    source: Path, dest: Path, reference: Path, lock: dict, manifest: bytes,
    jobs: int
) -> dict:
    if source.is_symlink() or not (source / ".repo/manifest.xml").exists():
        raise BuildError("baseline source directory is missing or unsafe")
    source = source.resolve(strict=True)
    verify_checkout(source, manifest, clean=True)
    official = read_vold(reference)
    if dest.is_symlink() or (dest.exists() and (
        not dest.is_dir() or any(dest.iterdir())
    )):
        raise BuildError("baseline output directory must be new or empty")
    dest.mkdir(parents=True, exist_ok=True)
    artifact = source / lock["build"]["artifact"]
    if artifact.is_symlink():
        raise BuildError("Android vold build artifact cannot be a symbolic link")
    # Never mistake an existing build's vold for a result of this build.
    if artifact.exists():
        if not artifact.is_file():
            raise BuildError("unexpected prior vold output type")
        artifact.unlink()
    script = (
        "source build/envsetup.sh && "
        f"lunch {lock['build']['target']} && "
        f"m -j{jobs} vold"
    )
    command(["bash", "-euc", script], source)
    if not artifact.exists() or not artifact.stat().st_mode & 0o111:
        raise BuildError("source build did not produce an executable vold")
    built = read_vold(artifact)
    report = comparison(official, built, lock)
    write_receipt(dest / "baseline-report.json", report)
    require_identical(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("sync", "compare"):
        opt = sub.add_parser(name)
        opt.add_argument("--source-root", type=Path, required=True)
        opt.add_argument("--jobs", type=int, default=4)
        if name == "compare":
            opt.add_argument("--reference-vold", type=Path, required=True)
            opt.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be between 1 and 16")
    try:
        lock, manifest = load_contract()
        if args.mode == "sync":
            sync(args.source_root, lock, manifest, args.jobs)
        else:
            report = build_baseline(
                args.source_root, args.output_dir, args.reference_vold,
                lock, manifest, args.jobs,
            )
            print(json.dumps(report, sort_keys=True))
    except (OSError, BuildError, BaselineError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
