#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bind a Magisk selection, downloaded APK and finished ramdisk to the same bytes.

The official APK hashes are reviewed, committed policy. Other versions/forks
require an explicit caller-supplied digest. This is artifact/boot verification;
it cannot establish that Magisk has initialized or granted root on a device.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import zipfile

from bootstrap_archive import BootstrapError
from bootstrap_io import committed_bytes, download_https


LOCK_PATH = Path(__file__).resolve().parents[1] / "locks/magisk-v1.json"
MAX_APK_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
VERSION = re.compile(r"v[0-9]+(?:\.[0-9A-Za-z_-]+)*")
DIGEST = re.compile(r"[0-9a-f]{64}")
ABIS = ("arm64-v8a", "x86_64", "armeabi-v7a", "x86")


class MagiskError(ValueError):
    """The selected APK or output does not satisfy the Magisk contract."""


@dataclass(frozen=True)
class MagiskArtifact:
    repository: str
    version: str
    sha256: str
    size: int | None = None
    version_code: int | None = None

    @property
    def url(self) -> str:
        return (
            f"https://github.com/{self.repository}/releases/download/"
            f"{self.version}/Magisk-{self.version}.apk"
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise MagiskError(f"duplicate Magisk lock key: {key}")
        result[key] = value
    return result


def load_lock(path: Path = LOCK_PATH) -> list[MagiskArtifact]:
    data = _read_regular(path, 64 * 1024)
    committed_bytes(path, data)
    lock = json.loads(data, object_pairs_hook=_unique_object)
    if (
        not isinstance(lock, dict)
        or set(lock) != {"schema", "artifacts"}
        or lock["schema"] != "modos-magisk-artifacts-v1"
        or not isinstance(lock["artifacts"], list)
        or not lock["artifacts"]
    ):
        raise MagiskError("invalid Magisk artifact lock")
    artifacts = []
    identities = set()
    for entry in lock["artifacts"]:
        if not isinstance(entry, dict) or set(entry) != {
            "repository", "version", "sha256", "size", "version_code"
        }:
            raise MagiskError("invalid Magisk artifact lock entry")
        artifact = MagiskArtifact(**entry)
        _validate_selection(artifact.repository, artifact.version, artifact.sha256)
        if (
            type(artifact.size) is not int
            or not 0 < artifact.size <= MAX_APK_BYTES
            or type(artifact.version_code) is not int
            or artifact.version_code <= 0
        ):
            raise MagiskError("invalid locked Magisk size or version code")
        identity = (artifact.repository, artifact.version)
        if identity in identities:
            raise MagiskError("duplicate Magisk artifact selection")
        identities.add(identity)
        artifacts.append(artifact)
    return artifacts


def _validate_selection(repository: str, version: str, sha256: str) -> None:
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise MagiskError("Magisk repository must be owner/name")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise MagiskError("Magisk requires an explicit version tag, such as v31.1")
    if not isinstance(sha256, str) or not DIGEST.fullmatch(sha256):
        raise MagiskError("Magisk APK SHA-256 must be 64 lowercase hexadecimal digits")


def resolve_artifact(repository: str, version: str, sha256: str = "") -> MagiskArtifact:
    for artifact in load_lock():
        if (artifact.repository, artifact.version) == (repository, version):
            if sha256 and sha256 != artifact.sha256:
                raise MagiskError("MAGISK_APK_SHA256 disagrees with the reviewed Magisk lock")
            return artifact
    if not sha256:
        raise MagiskError(
            f"{repository} {version} has no reviewed APK lock; "
            "set MAGISK_APK_SHA256 to an independently verified digest"
        )
    _validate_selection(repository, version, sha256)
    return MagiskArtifact(repository, version, sha256)


def _read_regular(path: Path, maximum: int) -> bytes:
    # O_NOFOLLOW also rejects a symlink swapped in after the caller's checks.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            raise MagiskError(f"not a bounded regular file: {path}")
        data = stream.read(maximum + 1)
    if len(data) != info.st_size or len(data) > maximum:
        raise MagiskError(f"file changed or exceeded its limit: {path}")
    return data


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise MagiskError(f"Magisk APK is missing {name}") from exc
    if info.is_dir() or not 0 < info.file_size <= MAX_MEMBER_BYTES:
        raise MagiskError(f"invalid Magisk APK payload: {name}")
    with archive.open(info) as member:
        data = member.read(MAX_MEMBER_BYTES + 1)
    if len(data) != info.file_size or len(data) > MAX_MEMBER_BYTES:
        raise MagiskError(f"invalid Magisk APK payload length: {name}")
    return data


def _open_verified_apk(path: Path, artifact: MagiskArtifact) -> tuple[zipfile.ZipFile, dict]:
    _validate_selection(artifact.repository, artifact.version, artifact.sha256)
    data = _read_regular(path, artifact.size or MAX_APK_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    if digest != artifact.sha256 or (artifact.size is not None and len(data) != artifact.size):
        raise MagiskError("Magisk APK does not match the selected SHA-256/size")
    archive = zipfile.ZipFile(io.BytesIO(data))
    try:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise MagiskError("Magisk APK contains duplicate ZIP members")
        pairs = []
        for line in archive.comment.decode("utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator:
                raise MagiskError("invalid Magisk APK version comment")
            pairs.append((key, value))
        metadata = _unique_object(pairs)
        version = metadata.get("version")
        code_text = metadata.get("versionCode", "")
        if version != artifact.version.removeprefix("v") or not re.fullmatch(r"[1-9][0-9]*", code_text):
            raise MagiskError("Magisk APK version comment does not match the selection")
        code = int(code_text)
        if artifact.version_code is not None and code != artifact.version_code:
            raise MagiskError("Magisk APK version code does not match its lock")
        script = _read_member(archive, "assets/util_functions.sh").decode("utf-8")
        if (
            re.findall(r"^MAGISK_VER=['\"]([^'\"]+)['\"]\r?$", script, re.MULTILINE) != [version]
            or re.findall(r"^MAGISK_VER_CODE=([0-9]+)\r?$", script, re.MULTILINE) != [code_text]
        ):
            raise MagiskError("Magisk APK script and ZIP versions disagree")
        if not any(
            f"lib/{abi}/libmagiskinit.so" in names and f"lib/{abi}/libmagisk.so" in names
            for abi in ABIS
        ):
            raise MagiskError("Magisk APK lacks a supported native payload layout")
        return archive, {
            "repository": artifact.repository,
            "version": version,
            "version_code": code,
            "sha256": digest,
            "size": len(data),
        }
    except Exception:
        archive.close()
        raise


def verify_apk(path: Path, artifact: MagiskArtifact) -> dict:
    archive, report = _open_verified_apk(path, artifact)
    archive.close()
    return report


def download_apk(destination: Path, artifact: MagiskArtifact) -> dict:
    # Never reuse an APK merely because an earlier build left a file here.
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file():
            raise MagiskError("Magisk APK destination must be a regular file")
        try:
            return verify_apk(destination, artifact)
        except (MagiskError, zipfile.BadZipFile, UnicodeError):
            pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part-{secrets.token_hex(12)}")
    try:
        download_https(artifact.url, temporary, artifact.size or MAX_APK_BYTES)
        report = verify_apk(temporary, artifact)
        os.replace(temporary, destination)
        return report
    finally:
        temporary.unlink(missing_ok=True)


def _ramdisk_file(root: Path, relative: str) -> Path:
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise MagiskError(f"Magisk ramdisk payload is a symlink: {relative}")
    return current


def _unxz(data: bytes, maximum: int) -> bytes:
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 * 1024 * 1024)
    result = decoder.decompress(data, max_length=maximum + 1)
    if len(result) > maximum or not decoder.eof or decoder.unused_data:
        raise MagiskError("invalid or oversized Magisk ramdisk XZ payload")
    return result


def verify_ramdisk(directory: Path, apk: Path, artifact: MagiskArtifact) -> dict:
    archive, report = _open_verified_apk(apk, artifact)
    with archive:
        init = _read_regular(_ramdisk_file(directory, "init"), MAX_MEMBER_BYTES)
        matches = [
            abi for abi in ABIS
            if f"lib/{abi}/libmagiskinit.so" in archive.namelist()
            and _read_member(archive, f"lib/{abi}/libmagiskinit.so") == init
        ]
        if len(matches) != 1:
            raise MagiskError("finished ramdisk init does not identify the selected Magisk APK/ABI")
        abi = matches[0]
        payloads = {"magisk.xz": f"lib/{abi}/libmagisk.so"}
        if f"lib/{abi}/libinit-ld.so" in archive.namelist():
            payloads["init-ld.xz"] = f"lib/{abi}/libinit-ld.so"
        if "assets/stub.apk" in archive.namelist():
            payloads["stub.xz"] = "assets/stub.apk"
        for filename in ("magisk.xz", "magisk32.xz", "magisk64.xz", "init-ld.xz", "stub.xz"):
            path = _ramdisk_file(directory, f"overlay.d/sbin/{filename}")
            if filename not in payloads:
                if path.exists():
                    raise MagiskError(f"unexpected Magisk ramdisk payload: {filename}")
                continue
            expected = _read_member(archive, payloads[filename])
            actual = _unxz(_read_regular(path, MAX_MEMBER_BYTES), len(expected))
            if actual != expected:
                raise MagiskError(f"finished ramdisk {filename} differs from the selected Magisk APK")
        return {**report, "abi": abi, "ramdisk_payloads": ["init", *payloads]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha256", default="")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("resolve", help="print the selected APK digest for build identity")
    for action in ("verify", "download", "verify-ramdisk"):
        subparser = subparsers.add_parser(action)
        subparser.add_argument("--apk", type=Path, required=True)
        if action == "verify-ramdisk":
            subparser.add_argument("--ramdisk-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        artifact = resolve_artifact(args.repository, args.version, args.sha256)
        if args.action == "resolve":
            print(artifact.sha256)
            return 0
        if args.action == "download":
            report = download_apk(args.apk, artifact)
        elif args.action == "verify-ramdisk":
            report = verify_ramdisk(args.ramdisk_dir, args.apk, artifact)
        else:
            report = verify_apk(args.apk, artifact)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (MagiskError, BootstrapError, OSError, ValueError, zipfile.BadZipFile, lzma.LZMAError) as exc:
        print(f"Magisk verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
