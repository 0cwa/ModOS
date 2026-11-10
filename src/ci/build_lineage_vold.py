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
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

import build_original_vold as original


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "locks/lineage-vold-exfat-timeout.json"
MANIFEST_PATH = ROOT / "locks/lineage-pdx235-20261016-build-manifest.xml"
ADAPTER_PATH = ROOT / "src/lineage_vold.py"


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_contract(*, verify_patch: bool = True) -> tuple[dict, bytes, bytes]:
    lock_bytes = LOCK_PATH.read_bytes()
    manifest_bytes = MANIFEST_PATH.read_bytes()
    lock = json.loads(lock_bytes)
    if digest(manifest_bytes) != lock["manifest"]["sha256"]:
        raise BuildError("checked-in LineageOS manifest failed its SHA-256 check")
    # The unmodified reproduction phase must not even open the timeout patch.
    # Only a later explicitly requested patched build verifies and applies it.
    patch_bytes = b""
    if verify_patch:
        patch_path = ROOT / lock["patch"]["path"]
        patch_bytes = patch_path.read_bytes()
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


def original_build_contract(lock: dict, manifest: bytes) -> dict:
    """Share the independently reviewed original-build inputs and safeguards."""
    baseline_lock, baseline_manifest = original.load_contract()
    identity_fields = ("rom_family", "device", "base_build_id", "source_ota_sha256", "projects")
    if baseline_manifest != manifest or any(
        baseline_lock.get(key) != lock.get(key) for key in identity_fields
    ):
        raise BuildError("patched-vold and original-build source contracts disagree")
    for key in ("target", "command", "artifact", "elf_class", "elf_machine", "interpreter"):
        if baseline_lock["build"].get(key) != lock["build"].get(key):
            raise BuildError(f"patched-vold and original-build contracts disagree for {key}")
    return baseline_lock


def command_sync(source_root: Path, jobs: int | None) -> None:
    lock, manifest, _ = load_contract(verify_patch=False)
    baseline_lock = original_build_contract(lock, manifest)
    # Use Repo's supported -m selection and verified checkout path. Directly
    # overwriting .repo/manifest.xml is not stable across Repo sync/init.
    original.sync(source_root, baseline_lock, manifest, jobs or 4)


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


