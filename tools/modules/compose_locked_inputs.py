#!/usr/bin/env python3
"""Combine checked lock records and module selections for one helper invocation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib


class ComposeError(ValueError):
    """Locked inputs cannot be combined without changing their meaning."""


def fail(message: str) -> None:
    raise ComposeError(message)


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{ ' + ', '.join(
            f'{toml_key(key)} = {toml_value(item)}'
            for key, item in sorted(value.items())
        ) + ' }'
    fail(f'unsupported TOML value: {type(value).__name__}')


def toml_key(value: str) -> str:
    if re.fullmatch(r'[A-Za-z0-9_-]+', value):
        return value
    return json.dumps(value, ensure_ascii=True)


def dump_toml(value: dict[str, object]) -> str:
    lines: list[str] = []

    def table(data: dict[str, object], prefix: tuple[str, ...] = ()) -> None:
        scalars = [(key, item) for key, item in data.items() if not isinstance(item, dict)]
        children = [(key, item) for key, item in data.items() if isinstance(item, dict)]
        if prefix:
            if lines and lines[-1] != '':
                lines.append('')
            lines.append('[' + '.'.join(toml_key(key) for key in prefix) + ']')
        for key, item in sorted(scalars):
            lines.append(f'{toml_key(key)} = {toml_value(item)}')
        for key, item in sorted(children):
            table(item, (*prefix, key))

    table(value)
    return '\n'.join(lines) + '\n'


def compose(
    lock_paths: list[Path],
    selected_ids: list[str],
    profile_base: Path | None,
    lock_output: Path,
    profile_output: Path,
    modules_output: Path,
    rom_family: str,
    root_mode: str,
    abi: str,
    api_level: int,
    output_scope: str,
) -> None:
    records: dict[str, dict[str, object]] = {}
    for path in lock_paths:
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ComposeError(f'cannot read locked module input: {path}') from error
        if not isinstance(raw, dict) or raw.get('schema_version') != 1 or not isinstance(raw.get('modules'), list):
            fail(f'unsupported locked input schema: {path}')
        for record in raw['modules']:
            if not isinstance(record, dict) or not isinstance(record.get('id'), str):
                fail(f'locked input contains an invalid module record: {path}')
            module_id = record['id']
            if module_id in records:
                fail(f'duplicate module record while composing locks: {module_id}')
            records[module_id] = record

    if profile_base is None:
        if not selected_ids:
            fail('cannot compose an empty locked module selection')
        profile: dict[str, object] = {
            'schema_version': 1,
            'id': 'modos-' + '-'.join(sorted(selected_ids)),
            'rom_family': rom_family,
            'root_mode': root_mode,
            'abi': abi,
            'api_level': api_level,
            'output_scope': output_scope,
            'enabled_modules': sorted(set(selected_ids)),
            'acknowledgements': [],
            'experimental_acknowledgements': [],
            'capabilities': {
                'root_providers': [],
                'zygisk_providers': [],
                'selective_signature_spoofing': 'microg' in selected_ids,
                'product_priv_app': 'microg' in selected_ids,
                'custom_init_selinux': False,
            },
        }
    else:
        try:
            profile = tomllib.loads(profile_base.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ComposeError(f'cannot read locked module profile: {profile_base}') from error
        if not isinstance(profile, dict) or not isinstance(profile.get('enabled_modules'), list):
            fail('locked module profile has no enabled_modules array')
        expected_facts: dict[str, object] = {
            'rom_family': rom_family,
            'root_mode': root_mode,
            'abi': abi,
            'api_level': api_level,
            'output_scope': output_scope,
        }
        for field, expected in expected_facts.items():
            actual = profile.get(field)
            # TOML booleans are Python bools, which compare equal to 0 and 1.
            # Require the same concrete scalar type as the selected target.
            if type(actual) is not type(expected) or actual != expected:
                fail(
                    f'locked module profile target mismatch for {field}: '
                    f'expected {expected!r}, got {actual!r}'
                )
        if any(not isinstance(item, str) for item in profile['enabled_modules']):
            fail('locked module profile has a non-string module selection')
        profile['enabled_modules'] = sorted(set(profile['enabled_modules']) | set(selected_ids))

    enabled = profile.get('enabled_modules')
    if not isinstance(enabled, list) or not enabled or any(not isinstance(item, str) for item in enabled):
        fail('composed profile has an invalid module selection')
    missing = set(enabled) - set(records)
    if missing:
        fail('composed profile modules are absent from locks: ' + ', '.join(sorted(missing)))

    lock_value = {
        'schema_version': 1,
        'modules': [records[module_id] for module_id in sorted(records)],
    }
    lock_output.parent.mkdir(parents=True, exist_ok=True)
    profile_output.parent.mkdir(parents=True, exist_ok=True)
    modules_output.parent.mkdir(parents=True, exist_ok=True)
    lock_output.write_text(json.dumps(lock_value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    profile_output.write_text(dump_toml(profile), encoding='utf-8')
    modules_output.write_text(''.join(module + '\n' for module in sorted(enabled)), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', action='append', type=Path, required=True)
    parser.add_argument('--selected', action='append', default=[])
    parser.add_argument('--profile-base', type=Path)
    parser.add_argument('--lock-output', type=Path, required=True)
    parser.add_argument('--profile-output', type=Path, required=True)
    parser.add_argument('--modules-output', type=Path, required=True)
    parser.add_argument('--rom-family', required=True)
    parser.add_argument('--root-mode', choices=('rootless', 'rooted'), required=True)
    parser.add_argument('--abi', required=True)
    parser.add_argument('--api-level', type=int, required=True)
    parser.add_argument('--output-scope', choices=('local-unpublished', 'private', 'shared', 'published'), required=True)
    args = parser.parse_args()
    try:
        compose(
            args.lock, args.selected, args.profile_base, args.lock_output,
            args.profile_output, args.modules_output, args.rom_family,
            args.root_mode, args.abi, args.api_level, args.output_scope,
        )
    except (ComposeError, OSError, ValueError) as error:
        print(f'locked module composition rejected: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
