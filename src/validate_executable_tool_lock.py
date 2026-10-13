#!/usr/bin/env python3
"""Validate the checked-in executable-tool lock and SSH trust binding offline."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
from typing import Any, NoReturn


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = REPO_ROOT / "locks" / "executable-tools-v1.json"
DEFAULT_TRUST = REPO_ROOT / "trust" / "chenxiaolong.allowed_signers"

SCHEMA_VERSION = 1
ARCH = "x86_64-unknown-linux-gnu"
SIGNER_IDENTITY = "chenxiaolong"
SIGNATURE_NAMESPACE = "file"
SIGNATURE_TYPE = "ssh"
SIGNER_KEY_TYPE = "ssh-ed25519"
SIGNER_PUBLIC_KEY = (
    "AAAAC3NzaC1lZDI1NTE5AAAAIDOe6/tBnO7xZhAWXRj3ApUYgn+XZ0wnQiXM8B7tPgv4"
)
SIGNER_FINGERPRINT = "SHA256:Ct0HoRyrFLrnF9W+A/BKEiJmwx7yWkgaW/JvghKrboA"

MAX_LOCK_BYTES = 1024 * 1024
MAX_TRUST_BYTES = 4096
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024

TOP_LEVEL_FIELDS = frozenset(("schema_version", "tools"))
TOOL_FIELDS = frozenset(
    (
        "id",
        "version",
        "arch",
        "artifact_name",
        "url",
        "size",
        "sha256",
        "signature",
        "layout",
    )
)
SIGNATURE_FIELDS = frozenset(
    ("url", "type", "identity", "namespace", "signer_fingerprint")
)
LAYOUT_FIELDS = frozenset(("path", "type", "size", "sha256", "mode"))
LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MODE = re.compile(r"0[0-7]{3}\Z")

TOOL_POLICY = {
    "afsr": {
        "version": "2.0.0",
        "repository": "afsr",
        "artifact_name": "afsr-2.0.0-x86_64-unknown-linux-gnu.zip",
        "layout": {"afsr": "0755"},
    },
    "avbroot": {
        "version": "3.34.1",
        "repository": "avbroot",
        "artifact_name": "avbroot-3.34.1-x86_64-unknown-linux-gnu.zip",
        "layout": {"LICENSE": "0644", "README.md": "0644", "avbroot": "0755"},
    },
    "custota-tool": {
        "version": "6.6",
        "repository": "Custota",
        "artifact_name": "custota-tool-6.6-x86_64-unknown-linux-gnu.zip",
        "layout": {"custota-tool": "0755"},
    },
}


class ValidationError(Exception):
    """Raised when lock or trust data fails closed."""


def fail(message: str) -> NoReturn:
    raise ValidationError(message)


def require_exact_fields(
    value: Any, expected: frozenset[str], context: str
) -> dict[str, Any]:
    if type(value) is not dict:
        fail(f"{context} must be an object")
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        fail(f"{context} has {'; '.join(details)}")
    return value


def require_string(value: Any, context: str) -> str:
    if type(value) is not str or not value:
        fail(f"{context} must be a nonempty string")
    return value


def require_bounded_size(value: Any, maximum: int, context: str) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        fail(f"{context} must be an integer between 1 and {maximum}")
    return value


def require_sha256(value: Any, context: str) -> str:
    digest = require_string(value, context)
    if LOWER_SHA256.fullmatch(digest) is None:
        fail(f"{context} must be exactly 64 lowercase hexadecimal characters")
    return digest


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def reject_nonstandard_number(value: str) -> None:
    fail(f"nonstandard JSON number is forbidden: {value}")


def read_regular_file(path: Path, maximum: int, context: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        fail(f"cannot stat {context}: {exc.strerror or exc}")
    if not stat.S_ISREG(before.st_mode):
        fail(f"{context} must be a regular, non-symlink file")
    if before.st_size > maximum:
        fail(f"{context} exceeds the {maximum}-byte limit")
    if not hasattr(os, "O_NOFOLLOW"):
        fail("this platform cannot enforce no-follow reads")

    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        fail(f"cannot open {context} without following links: {exc.strerror or exc}")
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                fail(f"{context} must remain a regular file")
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                fail(f"{context} changed before it was opened")
            if opened.st_size != before.st_size or opened.st_size > maximum:
                fail(f"{context} changed size before it was opened")
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        fail(f"cannot read {context}: {exc.strerror or exc}")
    if len(data) != opened.st_size or opened.st_size != after.st_size:
        fail(f"{context} changed while being read")
    return data


def parse_ssh_string(blob: bytes, offset: int, context: str) -> tuple[bytes, int]:
    if len(blob) - offset < 4:
        fail(f"{context} has a truncated SSH string length")
    length = struct.unpack(">I", blob[offset : offset + 4])[0]
    offset += 4
    end = offset + length
    if end > len(blob):
        fail(f"{context} has a truncated SSH string")
    return blob[offset:end], end


def validate_trust_bytes(raw: bytes) -> str:
    expected_line = f"{SIGNER_IDENTITY} {SIGNER_KEY_TYPE} {SIGNER_PUBLIC_KEY}\n".encode(
        "ascii"
    )
    if raw != expected_line:
        fail(
            "trust file must contain exactly the reviewed chenxiaolong allowed-signer binding"
        )

    try:
        key_blob = base64.b64decode(SIGNER_PUBLIC_KEY, validate=True)
    except (binascii.Error, ValueError) as exc:
        fail(f"trusted SSH key is not canonical base64: {exc}")

    key_type, offset = parse_ssh_string(key_blob, 0, "trusted SSH key")
    public_key, offset = parse_ssh_string(key_blob, offset, "trusted SSH key")
    if offset != len(key_blob):
        fail("trusted SSH key has trailing data")
    if key_type != SIGNER_KEY_TYPE.encode("ascii"):
        fail("trusted SSH key blob type does not match its declaration")
    if len(public_key) != 32:
        fail("trusted Ed25519 public key must contain exactly 32 key bytes")

    encoded_digest = (
        base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
    )
    fingerprint = f"SHA256:{encoded_digest}"
    if fingerprint != SIGNER_FINGERPRINT:
        fail("trusted SSH key does not match the reviewed fingerprint")
    return fingerprint


def load_validated_trust(path: Path) -> tuple[str, bytes]:
    raw = read_regular_file(path, MAX_TRUST_BYTES, "trust file")
    return validate_trust_bytes(raw), raw


def validate_trust(path: Path) -> str:
    fingerprint, _ = load_validated_trust(path)
    return fingerprint


def canonical_release_url(repository: str, version: str, artifact_name: str) -> str:
    return (
        f"https://github.com/chenxiaolong/{repository}/releases/download/"
        f"v{version}/{artifact_name}"
    )


def validate_layout(value: Any, tool_id: str, policy: dict[str, Any]) -> None:
    if type(value) is not list or not value:
        fail(f"tool {tool_id} layout must be a nonempty array")

    paths: list[str] = []
    modes: dict[str, str] = {}
    for index, raw_entry in enumerate(value):
        context = f"tool {tool_id} layout[{index}]"
        entry = require_exact_fields(raw_entry, LAYOUT_FIELDS, context)
        path = require_string(entry["path"], f"{context}.path")
        pure_path = PurePosixPath(path)
        if (
            path.startswith("/")
            or "\\" in path
            or pure_path.is_absolute()
            or len(pure_path.parts) != 1
            or any(part in ("", ".", "..") for part in pure_path.parts)
            or pure_path.as_posix() != path
        ):
            fail(f"{context}.path must be one normalized top-level POSIX name")
        if path in modes:
            fail(f"tool {tool_id} has duplicate layout path: {path}")
        if entry["type"] != "file":
            fail(f"{context}.type must be file")
        require_bounded_size(entry["size"], MAX_MEMBER_BYTES, f"{context}.size")
        require_sha256(entry["sha256"], f"{context}.sha256")
        mode = require_string(entry["mode"], f"{context}.mode")
        if MODE.fullmatch(mode) is None or mode not in ("0644", "0755"):
            fail(f"{context}.mode must be canonical 0644 or 0755")
        paths.append(path)
        modes[path] = mode

    if paths != sorted(paths):
        fail(f"tool {tool_id} layout paths must be sorted")
    if modes != policy["layout"]:
        fail(
            f"tool {tool_id} layout paths or modes do not match the reviewed release layout"
        )


def validate_tool(raw_tool: Any, index: int, fingerprint: str) -> str:
    context = f"tools[{index}]"
    tool = require_exact_fields(raw_tool, TOOL_FIELDS, context)
    tool_id = require_string(tool["id"], f"{context}.id")
    policy = TOOL_POLICY.get(tool_id)
    if policy is None:
        fail(f"unknown executable tool id: {tool_id}")

    if tool["version"] != policy["version"]:
        fail(f"tool {tool_id} has an unreviewed version")
    if tool["arch"] != ARCH:
        fail(f"tool {tool_id} has an unreviewed architecture")
    if tool["artifact_name"] != policy["artifact_name"]:
        fail(f"tool {tool_id} has a noncanonical artifact name")

    expected_url = canonical_release_url(
        policy["repository"], policy["version"], policy["artifact_name"]
    )
    if tool["url"] != expected_url:
        fail(f"tool {tool_id} has a noncanonical release URL")
    require_bounded_size(tool["size"], MAX_ARCHIVE_BYTES, f"tool {tool_id}.size")
    require_sha256(tool["sha256"], f"tool {tool_id}.sha256")

    signature = require_exact_fields(
        tool["signature"], SIGNATURE_FIELDS, f"tool {tool_id}.signature"
    )
    if signature["url"] != f"{expected_url}.sig":
        fail(f"tool {tool_id} has a noncanonical signature URL")
    if signature["type"] != SIGNATURE_TYPE:
        fail(f"tool {tool_id} has an unsupported signature type")
    if signature["identity"] != SIGNER_IDENTITY:
        fail(f"tool {tool_id} has an unreviewed signer identity")
    if signature["namespace"] != SIGNATURE_NAMESPACE:
        fail(f"tool {tool_id} has an unreviewed signature namespace")
    if signature["signer_fingerprint"] != fingerprint:
        fail(f"tool {tool_id} signer fingerprint does not match the trust file")

    validate_layout(tool["layout"], tool_id, policy)
    return tool_id


def validate_lock(path: Path, fingerprint: str) -> dict[str, Any]:
    raw = read_regular_file(path, MAX_LOCK_BYTES, "lock file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"lock file is not UTF-8: {exc}")
    try:
        data = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_number,
        )
    except json.JSONDecodeError as exc:
        fail(
            f"lock file is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
        )

    document = require_exact_fields(data, TOP_LEVEL_FIELDS, "lock")
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != SCHEMA_VERSION
    ):
        fail(f"lock schema_version must be {SCHEMA_VERSION}")
    tools = document["tools"]
    if type(tools) is not list:
        fail("lock tools must be an array")

    tool_ids = [
        validate_tool(tool, index, fingerprint) for index, tool in enumerate(tools)
    ]
    if len(tool_ids) != len(set(tool_ids)):
        fail("lock contains duplicate tool ids")
    expected_ids = sorted(TOOL_POLICY)
    if tool_ids != expected_ids:
        fail(
            f"lock tools must contain exactly these ids in order: {', '.join(expected_ids)}"
        )

    canonical = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if text != canonical:
        fail("lock file is not in canonical sorted-key JSON form")
    return document


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the executable-tool lock and SSH trust binding without network access."
    )
    parser.add_argument(
        "--lock", type=Path, default=DEFAULT_LOCK, help="lock JSON path"
    )
    parser.add_argument(
        "--trust", type=Path, default=DEFAULT_TRUST, help="allowed-signers path"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        fingerprint = validate_trust(args.trust)
        validate_lock(args.lock, fingerprint)
    except ValidationError as exc:
        print(f"executable-tool lock validation failed: {exc}", file=sys.stderr)
        return 1
    print("Executable-tool lock and trust binding are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