def command_baseline(
    source_root: Path, output_dir: Path, reference_vold: Path,
    source_ota_sha256: str, jobs: int | None,
) -> dict:
    """Build UNMODIFIED vold and compare every byte against the official OTA.

    The timeout patch is never applied by this command. Even a dependency- or
    ABI-compatible binary is rejected when any byte differs.
    """
    from lineage_vold_baseline import (
        BaselineMismatch, comparison_report, read_binary, require_exact_match, write_report,
    )
    lock, manifest_bytes, _patch_bytes = load_contract(verify_patch=False)
    baseline_lock = original_build_contract(lock, manifest_bytes)
    if source_ota_sha256 != lock["source_ota_sha256"]:
        raise BuildError("unmodified reference OTA differs from the pinned authenticated source SHA-256")
    reference = read_binary(reference_vold)
    if output_dir.is_symlink() or (output_dir.exists() and (
        not output_dir.is_dir() or any(output_dir.iterdir())
    )):
        raise BuildError("baseline output directory must be new or empty and not a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    # This path verifies the vendor candidates, release configuration, complete
    # source workspace, and fresh output before and after the original build.
    output_artifact = original.compile_original_vold(
        source_root, baseline_lock, manifest_bytes, jobs or 4
    )

    built = ensure_regular_executable(output_artifact)
    try:
        report = comparison_report(reference, built, lock, source_ota_sha256)
    except (ValueError, OSError) as exc:
        raise BuildError(f"baseline binary validation failed: {exc}") from exc

    # Preserve diagnostic hashes on a mismatch, but never grant a pass.
    write_report(output_dir / "baseline-report.json", report)
    try:
        require_exact_match(report)
    except BaselineMismatch as exc:
        raise BuildError(str(exc)) from exc
    (output_dir / "baseline-vold").write_bytes(built)
    print(json.dumps(report, sort_keys=True))
    return report


def command_build(
    source_root: Path, output_dir: Path, jobs: int | None,
    baseline_report: Path, baseline_binary: Path, reference_vold: Path,
) -> None:
    lock, manifest_bytes, _patch_bytes = load_contract()
    # A patched binary must NOT be built before a byte-exact unmodified vold
    # has been reproduced and checked against the authenticated source OTA.
    from lineage_vold_baseline import verify_baseline_inputs
    try:
        baseline = verify_baseline_inputs(
            baseline_report, reference_vold, baseline_binary, lock
        )
    except (ValueError, OSError) as exc:
        raise BuildError(f"patched vold is gated on exact baseline reproduction: {exc}") from exc
    baseline_lock = original_build_contract(lock, manifest_bytes)
    # Prepare and validate while every source project is still pristine. This
    # refuses missing vendor inputs, mutable lunch/roomservice downloads,
    # dependency bypasses, output overrides, and stale output binaries.
    source_root, output_artifact, shell_command, environment = original.prepare_vold_build(
        source_root, baseline_lock, manifest_bytes, jobs or 4
    )
    vold_project = lock["projects"]["vold"]
    vold_root = source_root / vold_project["path"]

    # Resolve only after checking the caller-provided path: resolve() erases
    # whether its final component was an unsafe symbolic link.
    output_dir = output_dir.expanduser()
    if output_dir.is_symlink():
        raise BuildError("artifact output directory must not be a symlink")
    output_dir = output_dir.resolve()
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise BuildError("artifact output directory must be absent or empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    patch_path = ROOT / lock["patch"]["path"]
    check = subprocess.run(["git", "-C", str(vold_root), "apply", "--check", str(patch_path)], check=False)
    if check.returncode:
        raise BuildError("timeout patch does not apply cleanly to the pinned vold source")
    subprocess.run(["git", "-C", str(vold_root), "apply", str(patch_path)], check=True)
    try:
        completed = subprocess.run(
            ["bash", "-ec", shell_command], cwd=source_root, env=environment, check=False
        )
        if completed.returncode:
            raise BuildError("LineageOS vold build failed")
        binary = ensure_regular_executable(output_artifact)
    finally:
        revert = subprocess.run(
            ["git", "-C", str(vold_root), "apply", "--reverse", str(patch_path)],
            check=False,
        )
        if revert.returncode:
            raise BuildError("failed to restore the pinned vold checkout after the build")

    original.verify_checkout(source_root, manifest_bytes, clean=True)
    original.verify_source_workspace(source_root, manifest_bytes, None, require_vendor=True)
    # Reuse the adapter's independent ELF parser for the artifact contract.
    sys.path.insert(0, str(ROOT / "src"))
    from lineage_vold import _elf_identity
    identity = _elf_identity(binary)
    if identity["machine"] != lock["build"]["elf_machine"] or identity["interpreter"] != lock["build"]["interpreter"]:
        raise BuildError("built vold has an unexpected target ABI")
    (output_dir / "vold").write_bytes(binary)
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
        "baseline_reference_sha256": baseline["reference_sha256"],
        "baseline_source_ota_sha256": baseline["source_ota_sha256"],
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="explicitly download the large exact-revision source tree")
    sync.add_argument("--source-root", type=Path, required=True, help="new or empty source checkout directory")
    sync.add_argument("--jobs", type=int, help="optional repo sync parallelism")
    baseline = subparsers.add_parser(
        "baseline", help="build unmodified vold and require exact OTA byte identity"
    )
    baseline.add_argument("--source-root", type=Path, required=True)
    baseline.add_argument("--output-dir", type=Path, required=True)
    baseline.add_argument("--reference-vold", type=Path, required=True)
    baseline.add_argument("--source-ota-sha256", required=True)
    baseline.add_argument("--jobs", type=int)
    build = subparsers.add_parser(
        "build", help="build patched vold ONLY after exact baseline proof"
    )
    build.add_argument("--source-root", type=Path, required=True, help="complete, clean checkout synced to the locked official build manifest")
    build.add_argument("--output-dir", type=Path, required=True, help="new or empty artifact directory")
    build.add_argument("--jobs", type=int, help="optional parallel build job count")
    build.add_argument("--baseline-report", type=Path, required=True)
    build.add_argument("--baseline-binary", type=Path, required=True)
    build.add_argument("--reference-vold", type=Path, required=True)
    args = parser.parse_args()
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be positive")
    try:
        if args.command == "sync":
            command_sync(args.source_root, args.jobs)
        elif args.command == "baseline":
            command_baseline(
                args.source_root, args.output_dir, args.reference_vold,
                args.source_ota_sha256, args.jobs,
            )
        else:
            command_build(
                args.source_root, args.output_dir, args.jobs, args.baseline_report,
                args.baseline_binary, args.reference_vold,
            )
    except (BuildError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
