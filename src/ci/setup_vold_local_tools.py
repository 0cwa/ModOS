#!/usr/bin/env python3
"""Install private Repo/avbroot launchers for local vold work, without sudo.

Repo's launcher is SHA-256 pinned; its normal signed Repo bootstrap stays on.
avbroot uses the existing publisher-authenticated executable lock and sealed
runner. No system tools, shell profiles, source trees or GitHub settings change.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile

from vold_local import (
    DEFAULT_WORK, LocalBuildError, REPO_LAUNCHER_REV, REPO_LAUNCHER_SHA256,
    ROOT, digest_file, exclusive_workspace, work_directory,
)


def private_directory(path: Path) -> Path:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise LocalBuildError(f"Tool directory is unsafe: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise LocalBuildError(f"Tool directory must be private (mode 0700): {path}")
    return path


def write_executable(path: Path, contents: str, mode: int = 0o755) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LocalBuildError(f"Tool launcher is unsafe: {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as output:
        temporary = Path(output.name)
        output.write(contents.encode())
    try:
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_repo(binary: Path) -> None:
    if binary.exists() or binary.is_symlink():
        if digest_file(binary) != REPO_LAUNCHER_SHA256:
            raise LocalBuildError(f"Existing private Repo launcher does not match the reviewed SHA-256: {binary}")
        binary.chmod(0o755)
        return
    with tempfile.TemporaryDirectory(prefix="repo-download-", dir=binary.parent) as temporary:
        downloaded = Path(temporary) / "repo"
        subprocess.run([
            "curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "30",
            "--proto", "=https", "--proto-redir", "=https",
            "https://raw.githubusercontent.com/GerritCodeReview/git-repo/" + REPO_LAUNCHER_REV + "/repo",
            "--output", str(downloaded),
        ], check=True)
        if digest_file(downloaded) != REPO_LAUNCHER_SHA256:
            raise LocalBuildError("Official Repo launcher download failed its pinned SHA-256 check.")
        downloaded.chmod(0o755)
        downloaded.replace(binary)


def avbroot_wrapper(store: Path) -> str:
    python = str(Path(sys.executable).resolve())
    command = [
        python, "-E", "-s", "-B",
        str(ROOT / "src/bootstrap_executable_tools.py"),
        "--workdir", str(store), "run", "avbroot", "--",
    ]
    # Bypass PATH interpreters, Python startup overrides and shell BASH_ENV.
    return (
        f"#!{python} -I\nimport os\nimport sys\n"
        f"os.execv({python!r}, {command!r} + sys.argv[1:])\n"
    )


def setup(work: Path, repo_only: bool = False) -> Path:
    store = private_directory(work / "host-tools")
    binaries = private_directory(store / "bin")
    install_repo(binaries / "repo")
    if not repo_only:
        # Reuse the existing archive/member hashes, detached SSH signature,
        # trusted signer and executable runner. No parallel download policy.
        subprocess.run([
            sys.executable, "-E", "-s", "-B", str(ROOT / "src/bootstrap_executable_tools.py"),
            "--workdir", str(store), "install", "--report", str(store / "executable-tools.json"),
            "avbroot",
        ], cwd=work, check=True)
        write_executable(binaries / "avbroot", avbroot_wrapper(store))
        subprocess.run([str(binaries / "avbroot"), "--version"], cwd=work, check=True)
    environment = store / "env.sh"
    write_executable(environment, "export PATH=" + shlex.quote(str(binaries)) + ':"$PATH"\n', 0o600)
    return environment


def sealed_runner_supported() -> bool:
    import fcntl
    return (
        os.execve in os.supports_fd and hasattr(os, "memfd_create")
        and all(hasattr(fcntl, name) for name in (
            "F_ADD_SEALS", "F_GET_SEALS", "F_SEAL_WRITE", "F_SEAL_GROW", "F_SEAL_SHRINK", "F_SEAL_SEAL",
        ))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--repo-only", action="store_true", help="skip avbroot until ready for authenticated comparison")
    args = parser.parse_args(argv)
    try:
        if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
            raise LocalBuildError("The local build setup supports Linux x86_64.")
        if not args.repo_only and not sealed_runner_supported():
            raise LocalBuildError(
                "This Python lacks Linux sealed-execution support; on Ubuntu rerun this setup with /usr/bin/python3."
            )
        required = ("curl", "git", "gpg") + (() if args.repo_only else ("ssh-keygen",))
        missing = [tool for tool in required if shutil.which(tool) is None]
        if missing:
            raise LocalBuildError("Install the documented host packages first; missing: " + ", ".join(missing))
        work = work_directory(args.work_dir)
        with exclusive_workspace(work):
            environment = setup(work, args.repo_only)
        print("Private host tools are ready. Run this in your current shell:")
        print("  source " + shlex.quote(str(environment)))
        print("Repo's launcher is pinned; its normal signed tool bootstrap remains enabled.")
        if not args.repo_only:
            print("avbroot executes through the existing publisher-verified sealed runner.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: local tool setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
