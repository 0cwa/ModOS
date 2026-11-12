#!/usr/bin/env python3
"""Catalog adapters consume verified readers rather than environment paths."""
import contextlib
import hashlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import exfatprogs_fsck, shizuku


class CatalogPayloadContextTest(unittest.TestCase):
    def cases(self):
        return ((shizuku, 'shizuku', 'APK_SHA256', 'APK_SIZE', shizuku.ShizukuModuleError),
                (exfatprogs_fsck, 'exfatprogs-fsck', 'PACKAGE_SHA256', 'PACKAGE_SIZE', exfatprogs_fsck.ExfatFsckError))

    def fixture(self, module_id, payload=b'fixture payload'):
        calls = []
        @contextlib.contextmanager
        def opened():
            calls.append('open')
            yield io.BytesIO(payload)
        artifact = SimpleNamespace(role='injection-input', size=len(payload),
                                   sha256=hashlib.sha256(payload).hexdigest(), open=opened)
        context = SimpleNamespace(module_id=module_id, artifact=lambda name: artifact)
        return context, artifact, calls

    def test_reads_capability_without_environment_path(self):
        for module, module_id, digest, size, _ in self.cases():
            with self.subTest(module=module_id):
                context, artifact, calls = self.fixture(module_id)
                with patch.object(module, digest, artifact.sha256), patch.object(module, size, artifact.size):
                    self.assertEqual(module._context_payload(context), b'fixture payload')
                self.assertEqual(calls, ['open'])

    def test_mismatched_lock_metadata_fails_before_read(self):
        for module, module_id, digest, size, error in self.cases():
            for attribute, value in (('role', 'corresponding-source'), ('size', 999), ('sha256', '0' * 64)):
                with self.subTest(module=module_id, mismatch=attribute):
                    context, artifact, calls = self.fixture(module_id)
                    expected_digest, expected_size = artifact.sha256, artifact.size
                    setattr(artifact, attribute, value)
                    with patch.object(module, digest, expected_digest), patch.object(module, size, expected_size):
                        with self.assertRaises(error):
                            module._context_payload(context)
                    self.assertEqual(calls, [])

    def test_changed_content_fails_after_read(self):
        for module, module_id, digest, size, error in self.cases():
            with self.subTest(module=module_id):
                context, artifact, calls = self.fixture(module_id)
                artifact.sha256 = hashlib.sha256(b'another payload').hexdigest()
                with patch.object(module, digest, artifact.sha256), patch.object(module, size, artifact.size):
                    with self.assertRaises(error):
                        module._context_payload(context)
                self.assertEqual(calls, ['open'])

    def test_wrong_module_cannot_use_the_payload(self):
        for module, module_id, digest, size, error in self.cases():
            with self.subTest(module=module_id):
                context, artifact, calls = self.fixture('different-module')
                with patch.object(module, digest, artifact.sha256), patch.object(module, size, artifact.size):
                    with self.assertRaises(error):
                        module._context_payload(context)
                self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
