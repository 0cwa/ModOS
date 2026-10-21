#!/usr/bin/env python3
"""Build the pinned pdx235 vold from an already synchronized LineageOS tree.

The complete LineageOS source checkout is large and is intentionally not
downloaded implicitly. Prepare it with the pinned official build manifest in
an isolated build workspace, then pass that workspace to this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "locks/lineage-vold-exfat-timeout.json"
MANIFEST_PATH = ROOT / "locks/lineage-pdx235-20261016-build-manifest.xml"
ADAPTER_PATH = ROOT / "src/lineage_vold.py"


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_contract() -> tuple[dict, bytes, bytes]:
    lock_bytes = LOCK_PATH.read_bytes()
    manifest_bytes = MANIFEST_PATH.read_bytes()
    patch_path = ROOT / json.loads(lock_bytes)["patch"]["path"]
    patch_bytes = patch_path.read_bytes()
    lock = json.loads(lock_bytes)
    if digest(manifest_bytes) != lock["manifest"]["sha256"]:
        raise BuildError("checked-in LineageOS manifest failed its SHA-256 check")
    if digest(patch_bytes) != lock["patch"]["sha256"]:
        raise BuildError("pinned vold timeout patch failed its SHA-256 check")
    return lock, manifest_bytes, patch_bytes


def project_map(xml_bytes: bytes) -> dict[str, tuple[str, str]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise BuildError("LineageOS source manifest is malformed XML") from exc
    if root.tag != "manifest":
        raise BuildError("LineageOS source manifest has an unexpected root element")
    result: dict[str, tuple[str, str]] = {}
    for project in root.findall("project"):
        name, path, revision = (
            project.get("name", ""),
            project.get("path", project.get("name", "")),
            project.get("revision", ""),
        )
        if not name or not path or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise BuildError("source manifest contains an unpinned project")
        if path in result:
            raise BuildError(f"source manifest repeats project path {path}")
        result[path] = (name, revision)
    if not result:
        raise BuildError("source manifest contains no projects")
    return result


def checked_source_manifest(source_root: Path, expected: dict) -> None:
    repo = shutil.which("repo")
    if not repo:
        raise BuildError("repo tool is required to verify the synchronized LineageOS source tree")
    completed = subprocess.run(
        [repo, "manifest", "-r"],
        cwd=source_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise BuildError("repo manifest -r failed: " + completed.stderr.decode("utf-8", "replace")[-2000:])
    actual = project_map(completed.stdout)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        changed = sorted(path for path in set(expected) & set(actual) if expected[path] != actual[path])
        details = []
        if missing:
            details.append("missing=" + ",".join(missing[:8]))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected[:8]))
        if changed:
            details.append("revision-mismatch=" + ",".join(changed[:8]))
        raise BuildError("synchronized source does not match the complete pinned manifest (" + "; ".join(details) + ")")


def command_sync(source_root: Path, jobs: int | None) -> None:
    lock, manifest_bytes, _ = load_contract()
    repo = shutil.which("repo")
    if not repo:
        raise BuildError("repo tool is required to synchronize LineageOS source")
    source_root = source_root.expanduser().resolve()
    if source_root.exists() and (not source_root.is_dir() or any(source_root.iterdir())):
        raise BuildError("source root must be a new or empty directory")
    source_root.mkdir(parents=True, exist_ok=True)
    manifest_project = lock["projects"]["manifest"]
    subprocess.run(
        [
            repo,
            "init",
            "-u",
            "https://github.com/LineageOS/android.git",
            "-b",
            manifest_project["revision"],
        ],
        cwd=source_root,
        check=True,
    )
    active_manifest = source_root / ".repo/manifest.xml"
    if not active_manifest.exists() and not active_manifest.is_symlink():
        raise BuildError("repo init did not create the active manifest path")
    if active_manifest.is_dir():
        raise BuildError("repo active manifest path unexpectedly resolves to a directory")
    active_manifest.unlink()
    active_manifest.write_bytes(manifest_bytes)
    sync_args = [repo, "sync", "--no-clone-bundle"]
    if jobs:
        sync_args.append(f"-j{jobs}")
    subprocess.run(sync_args, cwd=source_root, check=True)
    expected = project_map(manifest_bytes)
    checked_source_manifest(source_root, expected)


def git(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise BuildError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def verify_source_checkouts_clean(source_root: Path, expected: dict) -> None:
    """Ensure pinned revisions are not overridden by local edits or extra files.

    A repo manifest pins Git HEADs; it does not certify the working-tree bytes.
    An Android binary built with dirty dependencies must never be presented as
    built from the exact official manifest.
    """
    source_root = source_root.resolve(strict=True)
    for relative_path, (_name, revision) in sorted(expected.items()):
        project_path = source_root / relative_path
        if (
            project_path.is_symlink()
            or not project_path.is_dir()
            or not project_path.resolve().is_relative_to(source_root)
        ):
            raise BuildError(f"pinned source project is missing or unsafe: {relative_path}")
        if git(project_path, "rev-parse", "HEAD") != revision:
            raise BuildError(f"pinned source revision differs at: {relative_path}")
        # --ignored includes files hidden by .gitignore that could still be
        # read by the Android compiler; require a virgin source workspace.
        if git(project_path, "status", "--porcelain", "--ignored", "--untracked-files=all"):
            raise BuildError(f"pinned source project is dirty: {relative_path}")


def ensure_regular_executable(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BuildError(f"expected build output is unavailable: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > 32 * 1024 * 1024:
        raise BuildError("built vold is not a bounded regular file")
    if not metadata.st_mode & 0o111:
        raise BuildError("built vold is not executable")
    return path.read_bytes()


def command_build(source_root: Path, output_dir: Path, jobs: int | None) -> None:
    lock, manifest_bytes, patch_bytes = load_contract()
    source_root = source_root.resolve(strict=True)
    if not (source_root / ".repo/manifest.xml").exists():
        raise BuildError("source root is not a repo checkout")
    expected = project_map(manifest_bytes)
    for key in ("manifest", "vold", "device"):
        project = lock["projects"][key]
        if expected.get(project["path"]) != (project["name"], project["revision"]):
            raise BuildError(f"lock and official manifest disagree for project {key}")
    checked_source_manifest(source_root, expected)
    verify_source_checkouts_clean(source_root, expected)

    vold_project = lock["projects"]["vold"]
    vold_root = source_root / vold_project["path"]
    if git(vold_root, "rev-parse", "HEAD") != vold_project["revision"]:
        raise BuildError("android_system_vold checkout is not the pinned revision")
    if git(vold_root, "status", "--porcelain"):
        raise BuildError("android_system_vold checkout is dirty; use an isolated source workspace")

    # Resolve only after checking the caller-provided path: resolve() erases
    # whether its final component was an unsafe symbolic link.
    output_dir = output_dir.expanduser()
    if output_dir.is_symlink():
        raise BuildError("artifact output directory must not be a symlink")
    output_dir = output_dir.resolve()
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise BuildError("artifact output directory must be absent or empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_artifact = source_root / lock["build"]["artifact"]

    patch_path = ROOT / lock["patch"]["path"]
    check = subprocess.run(["git", "-C", str(vold_root), "apply", "--check", str(patch_path)], check=False)
    if check.returncode:
        raise BuildError("timeout patch does not apply cleanly to the pinned vold source")
    subprocess.run(["git", "-C", str(vold_root), "apply", str(patch_path)], check=True)
    try:
        jobs_arg = f"-j{jobs}" if jobs else ""
        shell_command = (
            "source build/envsetup.sh && "
            f"lunch {lock['build']['target']} && "
            f"m {jobs_arg} vold".replace("  ", " ")
        )
        completed = subprocess.run(
            ["bash", "-euc", shell_command], cwd=source_root, check=False
        )
        if completed.returncode:
            raise BuildError("LineageOS vold build failed")
        binary = ensure_regular_executable(output_artifact)
        # Reuse the adapter's independent ELF parser for the artifact contract.
        sys.path.insert(0, str(ROOT / "src"))
        from lineage_vold import _elf_identity
        identity = _elf_identity(binary)
        if identity["machine"] != lock["build"]["elf_machine"] or identity["interpreter"] != lock["build"]["interpreter"]:
            raise BuildError("built vold has an unexpected target ABI")
        shutil.copyfile(output_artifact, output_dir / "vold")
        os.chmod(output_dir / "vold", 0o755)
        provenance = {
            "schema_version": 1,
            "module": lock["module"],
            "base_build_id": lock["base_build_id"],
            "manifest_sha256": lock["manifest"]["sha256"],
            "vold_revision": vold_project["revision"],
            "device_revision": lock["projects"]["device"]["revision"],
            "patch_sha256": lock["patch"]["sha256"],
            "build_target": lock["build"]["target"],
            "build_command": lock["build"]["command"],
            "artifact_path": lock["build"]["artifact"],
            "binary_sha256": digest(binary),
            "elf": {
                "elf_class": identity["elf_class"],
                "machine": identity["machine"],
                "interpreter": identity["interpreter"],
                "needed": list(identity["needed"]),
            },
        }
        (output_dir / "provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(provenance, sort_keys=True))
    finally:
        revert = subprocess.run(
            ["git", "-C", str(vold_root), "apply", "--reverse", str(patch_path)],
            check=False,
        )
        if revert.returncode:
            raise BuildError("failed to restore the pinned vold checkout after the build")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="explicitly download the large exact-revision source tree")
    sync.add_argument("--source-root", type=Path, required=True, help="new or empty source checkout directory")
    sync.add_argument("--jobs", type=int, help="optional repo sync parallelism")
    build = subparsers.add_parser("build", help="build vold from an already synchronized exact-revision tree")
    build.add_argument("--source-root", type=Path, required=True, help="complete, clean checkout synced to the locked official build manifest")
    build.add_argument("--output-dir", type=Path, required=True, help="new or empty artifact directory")
    build.add_argument("--jobs", type=int, help="optional parallel build job count")
    args = parser.parse_args()
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be positive")
    try:
        if args.command == "sync":
            command_sync(args.source_root, args.jobs)
        else:
            command_build(args.source_root, args.output_dir, args.jobs)
    except (BuildError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
