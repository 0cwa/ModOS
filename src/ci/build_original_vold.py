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
MANIFEST_NAME = "modos-vold-original-20261016.xml"
VENDOR_PREREQUISITE_PROJECTS = (
    "device/sony/pdx235", "device/sony/sm6375-common", "tools/extract-utils",
)


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
    build = lock["build"]
    if (
        (build.get("product"), build.get("release"), build.get("variant"))
        != ("lineage_pdx235", "cp2a", "userdebug")
        or build.get("setup_command") != "_lunch_meat lineage_pdx235 cp2a userdebug"
        or build.get("release_config", {}).get("path") != "vendor/lineage/vars/aosp_target_release"
        or build.get("release_config", {}).get("sha256")
        != "258eaaeb1fc743ee5fd3a0ade38905be3bbfe3d2624d7fd477dd966a68d67eb6"
    ):
        raise BuildError("baseline product/release/variant differs from pinned LineageOS setup")
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


def verify_active_manifest(source: Path, manifest: bytes) -> None:
    """Validate Repo's selected file, including its modern include wrapper.

    Repo's Link() writes .repo/manifest.xml as a single include, not a copy
    of the selected manifest. Check that include without permitting another
    manifest, a local override, or a linked file outside this checkout.
    """
    repo_dir = source / ".repo"
    if repo_dir.is_symlink() or not repo_dir.is_dir():
        raise BuildError("repo metadata directory is missing or unsafe")
    for local in (repo_dir / "local_manifest.xml", repo_dir / "local_manifests"):
        if local.is_symlink() or (local.exists() and (
            not local.is_dir() or any(local.iterdir())
        )):
            raise BuildError("local manifest overrides are forbidden for the pinned baseline")
    active = repo_dir / "manifest.xml"
    if not active.is_file():
        raise BuildError("repo selected manifest is unavailable")
    if not active.is_symlink() and active.read_bytes() == manifest:
        # Support an existing legacy checkout with the exact flattened file.
        return
    manifest_dir = repo_dir / "manifests"
    pinned = manifest_dir / MANIFEST_NAME
    if (
        manifest_dir.is_symlink() or not manifest_dir.is_dir()
        or pinned.is_symlink() or not pinned.is_file()
        or pinned.read_bytes() != manifest
    ):
        raise BuildError("repo selected manifest differs from the exact official snapshot")
    if active.is_symlink():
        if active.resolve(strict=True) != pinned.resolve(strict=True):
            raise BuildError("repo manifest link does not select the pinned snapshot")
        return
    try:
        wrapper = ET.fromstring(active.read_bytes())
    except ET.ParseError as exc:
        raise BuildError("invalid repo manifest selection wrapper") from exc
    children = list(wrapper)
    if (
        wrapper.tag != "manifest" or wrapper.attrib or (wrapper.text or "").strip()
        or len(children) != 1 or children[0].tag != "include"
        or children[0].attrib != {"name": MANIFEST_NAME}
        or list(children[0]) or (children[0].text or "").strip()
        or (children[0].tail or "").strip()
    ):
        raise BuildError("repo manifest wrapper must select only the exact pinned snapshot")


def verify_checkout(
    source: Path, manifest: bytes, *, clean: bool = False,
    selected: list[str] | None = None,
) -> None:
    repo = shutil.which("repo")
    if repo is None:
        raise BuildError("repo is required to verify complete source revisions")
    source = source.resolve(strict=True)
    expected = project_map(manifest)
    verify_active_manifest(source, manifest)
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


def verified_vendor_inputs(source: Path) -> dict:
    # Vendor repositories are absent from the public release manifest. They
    # have a separate immutable candidate lock, never a blanket vendor/ allowlist.
    from prepare_vold_vendor_makefiles import VendorInputError, verify_vendor_checkout
    try:
        return verify_vendor_checkout(source)
    except (OSError, ValueError, VendorInputError, subprocess.CalledProcessError) as exc:
        raise BuildError(f"supplemental vendor input verification failed: {exc}") from exc


def verify_source_workspace(
    source: Path, manifest: bytes, selected: list[str] | None,
    *, require_vendor: bool = False,
) -> dict | None:
    """Reject unrecorded source outside clean pinned projects before building."""
    approved = set(project_map(manifest) if selected is None else selected)
    from prepare_vold_vendor_makefiles import VENDOR_PATHS
    vendor = None
    if require_vendor or any((source / path).exists() or (source / path).is_symlink() for path in VENDOR_PATHS):
        vendor = verified_vendor_inputs(source)
        approved.update(VENDOR_PATHS)
    exported = {}
    for project in ET.fromstring(manifest).findall("project"):
        project_path = project.get("path", project.get("name", ""))
        if project_path not in approved:
            continue
        for entry in project:
            if entry.tag in ("linkfile", "copyfile"):
                exported[entry.attrib["dest"]] = (
                    entry.tag, source / project_path / entry.attrib["src"]
                )
    parents = {
        parent.as_posix()
        for path in approved | set(exported)
        for parent in Path(path).parents if parent != Path(".")
    }
    pending = [source]
    while pending:
        for path in pending.pop().iterdir():
            relative = path.relative_to(source).as_posix()
            if relative in approved:
                continue  # verify_checkout checks each entire Git worktree.
            if relative in exported:
                kind, original = exported[relative]
                if kind == "linkfile":
                    if not path.is_symlink() or path.resolve() != original.resolve():
                        raise BuildError(f"manifest linkfile differs from pinned source: {relative}")
                elif path.is_symlink() or not path.is_file() or path.read_bytes() != original.read_bytes():
                    raise BuildError(f"manifest copyfile differs from pinned source: {relative}")
                continue
            if relative in (".repo", "out"):
                if path.is_symlink() or not path.is_dir():
                    raise BuildError(f"build workspace directory is unsafe: {relative}")
                continue
            if relative in parents and path.is_dir() and not path.is_symlink():
                pending.append(path)
                continue
            raise BuildError(f"unrecorded source outside selected pinned projects: {relative}")
    return vendor


