#!/usr/bin/env python3
"""Validate the exact native helper contract without modifying its checkout.

The historical filename remains for callers and development profiles. Version 2
uses native helper options; no source transformation is supported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import urlsplit

COMPATIBILITY_VERSION = 2
DEFAULT_MANIFEST = Path(__file__).with_suffix('.json')
TARGETS = ('patch.py',)
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
REVISION = re.compile(r'[0-9a-f]{40}\Z')
REPORT_BINDING = 'verified-output-sha256-v1'
REQUIRED_OPTIONS = ('--release-url', '--adapter-extension')
SCP_SOURCE = re.compile(
    r'(?P<username>[^@/:]+)@(?P<host>\[[^\]]+\]|[^:/]+):(?P<path>.+)\Z'
)


class CompatibilityError(Exception):
    """Raised when the pinned source or working-tree state is unexpected."""


def fail(message: str) -> None:
    raise CompatibilityError(message)


def checked_revision(helper_root: Path, pinned_revision: str) -> None:
    result = subprocess.run(
        ['git', '-C', str(helper_root), 'rev-parse', '--verify', 'HEAD^{commit}'],
        capture_output=True,
        text=True,
        check=False,
    )
    actual = result.stdout.strip()
    if result.returncode != 0 or actual != pinned_revision:
        detail = result.stderr.strip() or f'resolved {actual or "<none>"}'
        fail(f'Checkout HEAD does not match pinned revision {pinned_revision}: {detail}')


def reject_userinfo(value: str) -> None:
    candidate = value.strip()
    if '://' not in candidate:
        if '@' not in candidate:
            return
        username, separator, suffix = candidate.partition('@')
        if separator and username == 'git' and ':' in suffix:
            return
        fail('Authenticated helper repository URLs are not allowed')

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        fail('Invalid helper repository source')
    try:
        username = parsed.username
        password = parsed.password
    except ValueError:
        fail('Authenticated helper repository URLs are not allowed')
    if password is not None or (username is not None and not (
        parsed.scheme == 'ssh' and username == 'git'
    )):
        fail('Authenticated helper repository URLs are not allowed')


def canonical_host_path(
    host: str,
    path: str,
    port: int | None,
    scheme: str | None,
) -> str:
    if not host or not path.strip('/'):
        fail('Invalid helper repository source')

    normalized_path = path.strip('/').removesuffix('.git')
    if not normalized_path:
        fail('Invalid helper repository source')
    normalized_host = host.lower()
    if normalized_host == 'github.com':
        normalized_path = normalized_path.lower()
    if ':' in normalized_host and not normalized_host.startswith('['):
        normalized_host = f'[{normalized_host}]'

    default_port = (scheme == 'https' and port == 443) or (
        scheme == 'ssh' and port == 22
    )
    port_suffix = '' if port is None or default_port else f':{port}'
    return f'{normalized_host}{port_suffix}/{normalized_path}'


def normalized_repository(value: str) -> str:
    candidate = value.strip()
    reject_userinfo(candidate)

    scp_match = SCP_SOURCE.fullmatch(candidate)
    if scp_match:
        return canonical_host_path(
            scp_match['host'], scp_match['path'], None, 'ssh'
        )

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        fail('Invalid helper repository source')
    if parsed.scheme in {'https', 'ssh'}:
        if parsed.query or parsed.fragment:
            fail('Invalid helper repository source')
        return canonical_host_path(hostname or '', parsed.path, port, parsed.scheme)

    return candidate.rstrip('/').removesuffix('.git')


def checked_repository(helper_root: Path, repository: str) -> None:
    expected = normalized_repository(repository)
    result = subprocess.run(
        ['git', '-C', str(helper_root), 'remote', 'get-url', 'origin'],
        capture_output=True,
        text=True,
        check=False,
    )
    actual = normalized_repository(result.stdout)
    if result.returncode != 0 or actual != expected:
        fail('Checkout origin does not match the effective helper repository source')


def head_blob(helper_root: Path, relative: str) -> bytes:
    result = subprocess.run(
        ['git', '-C', str(helper_root), 'show', f'HEAD:{relative}'],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        fail(f'Could not read pinned blob {relative}: {detail}')
    return result.stdout


def current_file(helper_root: Path, relative: str) -> tuple[bytes, int]:
    path = helper_root / relative
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            fail(f'{path} is not a regular file')
        return path.read_bytes(), stat.S_IMODE(info.st_mode)
    except OSError as error:
        fail(f'Could not read {path}: {error}')

def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f'Could not read helper contract {path}: {error}')
    expected = {'compatibility_version', 'report_binding', 'repository',
                'revision', 'targets', 'required_options'}
    if not isinstance(manifest, dict) or set(manifest) != expected:
        fail('Unexpected native helper contract fields')
    if manifest['compatibility_version'] != COMPATIBILITY_VERSION:
        fail('Unsupported native helper contract version')
    if manifest['report_binding'] != REPORT_BINDING:
        fail('Unsupported verified output report binding')
    if manifest['required_options'] != list(REQUIRED_OPTIONS):
        fail('Unexpected native helper option contract')
    if not isinstance(manifest['repository'], str) or not manifest['repository']:
        fail('Helper contract has no repository')
    normalized_repository(manifest['repository'])
    if not isinstance(manifest['revision'], str) or REVISION.fullmatch(manifest['revision']) is None:
        fail('Helper contract has an invalid revision')
    targets = manifest['targets']
    if not isinstance(targets, dict) or set(targets) != set(TARGETS):
        fail('Unexpected helper source target set')
    for entry in targets.values():
        if not isinstance(entry, dict) or set(entry) != {'sha256'}:
            fail('Native helper contracts do not support source transformations')
        digest = entry['sha256']
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            fail('Invalid helper source SHA-256')
    return manifest


def checked_checkout_status(helper_root: Path) -> None:
    result = subprocess.run(
        ['git', '-C', str(helper_root), 'status', '--porcelain=v1', '-z',
         '--untracked-files=all', '--ignored=matching'],
        capture_output=True, check=False,
    )
    if result.returncode or result.stdout:
        fail('Helper checkout must be pristine, including untracked and ignored files')


def run(helper_root: Path, release_url: str, pinned_revision: str,
        manifest_path: Path, source_url: str | None) -> None:
    manifest = load_manifest(manifest_path)
    if pinned_revision != manifest['revision']:
        fail('Pinned revision does not match native helper contract')
    # Retained positional argument for existing callers; passed to patch.py as
    # a native argv value later. It is never interpolated into Python source.
    if not release_url or any(ord(c) < 32 or ord(c) == 127 for c in release_url):
        fail('Invalid release URL')
    checked_repository(helper_root, source_url or manifest['repository'])
    checked_revision(helper_root, pinned_revision)
    checked_checkout_status(helper_root)
    for relative in TARGETS:
        pinned = head_blob(helper_root, relative)
        if hashlib.sha256(pinned).hexdigest() != manifest['targets'][relative]['sha256']:
            fail(f'Pinned blob hash differs from helper contract: {relative}')
        current, _ = current_file(helper_root, relative)
        if current != pinned:
            fail(f'Helper source differs from pinned blob: {relative}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--source', help='effective clone URL for the helper checkout')
    parser.add_argument('helper_root', type=Path)
    parser.add_argument('release_url')
    parser.add_argument('pinned_revision')
    args = parser.parse_args()
    try:
        run(args.helper_root, args.release_url, args.pinned_revision,
            args.manifest, args.source)
    except CompatibilityError as error:
        print(f'Helper contract rejected: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
