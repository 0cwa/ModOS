#!/usr/bin/env python3
"""Stage ModOS's reviewed adapters outside the pinned helper checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


CONFIG = Path('tools/modules/adapter_extension.json')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
ENTRY_FIELDS = {
    'id', 'kind', 'source_path', 'source', 'sha256', 'target',
    'verification_schemes', 'trusted_signers', 'digest_required',
}


class ExtensionBuildError(ValueError):
    """The trusted ModOS adapter input set is malformed or changed."""


def fail(message: str) -> None:
    raise ExtensionBuildError(message)


def relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        fail(f'{label} must be a nonempty relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in path.parts):
        fail(f'{label} contains an unsafe path')
    return path


def git_bytes(root: Path, path: PurePosixPath) -> bytes:
    try:
        return subprocess.check_output(
            ['git', '-C', str(root), 'show', f'HEAD:{path.as_posix()}'],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as error:
        raise ExtensionBuildError(f'extension input is not committed: {path}') from error


def checked_input(root: Path, path: PurePosixPath, expected: object) -> bytes:
    relative = path.as_posix()
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        fail(f'extension input has an invalid SHA-256: {relative}')
    try:
        subprocess.check_output(
            ['git', '-C', str(root), 'ls-files', '--error-unmatch', '--', relative],
            stderr=subprocess.DEVNULL,
        )
        status = subprocess.check_output(
            ['git', '-C', str(root), 'status', '--porcelain=v1', '-z', '--', relative],
        )
    except subprocess.CalledProcessError as error:
        raise ExtensionBuildError(f'extension input is not tracked: {relative}') from error
    if status:
        fail(f'extension input is not clean and committed: {relative}')
    source = root.joinpath(*path.parts)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise ExtensionBuildError(f'extension input is unavailable: {relative}') from error
    if not stat.S_ISREG(metadata.st_mode):
        fail(f'extension input is not a regular file: {relative}')
    current = source.read_bytes()
    committed = git_bytes(root, path)
    if current != committed or hashlib.sha256(current).hexdigest() != expected:
        fail(f'extension input differs from its reviewed digest: {relative}')
    return current


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_extension(config: dict[str, object]) -> bytes:
    lines = ['schema_version = 1']
    directories = config['catalog_directories']
    lines.append('catalog_directories = [' + ', '.join(toml_string(item) for item in directories) + ']')
    for item in config['adapters']:
        lines.extend(('', '[[adapters]]'))
        for key in ('id', 'kind', 'source', 'sha256', 'target'):
            lines.append(f'{key} = {toml_string(item[key])}')
        for key in ('verification_schemes', 'trusted_signers'):
            lines.append(f'{key} = [' + ', '.join(toml_string(value) for value in item[key]) + ']')
        lines.append(f'digest_required = {str(item["digest_required"]).lower()}')
    return ('\n'.join(lines) + '\n').encode('utf-8')


def build(root: Path, helper_root: Path, output: Path) -> Path:
    root = root.resolve(strict=True)
    helper_root = helper_root.resolve(strict=True)
    output = output.resolve(strict=False)
    script = Path(__file__).resolve(strict=True)
    try:
        script_path = PurePosixPath(script.relative_to(root).as_posix())
    except ValueError as error:
        raise ExtensionBuildError('adapter extension builder is outside the ModOS repository') from error
    checked_input(root, script_path, hashlib.sha256(git_bytes(root, script_path)).hexdigest())
    try:
        output.relative_to(helper_root)
    except ValueError:
        pass
    else:
        fail('adapter extension staging directory must be outside the helper checkout')

    config_bytes = checked_input(root, CONFIG, hashlib.sha256(
        git_bytes(root, CONFIG)
    ).hexdigest())
    try:
        config = json.loads(config_bytes)
    except json.JSONDecodeError as error:
        raise ExtensionBuildError('adapter extension config is invalid JSON') from error
    if not isinstance(config, dict) or set(config) != {
        'schema_version', 'catalog_directories', 'catalog_manifests', 'adapters'
    } or config['schema_version'] != 1:
        fail('adapter extension config has unknown or unsupported fields')
    if not isinstance(config['catalog_directories'], list) or not all(
        isinstance(value, str) and value for value in config['catalog_directories']
    ):
        fail('adapter extension catalog directories are invalid')
    if not isinstance(config['catalog_manifests'], list) or not isinstance(config['adapters'], list):
        fail('adapter extension input lists are invalid')

    staged: list[tuple[PurePosixPath, bytes]] = []
    manifest_sources: set[str] = set()
    for entry in config['catalog_manifests']:
        if not isinstance(entry, dict) or set(entry) != {'source_path', 'source', 'sha256'}:
            fail('catalog manifest input has unknown or missing fields')
        source_path = relative_path(entry['source_path'], 'catalog source path')
        target = relative_path(entry['source'], 'catalog staging path')
        if target.as_posix() in manifest_sources:
            fail('duplicate staged catalog manifest path')
        manifest_sources.add(target.as_posix())
        staged.append((target, checked_input(root, source_path, entry['sha256'])))

    adapter_ids: set[str] = set()
    staged_sources: set[str] = set()
    for entry in config['adapters']:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            fail('adapter entry has unknown or missing fields')
        adapter_id = entry['id']
        if not isinstance(adapter_id, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', adapter_id):
            fail('adapter entry has an invalid ID')
        if adapter_id in adapter_ids:
            fail(f'duplicate adapter ID: {adapter_id}')
        adapter_ids.add(adapter_id)
        if entry['kind'] not in ('legacy-cli', 'catalog-legacy'):
            fail(f'unsupported ModOS adapter kind: {entry["kind"]!r}')
        for name in ('id', 'kind', 'target'):
            if not isinstance(entry[name], str) or not entry[name]:
                fail(f'adapter {name} must be a nonempty string')
        if not entry['target'].isidentifier():
            fail(f'adapter target must be a Python identifier: {adapter_id}')
        if not isinstance(entry['verification_schemes'], list) or not entry['verification_schemes']:
            fail(f'adapter verification schemes are empty: {adapter_id}')
        if not isinstance(entry['trusted_signers'], list) or not all(
            isinstance(value, str) and value for value in entry['trusted_signers']
        ):
            fail(f'adapter trusted signer list is invalid: {adapter_id}')
        if type(entry['digest_required']) is not bool:
            fail(f'adapter digest requirement is invalid: {adapter_id}')
        source_path = relative_path(entry['source_path'], 'adapter source path')
        target = relative_path(entry['source'], 'adapter staging path')
        if target.as_posix() in staged_sources:
            fail('duplicate staged adapter source path')
        staged_sources.add(target.as_posix())
        entry['sha256'] = hashlib.sha256(
            checked_input(root, source_path, entry['sha256'])
        ).hexdigest()
        staged.append((target, checked_input(root, source_path, entry['sha256'])))

    if output.exists() or output.is_symlink():
        fail('adapter extension staging directory already exists')
    output.mkdir(parents=True)
    for relative, data in staged:
        destination = output.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    extension_path = output / 'adapter-extension.toml'
    extension_path.write_bytes(render_extension(config))
    return extension_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-root', type=Path, required=True)
    parser.add_argument('--helper-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(build(args.repository_root, args.helper_root, args.output))
    except (ExtensionBuildError, OSError, ValueError) as error:
        print(f'adapter extension rejected: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
