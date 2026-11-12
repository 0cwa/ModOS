#!/usr/bin/env python3
"""Prove the selection checker detects missing production consumers."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('selection', ROOT / 'tools/modules/check_selection.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class SelectionContractTest(unittest.TestCase):
    def setUp(self):
        self.index = json.loads((ROOT / 'tools/modules/selection.json').read_text())
        self.sources = {p: (ROOT / p).read_text() for p in checker.SOURCES}

    def check(self):
        checker.check_sources(ROOT, self.index, self.sources)

    def replace(self, path, before, after):
        self.assertIn(before, self.sources[path], 'mutation must change a real consumer')
        self.sources[path] = self.sources[path].replace(before, after, 1)

    def test_current_consumers(self):
        self.check()

    def test_unindexed_module(self):
        self.index['modules'].pop()
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.check()

    def test_duplicate_identity(self):
        self.index['modules'].append(copy.deepcopy(self.index['modules'][0]))
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.check()

    def test_miswired_config(self):
        self.index['modules'][0]['config'] = 'unknown_toggle'
        with self.assertRaisesRegex(ValueError, 'config identity'):
            self.check()

    def test_missing_helper_argument(self):
        self.replace('src/util_functions.sh', '    "bcr:BCR"\n', '')
        with self.assertRaisesRegex(ValueError, 'argument routing'):
            self.check()

    def test_miswired_helper_argument(self):
        self.replace('src/util_functions.sh', '"bcr:BCR"', '"bcr:CUSTOTA"')
        with self.assertRaisesRegex(ValueError, 'argument routing'):
            self.check()

    def test_missing_workflow_forwarding(self):
        self.replace('.github/workflows/build-rom.yml', '  ADDITIONALS_SHIZUKU:', '  OMITTED_SHIZUKU:')
        with self.assertRaisesRegex(ValueError, 'workflow environment'):
            self.check()

    def test_miswired_workflow_input(self):
        self.replace('.github/workflows/build-rom.yml', 'ADDITIONALS_SHIZUKU: ${{ inputs.shizuku }}', 'ADDITIONALS_SHIZUKU: ${{ inputs.msd }}')
        with self.assertRaisesRegex(ValueError, 'workflow environment'):
            self.check()

    def test_workflow_default_drift(self):
        self.replace('.github/workflows/build-rom.yml', '      shizuku:\n', '      omitted-shizuku:\n')
        with self.assertRaisesRegex(ValueError, 'workflow input'):
            self.check()

    def test_missing_identity_assignment(self):
        self.replace('src/rom_profiles.sh', '  SELECTION_MODULE_BCR="${ADDITIONALS[BCR]}"\n', '')
        with self.assertRaisesRegex(ValueError, 'fingerprint assignment'):
            self.check()

    def test_missing_identity_serialization(self):
        self.replace('src/ci/selection_variant.sh', '"module.bcr=${SELECTION_MODULE_BCR}"', '"omitted=true"')
        with self.assertRaisesRegex(ValueError, 'fingerprint assignment/serialization'):
            self.check()

    def test_missing_required_identity_field(self):
        self.replace('src/ci/selection_variant.sh', '    SELECTION_MODULE_BCR\n', '')
        with self.assertRaisesRegex(ValueError, 'required fingerprint'):
            self.check()

    def test_missing_health_registration(self):
        self.replace('src/device/check_pdx235_modules.py', '("bcr", "ADDITIONALS[BCR]", bcr),', '("bcr", "OMITTED[BCR]", bcr),')
        with self.assertRaisesRegex(ValueError, 'health registration'):
            self.check()

    def test_missing_adapter_preparation(self):
        self.replace('src/util_functions.sh', '! prepare_shizuku_module "${my_avbroot_setup}"', '! omitted_preparation "${my_avbroot_setup}"')
        with self.assertRaisesRegex(ValueError, 'adapter preparation'):
            self.check()

    def test_missing_rom_policy(self):
        self.replace('src/rom_profiles.sh', 'grapheneos) ADDITIONALS[BCR]=false', 'grapheneos) :')
        with self.assertRaisesRegex(ValueError, 'GrapheneOS exclusion'):
            self.check()


if __name__ == '__main__':
    unittest.main()
