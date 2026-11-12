#!/usr/bin/env python3
"""Native helper validation is repeatable and never rewrites source."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('contract', ROOT / 'tools/compat/avbroot_setup_compat.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class HelperContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'helper'
        self.repo.mkdir()
        self.source = b'# native helper fixture\n'
        (self.repo / 'patch.py').write_bytes(self.source)
        (self.repo / '.gitignore').write_text('__pycache__/\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
        self.git('remote', 'add', 'origin', 'https://github.com/0cwa/my-avbroot-setup')
        self.revision = self.git('rev-parse', 'HEAD').strip()
        self.manifest = {
            'compatibility_version': 2,
            'report_binding': 'verified-output-sha256-v1',
            'repository': 'https://github.com/0cwa/my-avbroot-setup',
            'revision': self.revision,
            'targets': {'patch.py': {'sha256': hashlib.sha256(self.source).hexdigest()}},
            'required_options': ['--release-url', '--adapter-extension'],
        }
        self.path = self.root / 'contract.json'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True)

    def run_contract(self, **kwargs):
        self.path.write_text(json.dumps(self.manifest))
        contract.run(self.repo, kwargs.get('url', "https://example.invalid/ota'quoted.zip"),
                     kwargs.get('revision', self.revision), self.path, kwargs.get('source'))

    def test_pristine_source_and_mode_unchanged_on_repeated_runs(self):
        before = (self.repo / 'patch.py').stat()
        self.run_contract()
        self.run_contract()
        after = (self.repo / 'patch.py').stat()
        self.assertEqual((before.st_mtime_ns, before.st_mode), (after.st_mtime_ns, after.st_mode))
        self.assertEqual((self.repo / 'patch.py').read_bytes(), self.source)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_dirty_target_rejected_without_rewrite(self):
        (self.repo / 'patch.py').write_bytes(b'changed')
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()
        self.assertEqual((self.repo / 'patch.py').read_bytes(), b'changed')

    def test_staged_source_rejected(self):
        (self.repo / 'patch.py').write_bytes(b'changed')
        self.git('add', 'patch.py')
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_untracked_file_rejected(self):
        (self.repo / 'injected.py').write_text('pass')
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_ignored_python_cache_rejected(self):
        (self.repo / '__pycache__').mkdir()
        (self.repo / '__pycache__/injected.pyc').write_bytes(b'cached')
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_wrong_pinned_blob_hash_rejected(self):
        self.manifest['targets']['patch.py']['sha256'] = '0' * 64
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_wrong_revision_argument_rejected(self):
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract(revision='0' * 40)

    def test_wrong_checkout_revision_rejected(self):
        self.manifest['revision'] = '0' * 40
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract(revision='0' * 40)

    def test_wrong_origin_rejected(self):
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/wrong/repo')
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_equivalent_ssh_origin_accepted(self):
        self.git('remote', 'set-url', 'origin', 'git@github.com:0cwa/my-avbroot-setup.git')
        self.run_contract()

    def test_effective_source_override_must_match_origin(self):
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/clone')
        self.run_contract(source='https://example.invalid/clone')
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract(source='https://example.invalid/other')

    def test_authenticated_source_rejected_without_echo(self):
        with self.assertRaises(contract.CompatibilityError) as failure:
            self.run_contract(source='https://not-a-real-secret@example.invalid/clone')
        self.assertNotIn('not-a-real-secret', str(failure.exception))

    def test_symlink_target_rejected(self):
        (self.repo / 'patch.py').unlink()
        other = self.root / 'other.py'
        other.write_bytes(self.source)
        (self.repo / 'patch.py').symlink_to(other)
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_old_transform_contract_is_rejected(self):
        self.manifest['compatibility_version'] = 1
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_source_transform_entry_is_rejected(self):
        self.manifest['targets']['patch.py']['replacement'] = {'kind': 'old'}
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_missing_native_option_rejected(self):
        self.manifest['required_options'].pop()
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract()

    def test_control_character_url_rejected(self):
        with self.assertRaises(contract.CompatibilityError):
            self.run_contract(url='https://example.invalid/ota\nextra')


if __name__ == '__main__':
    unittest.main()