def sync(
    source: Path, lock: dict, manifest: bytes, jobs: int,
    selected: list[str] | None = None, resume: bool = False,
) -> None:
    if source.is_symlink() or source.exists() and not source.is_dir():
        raise BuildError("source directory must be a real directory")
    if resume:
        try:
            verify_active_manifest(source, manifest)
        except BuildError as exc:
            raise BuildError(
                f"{exc}. Resume never replaces unverified Repo metadata. "
                "If the first Repo initialization was interrupted, rename only "
                f"the incomplete source directory {source} to an unused sibling "
                "path, then rerun the same local command with the same --work-dir. "
                "Retain source-selection.json and the rest of the work directory. "
                "For a previously completed checkout, investigate the manifest "
                "mismatch instead of treating it as interrupted setup. See "
                "docs/vold-baseline-ci.md#interrupted-initial-repo-setup."
            ) from exc
    elif source.exists() and any(source.iterdir()):
        raise BuildError("source directory must be new or empty; use --resume to add pinned dependencies")
    repo = shutil.which("repo")
    if repo is None:
        raise BuildError("repo tool is unavailable; see docs/vold-baseline-ci.md for local setup")
    source.mkdir(parents=True, exist_ok=True)
    source = source.resolve(strict=True)
    if not resume:
        manifest_commit = lock["projects"]["manifest"]["revision"]
        command([
            repo, "init", "-u", "https://github.com/LineageOS/android.git",
            "-b", manifest_commit, "--depth=1",
        ], source)
        # Do not overwrite .repo/manifest.xml: Repo manages this file, and
        # recent versions regenerate it during subsequent sync/init operations.
        # Instead install the immutable flattened manifest in Repo's manifest
        # checkout and formally select it with -m, as supported by Repo.
        manifest_dir = source / ".repo/manifests"
        if manifest_dir.is_symlink() or not manifest_dir.is_dir():
            raise BuildError("repo manifests checkout is unavailable or unsafe")
        pinned_manifest = manifest_dir / MANIFEST_NAME
        if pinned_manifest.exists() or pinned_manifest.is_symlink():
            raise BuildError("preexisting untrusted local pinned manifest")
        pinned_manifest.write_bytes(manifest)
        command([
            repo, "init", "-u", "https://github.com/LineageOS/android.git",
            "-b", manifest_commit, "--depth=1", "-m", MANIFEST_NAME
        ], source)
    verify_active_manifest(source, manifest)
    # Explicit project-path sync prevents all 1,243 projects being fetched.
    # On resume, preverify existing project HEADs and cleanliness, then fetch
    # *only newly selected* missing projects. Preserve the original Soong out
    # tree and all previously fetched Git objects across source expansions.
    to_sync = selected
    if resume:
        assert selected is not None
        present = []
        missing = []
        for project in selected:
            path = source / project
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise BuildError(f"previously synced project path is unsafe: {project}")
            # Parent directories and interrupted checkout directories are not
            # fetched projects. Repo worktrees have a .git file/link/directory.
            git_marker = path / ".git"
            (present if git_marker.exists() or git_marker.is_symlink() else missing).append(project)
        if present:
            verify_checkout(source, manifest, clean=True, selected=present)
        to_sync = missing
    if to_sync is None or to_sync:
        command([repo, "sync", "--current-branch", "--no-tags", "--no-clone-bundle", f"-j{jobs}", *(to_sync or [])], source)
    verify_checkout(source, manifest, clean=True, selected=selected)


