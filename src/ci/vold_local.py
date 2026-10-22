#!/usr/bin/env python3
"""Build and qualify original pdx235 vold locally with persistent sources/cache.

Examples:
  python3 src/ci/vold_local.py doctor --work-dir /fast/modos-vold
  python3 src/ci/vold_local.py vendor --work-dir /fast/modos-vold
  python3 src/ci/vold_local.py compare --work-dir /fast/modos-vold --jobs 4

This command never patches, signs, publishes, or deploys an OTA.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import sys
from urllib.parse import urlparse
import uuid

from build_original_vold import BuildError, load_contract
from vold_baseline_compare import BaselineError, read_vold
from vold_source_closure import initial_projects, selection_record, verified_contract

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK = Path.home() / ".cache/modos/vold-pdx235-20261016"
STAGES = ("vendor", "prepare", "probe", "compare")
BASE_TOOLS = ("bash", "git", "gpg", "repo", "python3")
BUILD_TOOLS = ("make", "gcc", "g++", "zip", "unzip", "rsync")
COMPARE_TOOLS = ("curl", "avbroot", "unzip", "openssl", "sha256sum", "debugfs")
AUTH_FILES = (
    "locks/lineage-vold-baseline-20261016.json",
    "locks/executable-tools-v1.json",
    "src/bootstrap_executable_tools.py",
    "src/ci/extract_original_vold.sh",
    "src/source_care_map_policy.sh",
    "src/ci/check_ota_certificate_member.py",
    "src/ci/vold_baseline_compare.py",
)
REPO_LAUNCHER_REV = "d744eb15f38b9351463900babffd732994e3a046"
REPO_LAUNCHER_SHA256 = "05f571deafd6ed6da0d20fa707dde29aa5624dbf35553d697a40dbdd4c8d178a"


class LocalBuildError(ValueError):
    pass


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def digest_file(path: Path) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise LocalBuildError(f"Expected a regular file, not a link: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise LocalBuildError(f"File changed while hashing: {path}")
    return digest.hexdigest()


def real_directory(path: Path) -> Path:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise LocalBuildError(f"Workspace directory is unsafe: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


def work_directory(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(character.isspace() for character in str(path)):
        raise LocalBuildError("Use a work directory without whitespace; Android build tools require a compatible source path.")
    result = real_directory(path)
    if any(character.isspace() for character in str(result)):
        raise LocalBuildError("The resolved Android work directory must not contain whitespace.")
    return result


def write_json(path: Path, value: dict) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LocalBuildError(f"Refusing to replace an unsafe report: {path}")
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
    temporary.replace(path)


@contextmanager
def exclusive_workspace(work: Path):
    # Android compilation is Linux-only. Import lazily so plan/help still work
    # on other platforms and doctor can explain the host requirement.
    import fcntl
    path = work / "local-workspace.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LocalBuildError(f"Another local vold command is using {work}; let it finish first.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def doctor(work: Path, stage: str, jobs: int, minimum_free_gb: float | None) -> dict:
    required = set(BASE_TOOLS)
    if stage in ("probe", "compare"):
        required.update(BUILD_TOOLS)
    if stage == "compare":
        required.update(COMPARE_TOOLS)
    paths = {tool: shutil.which(tool) for tool in sorted(required)}
    missing = [tool for tool, path in paths.items() if path is None]
    free = shutil.disk_usage(work).free
    errors = []
    notes = []
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        errors.append("Source builds require Linux x86_64; Ubuntu 24.04 is the documented local setup.")
    if missing:
        errors.append("Missing required executables: " + ", ".join(missing))
    if minimum_free_gb is not None and free < minimum_free_gb * 1_000_000_000:
        errors.append(f"Free disk is below the explicit --min-free-gb {minimum_free_gb:g} requirement.")
    elif stage != "vendor" and free < 100_000_000_000:
        notes.append("100 GB free is an unmeasured initial planning allowance, not a resume requirement. Existing sources already occupy disk; use --min-free-gb for an explicit floor.")
    memory = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                memory = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    return {
        "schema_version": 1, "kind": "local-vold-host-preflight",
        "stage": stage, "work_dir": str(work), "jobs": jobs,
        "host": {"system": platform.system(), "machine": platform.machine()},
        "free_disk_bytes": free, "physical_memory_bytes": memory,
        "minimum_free_gb": minimum_free_gb, "tool_paths": paths,
        "missing_tools": missing, "errors": errors, "notes": notes,
        "ready_for_requested_stage": not errors,
        "does_not_establish_source_closure_or_byte_identity": True,
    }


def print_doctor(report: dict) -> None:
    print(f"Work directory: {report['work_dir']}")
    print(f"Host: {report['host']['system']} {report['host']['machine']}; jobs: {report['jobs']}")
    print(f"Free disk: {report['free_disk_bytes'] / 1_000_000_000:.1f} GB")
    if report["physical_memory_bytes"] is not None:
        print(f"Physical memory: {report['physical_memory_bytes'] / (1024 ** 3):.1f} GiB")
    for note in report["notes"]:
        print("Note: " + note)
    for error in report["errors"]:
        print("Required: " + error)
    if "repo" in report["missing_tools"]:
        print("Install the official Repo launcher, then put it on PATH (its normal signed bootstrap remains enabled):")
        print("  mkdir -p \"$HOME/.local/bin\"")
        print("  curl --fail --location --proto '=https' --proto-redir '=https' \\")
        print(f"    https://raw.githubusercontent.com/GerritCodeReview/git-repo/{REPO_LAUNCHER_REV}/repo \\")
        print("    --output \"$HOME/.local/bin/repo.download\"")
        print(f"  printf '%s  %s\\n' '{REPO_LAUNCHER_SHA256}' \"$HOME/.local/bin/repo.download\" | sha256sum --check - && \\")
        print("    install -m 0755 \"$HOME/.local/bin/repo.download\" \"$HOME/.local/bin/repo\"")
        print("  export PATH=\"$HOME/.local/bin:$PATH\"")
    if report["missing_tools"]:
        print("Ubuntu packages and the trusted avbroot setup are documented in docs/vold-baseline-ci.md.")


def run_logged(command: list[str], *, log: Path, env: dict | None = None) -> None:
    print("Running: " + shlex.join(command), flush=True)
    with log.open("x", encoding="utf-8") as output:
        output.write("$ " + shlex.join(command) + "\n")
        output.flush()
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True,
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                output.write(line)
                output.flush()
            status = process.wait()
        except BaseException:
            # Stop the whole compile/download process group before releasing
            # the workspace lock; keep its sources and logs for the next run.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise
        finally:
            process.stdout.close()
    if status:
        raise LocalBuildError(f"Command exited with status {status}. Sources and outputs are retained; inspect {log} and rerun the same command after resolving the diagnostic.")


def source_stage(stage: str, work: Path, run_dir: Path, jobs: int, reference: Path | None = None) -> Path:
    phase = real_directory(run_dir / stage)
    environment = os.environ.copy()
    environment.update(VOLD_WORK=str(work), VOLD_RUN_DIR=str(phase),
                       VOLD_PROBE_STAGE=stage, VOLD_PROBE_JOBS=str(jobs))
    environment.pop("VOLD_SOURCE_REF", None)
    if reference is not None:
        environment["VOLD_SOURCE_REF"] = str(reference)
    run_logged(["bash", str(ROOT / "src/ci/probe_original_vold.sh")],
               log=phase / "workflow.log", env=environment)
    return phase


def ota_filename(contract: dict) -> str:
    filename = contract["base_build_id"] + ".zip"
    url = urlparse(contract["source_ota_url"])
    if url.scheme != "https" or Path(url.path).name != filename:
        raise LocalBuildError("The source contract does not name the exact HTTPS OTA archive.")
    return filename


def exact_ota(work: Path, run_dir: Path, contract: dict, supplied: Path | None = None) -> Path:
    filename = ota_filename(contract)
    if supplied is not None:
        archive = supplied.expanduser().absolute()
        if archive.name != filename or digest_file(archive) != contract["source_ota_sha256"]:
            raise LocalBuildError("--ota-file must match the exact locked official filename and SHA-256; it has not been accepted as authentic.")
        return archive
    cache = real_directory(work / "cache")
    archive = cache / filename
    if archive.exists() or archive.is_symlink():
        if digest_file(archive) != contract["source_ota_sha256"]:
            raise LocalBuildError(f"Cached OTA digest differs from the immutable lock. Move the corrupt file aside and rerun: {archive}")
        print(f"Reusing exact cached OTA: {archive}")
        return archive
    partial = cache / (filename + ".part")
    if partial.exists() or partial.is_symlink():
        if digest_file(partial) == contract["source_ota_sha256"]:
            partial.replace(archive)
            return archive
    run_logged([
        "curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "30",
        "--proto", "=https", "--proto-redir", "=https", "--continue-at", "-",
        "--output", str(partial), contract["source_ota_url"],
    ], log=run_dir / "download-ota.log")
    if digest_file(partial) != contract["source_ota_sha256"]:
        raise LocalBuildError(f"Downloaded OTA failed the pinned SHA-256 check; reference extraction was not attempted: {partial}")
    partial.replace(archive)
    return archive


def authentication_inputs() -> dict:
    inputs = {path: digest_file(ROOT / path) for path in AUTH_FILES}
    executable = shutil.which("avbroot")
    if executable is None:
        raise LocalBuildError("avbroot is required for authentic OTA verification.")
    inputs["avbroot_executable"] = digest_file(Path(executable).resolve(strict=True))
    return inputs


def reference_receipt(directory: Path, contract: dict, inputs: dict) -> dict:
    reference = directory / "original-vold"
    read_vold(reference)
    return {
        "schema_version": 1, "kind": "authenticated-local-original-vold-reference",
        "base_build_id": contract["base_build_id"],
        "source_ota_filename": ota_filename(contract),
        "source_ota_sha256": contract["source_ota_sha256"],
        "manifest_sha256": contract["manifest"]["sha256"],
        "reference_sha256": digest_file(reference),
        "certificate_sha256": digest_file(directory / "source-otacert.pem"),
        "authentication_inputs": inputs,
        "independent_publisher_and_ota_signature_verified": True,
    }


def authenticated_reference(archive: Path, work: Path, run_dir: Path, contract: dict) -> Path:
    # The caller first checks the entire OTA against its immutable hash. A user
    # cannot supply a standalone ELF/reference path to this local entry point.
    # A local JSON receipt is editable and is not an independent authenticity
    # anchor. Re-extract from the verified cached OTA on every comparison; only
    # the download is reused. The reference receipt remains audit evidence.
    directory = run_dir / "reference"
    inputs = authentication_inputs()
    if directory.exists() or directory.is_symlink():
        raise LocalBuildError("Fresh reference extraction requires a new invocation directory; existing ELF files or receipts are not accepted.")
    receipt_file = directory / "reference-receipt.json"
    run_logged(["bash", str(ROOT / "src/ci/extract_original_vold.sh"), str(archive), str(directory)],
               log=run_dir / "extract-reference.log")
    write_json(receipt_file, reference_receipt(directory, contract, inputs))
    return directory / "original-vold"


def require_comparison_receipt(phase: Path, reference: Path, contract: dict) -> Path:
    expected = digest_file(reference)
    for path in sorted(phase.glob("baseline-*/baseline-report.json")):
        digest_file(path)
        report = json.loads(path.read_bytes())
        if (
            report.get("byte_identical") is True
            and report.get("official_ota_sha256") == contract["source_ota_sha256"]
            and report.get("manifest_sha256") == contract["manifest"]["sha256"]
            and report.get("reference_sha256") == report.get("built_sha256") == expected
        ):
            return path
    raise LocalBuildError("Compare returned without a matching byte-identical receipt in this invocation's diagnostics.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="mode", required=True)
    help_text = {
        "plan": "write the pinned source plan without downloads",
        "doctor": "check local host, tools, memory and disk",
        "vendor": "fetch only small public prerequisite projects and pinned vendor candidates",
        "prepare": "prepare/resume the pinned source selection without compiling",
        "probe": "compile unmodified m vold without downloading the OTA",
        "compare": "compile first, authenticate the exact OTA, then require byte equality",
    }
    for mode, description in help_text.items():
        command = commands.add_parser(mode, help=description)
        command.add_argument("--work-dir", type=Path, default=DEFAULT_WORK,
                             help="persistent source, cache and logs directory; no automatic cleanup")
        command.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
        command.add_argument("--min-free-gb", type=float,
                             help="optional explicit disk floor; 100 GB is only an initial recommendation")
        if mode == "doctor":
            command.add_argument("--for", dest="check_stage", choices=STAGES, default="probe")
        if mode == "compare":
            command.add_argument("--ota-file", type=Path,
                                 help="already downloaded exact official archive; filename, digest and publisher are verified")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if not 1 <= args.jobs <= 16:
        arguments.error("--jobs must be between 1 and 16")
    if args.min_free_gb is not None and not 0 <= args.min_free_gb < float("inf"):
        arguments.error("--min-free-gb must be a finite nonnegative number")
    run_dir = None
    try:
        contract, _manifest = load_contract()
        work = work_directory(args.work_dir)
        if args.mode == "plan":
            seed, projects, _baseline = verified_contract()
            plan = selection_record(initial_projects(seed), seed)
            output = work / "source-provisional-plan.json"
            write_json(output, plan)
            print(f"Pinned starting selection: {len(plan['selected_projects'])}/{len(projects)} projects")
            print(f"Plan: {output}\nNo sources or OTA were downloaded.")
            return 0
        report = doctor(work, getattr(args, "check_stage", args.mode), args.jobs, args.min_free_gb)
        print_doctor(report)
        write_json(work / "local-host-report.json", report)
        if not report["ready_for_requested_stage"]:
            return 1
        if args.mode == "doctor":
            print("Local prerequisites passed. Source closure and binary equality still require the build stages.")
            return 0
        with exclusive_workspace(work):
            run_dir = real_directory(real_directory(work / "runs") / (timestamp() + "-" + args.mode))
            record = {"schema_version": 1, "mode": args.mode, "work_dir": str(work),
                      "jobs": args.jobs, "status": "running", "base_build_id": contract["base_build_id"]}
            write_json(run_dir / "local-run.json", record)
            print(f"Invocation logs: {run_dir}")
            try:
                if args.mode == "compare":
                    source_stage("probe", work, run_dir, args.jobs)
                    archive = exact_ota(work, run_dir, contract, args.ota_file)
                    reference = authenticated_reference(archive, work, run_dir, contract)
                    phase = source_stage("compare", work, run_dir, args.jobs, reference)
                    proof = require_comparison_receipt(phase, reference, contract)
                    record["baseline_report"] = str(proof)
                    print(f"Authentic byte-identical original vold confirmed: {proof}")
                else:
                    source_stage(args.mode, work, run_dir, args.jobs)
                    if args.mode == "probe":
                        print("Original vold compiled. Byte equality is still unproved; run compare next.")
                    else:
                        print(f"{args.mode.capitalize()} completed. Reuse this work directory for the next stage.")
                record["status"] = "completed"
            except BaseException:
                record["status"] = "interrupted-or-failed"
                raise
            finally:
                write_json(run_dir / "local-run.json", record)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Downloaded sources, outputs, caches and logs are retained.", file=sys.stderr)
        return 130
    except (OSError, ValueError, BuildError, BaselineError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if run_dir is not None:
            print(f"Invocation diagnostics: {run_dir}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
