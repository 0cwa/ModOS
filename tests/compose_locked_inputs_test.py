#!/usr/bin/env python3
"""Keep composed locked profiles bound to the selected target and scope."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'compose_locked_inputs', REPOSITORY / 'tools/modules/compose_locked_inputs.py'
)
compose_inputs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(compose_inputs)


TARGET = {
    'rom_family': 'lineageos',
    'root_mode': 'rootless',
    'abi': 'arm64-v8a',
    'api_level': 36,
    'output_scope': 'local-unpublished',
}


class ComposeLockedInputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = self.root / 'lock.json'
        self.lock.write_text(json.dumps({
            'schema_version': 1,
            'modules': [
                {'id': 'base-module', 'dependencies': ['base-dependency']},
                {'id': 'base-dependency'},
                {'id': 'shizuku'},
            ],
        }))
        self.base = self.root / 'base.toml'
        self.write_profile()

    def write_profile(self, **overrides):
        values = {
            'schema_version': 1,
            'id': 'reviewed-base-profile',
            **TARGET,
            'enabled_modules': ['base-module', 'base-dependency'],
            'acknowledgements': ['reviewed-base-ack'],
        }
        values.update(overrides)
        self.base.write_text(compose_inputs.dump_toml(values))

    def compose(self):
        compose_inputs.compose(
            [self.lock], ['shizuku'], self.base,
            self.root / 'out/combined.json',
            self.root / 'out/profile.toml',
            self.root / 'out/modules.txt',
            TARGET['rom_family'], TARGET['root_mode'], TARGET['abi'],
            TARGET['api_level'], TARGET['output_scope'],
        )

    def test_mismatched_target_facts_reject_profile_base(self):
        mismatches = {
            'rom_family': 'grapheneos',
            'root_mode': 'rooted',
            'abi': 'x86_64',
            'api_level': 37,
            'output_scope': 'published',
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                self.write_profile(**{field: value})
                with self.assertRaisesRegex(
                    compose_inputs.ComposeError,
                    f'profile target mismatch for {field}',
                ):
                    self.compose()

    def test_bool_is_not_accepted_as_numeric_api_level(self):
        self.write_profile(api_level=True)
        with self.assertRaisesRegex(
            compose_inputs.ComposeError,
            'profile target mismatch for api_level',
        ):
            self.compose()

    def test_matching_base_preserves_explicit_modules_and_profile_policy(self):
        self.compose()
        import tomllib

        profile = tomllib.loads((self.root / 'out/profile.toml').read_text())
        self.assertEqual(
            profile['enabled_modules'],
            ['base-dependency', 'base-module', 'shizuku'],
        )
        self.assertEqual(profile['acknowledgements'], ['reviewed-base-ack'])
        self.assertEqual(profile['id'], 'reviewed-base-profile')
        self.assertEqual(
            (self.root / 'out/modules.txt').read_text().splitlines(),
            ['base-dependency', 'base-module', 'shizuku'],
        )


if __name__ == '__main__':
    unittest.main()
