#!/usr/bin/env python3
"""Exercise committed extension staging and refusal of changed trusted inputs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path('tools/modules/build_adapter_extension.py')
CONFIG = Path('tools/modules/adapter_extension.json')


class ExtensionStagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.helper = self.root / 'helper'
        self.repo.mkdir()
        self.helper.mkdir()
        (self.helper / 'registry.py').write_bytes(b'unchanged helper sentinel\n')
        self.config = json.loads((ROOT / CONFIG).read_text())
        inputs = {BUILDER, CONFIG} | {
            Path(item['source_path'])
            for item in self.config['adapters'] + self.config['catalog_manifests']
        }
        for path in inputs:
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, self.repo / path)
        self.git('init', '-q')
        self.commit()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args],
                              check=True, capture_output=True, text=True)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'reviewed extension fixture')

    def stage(self, output=None):
        return subprocess.run([
            sys.executable, str(self.repo / BUILDER),
            '--repository-root', str(self.repo), '--helper-root', str(self.helper),
            '--output', str(output or self.root / 'extension'),
        ], capture_output=True, text=True)

    def test_current_reviewed_inputs_stage_without_helper_changes(self):
        result = self.stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        import tomllib
        extension = tomllib.loads(Path(result.stdout.strip()).read_text())
        self.assertEqual(len(extension['adapters']), len(self.config['adapters']))
        for entry in self.config['adapters'] + self.config['catalog_manifests']:
            self.assertEqual((self.root / 'extension' / entry['source']).read_bytes(),
                             (ROOT / entry['source_path']).read_bytes())
        self.assertEqual(list(self.helper.iterdir()), [self.helper / 'registry.py'])
        self.assertEqual((self.helper / 'registry.py').read_bytes(),
                         b'unchanged helper sentinel\n')

    def test_dirty_adapter_is_rejected_before_staging(self):
        path = self.repo / self.config['adapters'][0]['source_path']
        path.write_bytes(path.read_bytes() + b'\n# changed\n')
        result = self.stage()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not clean and committed', result.stderr)
        self.assertFalse((self.root / 'extension').exists())

    def test_committed_stale_digest_is_rejected(self):
        self.config['catalog_manifests'][0]['sha256'] = '0' * 64
        (self.repo / CONFIG).write_text(json.dumps(self.config))
        self.commit()
        result = self.stage()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reviewed digest', result.stderr)
        self.assertFalse((self.root / 'extension').exists())

    def test_staging_inside_helper_is_rejected(self):
        result = self.stage(self.helper / 'extension')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside the helper checkout', result.stderr)
        self.assertFalse((self.helper / 'extension').exists())


if __name__ == '__main__':
    unittest.main()
