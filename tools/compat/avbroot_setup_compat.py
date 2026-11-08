#!/usr/bin/env python3
"""Apply the reviewed compatibility delta to a pinned my-avbroot-setup checkout."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


COMPATIBILITY_VERSION = 1
DEFAULT_MANIFEST = Path(__file__).with_suffix('.json')
TARGETS = ('patch.py',)
TARGET_PATHS = frozenset(TARGETS)
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
REVISION = re.compile(r'[0-9a-f]{40}\Z')
PATCH_REPLACEMENT = {
    'kind': 'qualified_call_argument',
    'qualified_name': 'external.generate_update_info',
    'arguments': ['update_info', 'args.output.name'],
    'to': '{release_url!r}',
}
SCP_SOURCE = re.compile(
    r'(?P<username>[^@/:]+)@(?P<host>\[[^\]]+\]|[^:/]+):(?P<path>.+)\Z'
)


class CompatibilityError(Exception):
    """Raised when the pinned source or working-tree state is unexpected."""


def fail(message: str) -> None:
    raise CompatibilityError(message)


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f'Could not read compatibility manifest {path}: {error}')
    if not isinstance(manifest, dict):
        fail(f'Compatibility manifest {path} must be an object')
    if manifest.get('compatibility_version') != COMPATIBILITY_VERSION:
        fail(f'Unsupported compatibility manifest version in {path}')
    if not isinstance(manifest.get('repository'), str) or not manifest['repository']:
        fail(f'Compatibility manifest {path} has no repository')
    revision = manifest.get('revision')
    if not isinstance(revision, str) or REVISION.fullmatch(revision) is None:
        fail(f'Compatibility manifest {path} has an invalid revision')
    targets = manifest.get('targets')
    if not isinstance(targets, dict) or set(targets) != TARGET_PATHS:
        fail(f'Compatibility manifest {path} has an unexpected target set')
    for relative in TARGETS:
        entry = targets[relative]
        digest = entry.get('sha256') if isinstance(entry, dict) else None
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            fail(f'Compatibility manifest has an invalid hash for {relative}')
        replacement = entry.get('replacement')
        if not isinstance(replacement, dict):
            fail(f'Compatibility manifest has no replacement for {relative}')
        if replacement != PATCH_REPLACEMENT:
            fail(f'Compatibility manifest has an invalid patch replacement for {relative}')
    return manifest


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


def checked_checkout_status(helper_root: Path) -> set[str]:
    result = subprocess.run(
        [
            'git', '-C', str(helper_root), 'status', '--porcelain=v1', '-z',
            '--untracked-files=all', '--ignored=matching',
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        fail('Could not inspect helper checkout status')

    changed: set[str] = set()
    for record in result.stdout.split(b'\0'):
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b' ':
            fail('Helper checkout has an unexpected status record')
        status = record[:2].decode('ascii', errors='replace')
        try:
            relative = record[3:].decode('utf-8')
        except UnicodeDecodeError:
            fail('Helper checkout contains a path with an invalid name encoding')
        if status == '??':
            fail('Helper checkout contains an unexpected untracked file')
        if status == '!!':
            fail('Helper checkout contains an unexpected ignored file')
        if status != ' M':
            fail('Helper checkout contains an unexpected tracked change')
        if relative not in TARGET_PATHS:
            fail('Helper checkout contains a change outside the compatibility targets')
        changed.add(relative)
    return changed


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


def source_span(source: bytes, node: ast.AST, relative: str) -> tuple[int, int]:
    line_starts = [0]
    for index, byte in enumerate(source):
        if byte == 0x0A:
            line_starts.append(index + 1)
    values = tuple(getattr(node, name, None) for name in (
        'lineno', 'col_offset', 'end_lineno', 'end_col_offset'))
    if not all(isinstance(value, int) for value in values):
        fail(f'Missing source span in {relative}')
    start_line, start_column, end_line, end_column = values
    if start_line < 1 or end_line < start_line:
        fail(f'Invalid source span in {relative}')
    if start_line > len(line_starts) or end_line > len(line_starts):
        fail(f'Source span is outside {relative}')
    start = line_starts[start_line - 1] + start_column
    end = line_starts[end_line - 1] + end_column
    if not 0 <= start <= end <= len(source):
        fail(f'Source span is outside {relative}')
    return start, end


def parse_source(source: bytes, relative: str) -> ast.Module:
    try:
        return ast.parse(source.decode('utf-8'), filename=relative)
    except (UnicodeDecodeError, SyntaxError) as error:
        fail(f'Could not parse {relative}: {error}')


def rewrite_patch(source: bytes, relative: str, replacement: dict, release_url: str) -> bytes:
    tree = parse_source(source, relative)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'external'
        and node.func.attr == 'generate_update_info'
    ]
    if len(calls) != 1:
        fail(f'Expected exactly one qualified update-info call in {relative}')

    call = calls[0]
    if (
        len(call.args) != 2
        or call.keywords
        or not isinstance(call.args[0], ast.Name)
        or call.args[0].id != 'update_info'
    ):
        fail(f'Unexpected qualified update-info call shape in {relative}')
    argument = call.args[1]
    if not (
        isinstance(argument, ast.Attribute)
        and argument.attr == 'name'
        and isinstance(argument.value, ast.Attribute)
        and argument.value.attr == 'output'
        and isinstance(argument.value.value, ast.Name)
        and argument.value.value.id == 'args'
    ):
        fail(f'Expected args.output.name as the update-info argument in {relative}')

    call_start, call_end = source_span(source, call, relative)
    argument_start, argument_end = source_span(source, argument, relative)
    if not call_start <= argument_start <= argument_end <= call_end:
        fail(f'Update-info argument is outside its call in {relative}')
    old_argument = replacement['arguments'][1].encode('utf-8')
    if source[argument_start:argument_end] != old_argument:
        fail(f'Expected exact args.output.name source span in {relative}')

    new_argument = repr(release_url).encode('utf-8')
    call_source = source[call_start:call_end]
    relative_start = argument_start - call_start
    relative_end = argument_end - call_start
    rewritten_call = call_source[:relative_start] + new_argument + call_source[relative_end:]
    return source[:call_start] + rewritten_call + source[call_end:]


def stage_file(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as staged:
        staged.write(data)
        staged.flush()
        os.fsync(staged.fileno())
    os.chmod(path, mode)


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_before_replace(
    helper_root: Path,
    originals: dict[str, bytes],
    modes: dict[str, int],
    heads: dict[str, bytes],
    pinned_revision: str,
) -> None:
    checked_revision(helper_root, pinned_revision)
    for relative in TARGETS:
        current, mode = current_file(helper_root, relative)
        if current != originals[relative] or mode != modes[relative]:
            fail(f'{relative} changed after validation; refusing to write')
        if head_blob(helper_root, relative) != heads[relative]:
            fail(f'Pinned blob changed after validation for {relative}')


def apply_changes(
    helper_root: Path,
    changed: list[str],
    originals: dict[str, bytes],
    prepared: dict[str, bytes],
    modes: dict[str, int],
    heads: dict[str, bytes],
    pinned_revision: str,
) -> None:
    try:
        with tempfile.TemporaryDirectory(
            prefix='.pixeneos-avbroot-compat-', dir=helper_root
        ) as raw_stage:
            stage_root = Path(raw_stage)
            staged: dict[str, Path] = {}
            backups: dict[str, Path] = {}
            for relative in changed:
                staged[relative] = stage_root / 'new' / relative
                backups[relative] = stage_root / 'old' / relative
                stage_file(staged[relative], prepared[relative], modes[relative])
                stage_file(backups[relative], originals[relative], modes[relative])

            try:
                for relative in changed:
                    py_compile.compile(str(staged[relative]), doraise=True)
            except (OSError, py_compile.PyCompileError) as error:
                fail(f'Could not compile staged compatibility output: {error}')

            # CI gives this checkout a single writer. The final snapshot check
            # and first replace have a small unavoidable TOCTOU window.
            validate_before_replace(helper_root, originals, modes, heads, pinned_revision)

            replaced: list[str] = []
            try:
                for relative in changed:
                    os.replace(staged[relative], helper_root / relative)
                    replaced.append(relative)
                for parent in sorted({(helper_root / relative).parent for relative in changed}):
                    sync_directory(parent)
            except BaseException as error:
                rollback_errors = []
                for relative in reversed(replaced):
                    try:
                        os.replace(backups[relative], helper_root / relative)
                    except BaseException as rollback_error:
                        rollback_errors.append(f'{relative}: {rollback_error}')
                durability_errors = []
                for parent in sorted({(helper_root / relative).parent for relative in replaced}):
                    try:
                        sync_directory(parent)
                    except BaseException as durability_error:
                        durability_errors.append(f'{parent}: {durability_error}')
                if rollback_errors or durability_errors:
                    details = [f'original error: {error}']
                    if rollback_errors:
                        details.append('rollback errors: ' + '; '.join(rollback_errors))
                    if durability_errors:
                        details.append(
                            'post-rollback directory fsync failed; durability is uncertain: '
                            + '; '.join(durability_errors)
                        )
                    raise CompatibilityError(
                        'Compatibility patch failed; ' + ' | '.join(details)
                    ) from error
                raise CompatibilityError(f'Compatibility patch rolled back: {error}') from error
    except CompatibilityError:
        raise
    except (OSError, py_compile.PyCompileError) as error:
        raise CompatibilityError(f'Could not stage compatibility output: {error}') from error


def run(
    helper_root: Path,
    release_url: str,
    pinned_revision: str,
    manifest_path: Path,
    source_url: str | None,
) -> None:
    manifest = load_manifest(manifest_path)
    if pinned_revision != manifest['revision']:
        fail(
            f'Pinned revision argument does not match compatibility manifest: '
            f'{pinned_revision} != {manifest["revision"]}'
        )
    checked_repository(
        helper_root,
        manifest['repository'] if source_url is None else source_url,
    )
    checked_revision(helper_root, pinned_revision)
    status_paths = checked_checkout_status(helper_root)

    entries = manifest['targets']
    heads: dict[str, bytes] = {}
    originals: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    prepared: dict[str, bytes] = {}
    for relative in TARGETS:
        heads[relative] = head_blob(helper_root, relative)
        if hashlib.sha256(heads[relative]).hexdigest() != entries[relative]['sha256']:
            fail(f'Pinned blob hash does not match the manifest for {relative}')
        originals[relative], modes[relative] = current_file(helper_root, relative)
        patched = rewrite_patch(
            heads[relative],
            relative,
            entries[relative]['replacement'],
            release_url,
        )
        if originals[relative] == heads[relative]:
            prepared[relative] = patched
        elif originals[relative] == patched:
            prepared[relative] = originals[relative]
        else:
            fail(f'{relative} is neither pristine nor the exact compatibility state')
        if relative in status_paths and originals[relative] == heads[relative]:
            fail(f'{relative} is modified but not in the recognized compatibility state')

    changed = [relative for relative in TARGETS if prepared[relative] != originals[relative]]
    if not changed:
        return
    apply_changes(helper_root, changed, originals, prepared, modes, heads, pinned_revision)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--source', help='effective clone URL for the helper checkout')
    parser.add_argument('helper_root', type=Path)
    parser.add_argument('release_url')
    parser.add_argument('pinned_revision')
    args = parser.parse_args()
    try:
        run(
            args.helper_root,
            args.release_url,
            args.pinned_revision,
            args.manifest,
            args.source,
        )
    except CompatibilityError as error:
        print(f'Compatibility patch rejected: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
