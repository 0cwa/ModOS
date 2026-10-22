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


def verified_selection(selection_file: Path | None, manifest: bytes) -> list[str] | None:
    if selection_file is None:
        return None
    if selection_file.is_symlink() or not selection_file.is_file():
        raise BuildError("dependency selection must be a real checked-in or generated JSON file")
    from vold_source_closure import verified_contract, validate_selection
    seed, projects, _baseline = verified_contract()
    if hashlib.sha256(manifest).hexdigest() != seed["manifest_sha256"]:
        raise BuildError("dependency selection does not match the authenticated manifest")
    try:
        selection = json.loads(selection_file.read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise BuildError("dependency selection is not valid JSON") from exc
    return validate_selection(selection, projects, seed)


def verify_checkout(
    source: Path, manifest: bytes, *, clean: bool = False,
    selected: list[str] | None = None,
) -> None:
    repo = shutil.which("repo")
    if repo is None:
        raise BuildError("repo is required to verify complete source revisions")
    expected = project_map(manifest)
    active_manifest = source / ".repo/manifest.xml"
    if active_manifest.is_dir() or not active_manifest.exists() or active_manifest.read_bytes() != manifest:
        raise BuildError("repo selected manifest differs from the exact official snapshot")
    if selected is None:
        # The legacy full-checkout gate remains unchanged for fully synced trees.
        result = subprocess.run(
            [repo, "manifest", "-r"], cwd=source, check=True, capture_output=True
        )
        if project_map(result.stdout) != expected:
            raise BuildError("complete source checkout does not match pinned revisions")
    else:
        if not selected or any(relpath not in expected for relpath in selected):
            raise BuildError("unreviewed project outside the immutable manifest")
    if clean:
        to_verify = expected if selected is None else {p: expected[p] for p in selected}
        for relpath, (_name, revision) in sorted(to_verify.items()):
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


def sync(
    source: Path, lock: dict, manifest: bytes, jobs: int,
    selected: list[str] | None = None, resume: bool = False,
) -> None:
    if source.is_symlink() or source.exists() and not source.is_dir():
        raise BuildError("source directory must be a real directory")
    if resume:
        if not (source / ".repo/manifest.xml").is_file():
            raise BuildError("resuming a subset requires the existing pinned repo checkout")
        if (source / ".repo/manifest.xml").read_bytes() != manifest:
            raise BuildError("resuming checkout with a different manifest is forbidden")
    elif source.exists() and any(source.iterdir()):
        raise BuildError("source directory must be new or empty; use --resume to add pinned dependencies")
    repo = shutil.which("repo")
    if repo is None:
        raise BuildError("repo tool is unavailable on source build runner")
    source.mkdir(parents=True, exist_ok=True)
    if not resume:
        manifest_commit = lock["projects"]["manifest"]["revision"]
        command([
            repo, "init", "-u", "https://github.com/LineageOS/android.git",
            "-b", manifest_commit,
        ], source)
        # Do not overwrite .repo/manifest.xml: Repo manages this file, and
        # recent versions regenerate it during subsequent sync/init operations.
        # Instead install the immutable flattened manifest in Repo's manifest
        # checkout and formally select it with -m, as supported by Repo.
        manifest_name = "modos-vold-original-20261016.xml"
        manifest_dir = source / ".repo/manifests"
        if manifest_dir.is_symlink() or not manifest_dir.is_dir():
            raise BuildError("repo manifests checkout is unavailable or unsafe")
        pinned_manifest = manifest_dir / manifest_name
        if pinned_manifest.exists() or pinned_manifest.is_symlink():
            raise BuildError("preexisting untrusted local pinned manifest")
        pinned_manifest.write_bytes(manifest)
        command([
            repo, "init", "-u", "https://github.com/LineageOS/android.git",
            "-b", manifest_commit, "-m", manifest_name
        ], source)
    active_manifest = source / ".repo/manifest.xml"
    if not active_manifest.exists() or active_manifest.is_dir():
        raise BuildError("repo did not activate the immutable full manifest")
    if active_manifest.read_bytes() != manifest:
        raise BuildError("repo's active manifest differs from the pinned source")
    # Explicit project-path sync prevents all 1,243 projects being fetched.
    # On repeated probes, keep the same pinned workspace and fetch only the
    # new members; repo does not need to re-download already pinned projects.
    command([repo, "sync", "--no-clone-bundle", f"-j{jobs}", *(selected or [])], source)
    verify_checkout(source, manifest, clean=True, selected=selected)


def build_baseline(
    source: Path, dest: Path, reference: Path, lock: dict, manifest: bytes,
    jobs: int, selected: list[str] | None = None,
) -> dict:
    if source.is_symlink() or not (source / ".repo/manifest.xml").exists():
        raise BuildError("baseline source directory is missing or unsafe")
    source = source.resolve(strict=True)
    verify_checkout(source, manifest, clean=True, selected=selected)
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
    if selected is not None:
        report["selected_source_projects"] = len(selected)
        report["selected_source_paths_sha256"] = hashlib.sha256(
            ("\n".join(sorted(selected)) + "\n").encode()
        ).hexdigest()
        report["source_checkout_scope"] = "manifest-pinned-subset"
    else:
        report["source_checkout_scope"] = "complete-manifest"
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
        opt.add_argument("--projects-file", type=Path, help="optional validated subset plan; never weakens the binary equality gate")
        if name == "sync":
            opt.add_argument("--resume", action="store_true", help="fetch missing approved projects within the same checkout")
        if name == "compare":
            opt.add_argument("--reference-vold", type=Path, required=True)
            opt.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be between 1 and 16")
    try:
        lock, manifest = load_contract()
        selected = verified_selection(args.projects_file, manifest)
        if args.mode == "sync":
            if args.resume and selected is None:
                raise BuildError("full-manifest checkout does not support incremental resume")
            sync(args.source_root, lock, manifest, args.jobs, selected, args.resume)
        else:
            report = build_baseline(
                args.source_root, args.output_dir, args.reference_vold,
                lock, manifest, args.jobs, selected,
            )
            print(json.dumps(report, sort_keys=True))
    except (OSError, BuildError, BaselineError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