def compile_original_vold(
    source: Path, lock: dict, manifest: bytes, jobs: int,
    selected: list[str] | None = None,
) -> Path:
    """Do not apply or import the patch. A successful compile is NOT proof."""
    if source.is_symlink() or not (source / ".repo/manifest.xml").exists():
        raise BuildError("baseline source directory is missing or unsafe")
    source = source.resolve(strict=True)
    verify_checkout(source, manifest, clean=True, selected=selected)
    verify_source_workspace(source, manifest, selected, require_vendor=True)
    release_config = source / lock["build"]["release_config"]["path"]
    if (
        release_config.is_symlink() or not release_config.is_file()
        or hashlib.sha256(release_config.read_bytes()).hexdigest()
        != lock["build"]["release_config"]["sha256"]
    ):
        raise BuildError("pinned LineageOS release configuration is missing or changed")
    artifact = source / lock["build"]["artifact"]
    for parent in artifact.parents:
        if parent == source:
            break
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise BuildError("Android vold output directory is unsafe")
    if artifact.is_symlink():
        raise BuildError("Android vold build artifact cannot be a symbolic link")
    # An earlier trial must not be mistaken for the current compiler result.
    if artifact.exists():
        if not artifact.is_file():
            raise BuildError("unexpected prior vold output type")
        artifact.unlink()
    # At this pinned revision, lunch requires product/release/variant and also
    # invokes roomservice, which can fetch unselected sources. Its unchanged
    # _lunch_meat helper performs the same configuration without roomservice.
    # Kernel auto-construction is outside this individual target's scope.
    script = (
        "source build/envsetup.sh || exit $?; "
        "function build_kernel() { echo 'Automatic kernel builds are outside the pinned vold probe' >&2; exit 1; }; "
        f"check_product {lock['build']['product']} {lock['build']['release']} && "
        f"{lock['build']['setup_command']} && "
        f"m -j{jobs} vold"
    )
    environment = os.environ.copy()
    for variable in ("SOONG_ALLOW_MISSING_DEPENDENCIES", "ALLOW_MISSING_DEPENDENCIES"):
        if environment.get(variable, "").lower() not in ("", "false", "0"):
            raise BuildError(f"missing-dependency bypass must be disabled: {variable}")
        environment.pop(variable, None)
    # An inherited runner output override must not reuse another build tree.
    for variable in ("OUT_DIR_COMMON_BASE", "ANDROID_PRODUCT_OUT", "ANDROID_BUILD_TOP", "TOP", "BASH_ENV"):
        environment.pop(variable, None)
    environment["OUT_DIR"] = "out"
    environment["USE_LEFTOVERS"] = "0"
    environment["ROOMSERVICE_DRYRUN"] = "true"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Upstream envsetup intentionally reads unset optional variables; nounset
    # would fail before configuration (for example at USE_LEFTOVERS).
    command(["bash", "-ec", script], source, env=environment)
    verify_checkout(source, manifest, clean=True, selected=selected)
    verify_source_workspace(source, manifest, selected, require_vendor=True)
    if artifact.is_symlink() or not artifact.is_file() or not artifact.stat().st_mode & 0o111:
        raise BuildError("source build did not produce a regular executable vold")
    return artifact


def build_baseline(
    source: Path, dest: Path, reference: Path, lock: dict, manifest: bytes,
    jobs: int, selected: list[str] | None = None,
) -> dict:
    """A build is only *qualified* if every byte matches the official OTA."""
    official = read_vold(reference)
    if dest.is_symlink() or (dest.exists() and (
        not dest.is_dir() or any(dest.iterdir())
    )):
        raise BuildError("baseline output directory must be new or empty")
    dest.mkdir(parents=True, exist_ok=True)
    artifact = compile_original_vold(source, lock, manifest, jobs, selected)
    built = read_vold(artifact)
    report = comparison(official, built, lock)
    report["build_release"] = lock["build"]["release"]
    report["build_setup_command"] = lock["build"]["setup_command"]
    report["release_config_sha256"] = lock["build"]["release_config"]["sha256"]
    report["supplemental_vendor_inputs"] = verified_vendor_inputs(source)
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
    for name in ("sync", "sync-vendor-sources", "probe", "compare"):
        opt = sub.add_parser(name)
        opt.add_argument("--source-root", type=Path, required=True)
        opt.add_argument("--jobs", type=int, default=4)
        if name != "sync-vendor-sources":
            opt.add_argument("--projects-file", type=Path, help="optional validated subset plan; never weakens the binary equality gate")
        if name in ("sync", "sync-vendor-sources"):
            opt.add_argument("--resume", action="store_true", help="fetch missing approved projects within the same checkout")
        if name == "compare":
            opt.add_argument("--reference-vold", type=Path, required=True)
            opt.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be between 1 and 16")
    try:
        lock, manifest = load_contract()
        selected = verified_selection(getattr(args, "projects_file", None), manifest)
        if args.mode == "sync-vendor-sources":
            # Acquire/verify the small source lists before fetching toolchains.
            # Resume never resyncs existing clean worktrees or erases an
            # expanded dependency selection from an earlier local invocation.
            sync(args.source_root, lock, manifest, args.jobs,
                 list(VENDOR_PREREQUISITE_PROJECTS), args.resume)
        elif args.mode == "sync":
            if args.resume and selected is None:
                raise BuildError("full-manifest checkout does not support incremental resume")
            sync(args.source_root, lock, manifest, args.jobs, selected, args.resume)
        elif args.mode == "probe":
            if selected is None:
                raise BuildError("compilation probe requires an explicit pinned subset selection")
            compile_original_vold(args.source_root, lock, manifest, args.jobs, selected)
            print("Dependency probe compiled; this is NOT byte-identical proof or release approval.")
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
