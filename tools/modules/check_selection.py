#!/usr/bin/env python3
"""Check ModOS selection wiring; the helper still owns the module catalog.

This deliberately checks the repository's explicit shell/YAML declarations,
not arbitrary shell programs. Behavioral tests remain authoritative for policy.
"""
import ast
import json
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    'src/config_schema.sh', 'src/util_functions.sh', 'src/rom_profiles.sh',
    'src/ci/selection_variant.sh', '.github/workflows/build-rom.yml',
    'src/device/check_pdx235_modules.py',
)
# These typed ADDITIONALS switches describe build policy, not catalog modules.
POLICY_KEYS = {'root', 'compatible_sepolicy_patching'}
ROUTES = {'host-tool', 'signed-zip', 'local-adapter', 'locked-bundle'}


def schema_entries(text):
    entries = {}
    for line in text.replace('\\\n', '').splitlines():
        if line.startswith('_config_schema_define '):
            fields = shlex.split(line)
            if len(fields) != 12:
                raise ValueError('unsupported config schema declaration')
            if fields[1] in entries:
                raise ValueError(f'{fields[1]}: duplicate typed config')
            entries[fields[1]] = fields[2:]
    return entries


def function_body(text, name):
    match = re.search(r'^function ' + re.escape(name) + r'\(\) \{\n(.*?)^\}', text, re.M | re.S)
    if not match:
        raise ValueError(f'missing consumer function {name}')
    return match[1]


def check_sources(root, data, sources):
    if data.get('schema_version') != 1:
        raise ValueError('unsupported selection index schema')
    modules = data['modules']
    keys = [m['key'] for m in modules]
    if not keys or any(not re.fullmatch(r'[a-z][a-z0-9_]*', k) for k in keys) or len(keys) != len(set(keys)):
        raise ValueError('module keys must be unique and nonempty')
    schema = schema_entries(sources['src/config_schema.sh'])
    typed_modules = {k for k, v in schema.items() if v[3:5] == ['ADDITIONALS', 'array']} - POLICY_KEYS
    if set(keys) != typed_modules:
        raise ValueError(f'index/typed config module coverage differs: {sorted(set(keys) ^ typed_modules)}')

    setup = sources['src/util_functions.sh']
    argument_body = function_body(setup, 'append_enabled_module_arguments')
    actual_arguments = re.findall(r'"([a-z0-9-]+):([A-Z0-9_]+)"', argument_body)
    expected_arguments = {(m['helper_id'], m['key'].upper()) for m in modules
                          if m['route'] in {'signed-zip', 'local-adapter'}}
    if len(actual_arguments) != len(set(actual_arguments)) or set(actual_arguments) != expected_arguments:
        raise ValueError('helper argument routing differs from selection index')

    profiles = sources['src/rom_profiles.sh']
    fingerprint = function_body(profiles, 'module_selection_fingerprint')
    variant = function_body(sources['src/ci/selection_variant.sh'], 'selection_variant_manifest')
    health_source = ast.parse(sources['src/device/check_pdx235_modules.py'])
    health_registry = {}
    for node in health_source.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MODULES' for t in node.targets):
            for entry in node.value.elts:
                health_registry[ast.literal_eval(entry.elts[1])] = entry.elts[2].id
    workflow = sources['.github/workflows/build-rom.yml']
    configs = set()
    for m in modules:
        key, config = m['key'], m['config']
        if config in configs or config != key:
            raise ValueError(f'{key}: duplicated or miswired config identity')
        configs.add(config)
        if m['route'] not in ROUTES:
            raise ValueError(f'{key}: unknown route')
        entry = schema[config]
        flag = key.upper()
        if entry[0] != f'ADDITIONALS[{flag}]' or entry[2:7] != ['boolean', 'ADDITIONALS', 'array', flag, f'ADDITIONALS_{flag}'] or entry[8] != 'true':
            raise ValueError(f'{key}: typed config mapping/identity differs')
        toggle = key.replace('_', '-')
        match = re.search(r'^      ' + re.escape(toggle) + r':\n(.*?)(?=^      [a-z]|^    [a-z]|\Z)', workflow, re.M | re.S)
        if not match or not re.search(r'^        type: boolean$', match[1], re.M) or not re.search(r'^        default: ' + re.escape(entry[7]) + '$', match[1], re.M):
            raise ValueError(f'{key}: missing/mismatched workflow input/default')
        env = re.search(r'^  ADDITIONALS_' + flag + r': (.+)$', workflow, re.M)
        if not env or not re.search(r'\binputs\.' + re.escape(toggle) + r'(?![\w-])', env[1]):
            raise ValueError(f'{key}: missing workflow environment forwarding')
        field = m['selection_field']
        assignment = f'{field}="${{ADDITIONALS[{flag}]}}"'
        serialized = f'"{m["identity_key"]}=${{{field}}}"'
        if m.get('identity_encoding') == 'enabled-only':
            serialized = f"'{m['identity_key']}=true'"
            enabled_guard = f"if [[ \"${{{field}}}\" == 'true' ]]; then"
            if enabled_guard not in variant:
                raise ValueError(f'{key}: missing enabled-only identity guard')
        if assignment not in fingerprint or serialized not in variant:
            raise ValueError(f'{key}: missing fingerprint assignment/serialization')
        required = re.search(r'local -a required_fields=\((.*?)\n  \)', variant, re.S)
        if not required or field not in required[1].split():
            raise ValueError(f'{key}: missing required fingerprint field')
        health = m.get('health')
        if health:
            if health_registry.get(f'ADDITIONALS[{flag}]') != health or not (root / f'src/device/modos_health/checks/{health}.py').is_file():
                raise ValueError(f'{key}: missing/miswired health registration')
        elif not m.get('health_pending'):
            raise ValueError(f'{key}: missing health coverage needs an explicit reason')
        if m['route'] == 'local-adapter':
            preparation = m['prepare']
            function_body(setup, preparation)
            patch = function_body(setup, 'patch_ota')
            if not re.search(r'! ' + re.escape(preparation) + r' "\$\{my_avbroot_setup\}"', patch):
                raise ValueError(f'{key}: missing adapter preparation call')
        elif m['route'] == 'locked-bundle':
            body = function_body(setup, m['prepare'])
            if '--module-lock' not in body or '--module-profile' not in body or 'module-tool.py' not in body:
                raise ValueError(f'{key}: missing locked-bundle routing')
        if m.get('exclusive') and m['exclusive'] not in keys:
            raise ValueError(f'{key}: unknown exclusive module')
    if not re.search(r'grapheneos\) ADDITIONALS\[BCR\]=false', function_body(profiles, 'apply_rom_module_policy')):
        raise ValueError('bcr: missing GrapheneOS exclusion policy')


def check(root=ROOT, index_path=None):
    data = json.loads((index_path or root / 'tools/modules/selection.json').read_text())
    check_sources(root, data, {path: (root / path).read_text() for path in SOURCES})


if __name__ == '__main__':
    try:
        check(index_path=Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    except (ValueError, KeyError, TypeError, OSError, SyntaxError) as exc:
        print(f'module selection contract: {exc}', file=sys.stderr)
        sys.exit(1)
    print('module selection contract: ok')
