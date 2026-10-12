#!/usr/bin/env python3
"""Behavioral tests for the explicit encrypted device handoff."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('export', 'src/ci/encrypt_device_candidate.py')
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


class EncryptedCandidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transport = tempfile.TemporaryDirectory()
        cls.certs = []
        for identity in ('recipient', 'wrong-recipient'):
            key = Path(cls.transport.name) / (identity + '.key')
            cert = Path(cls.transport.name) / (identity + '.crt')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048',
                            '-nodes', '-days', '1', '-subj', '/CN=' + identity,
                            '-keyout', str(key), '-out', str(cert)],
                           capture_output=True, check=True)
            cls.certs.append((key, cert))
        cls.recipient = base64.b64encode(cls.certs[0][1].read_bytes()).decode()

    @classmethod
    def tearDownClass(cls):
        cls.transport.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ota = self.root / 'candidate.zip'
        self.ota.write_bytes(b'fixture OTA content')
        Path(str(self.ota) + '.selection.json').write_text('{}')
        Path(str(self.ota) + '.microg-patch-report.json').write_text('{}')
        self.public = self.root / 'device-candidate-avb_pkmd.bin'
        self.public.write_bytes(b'fixture public AVB key')
        self.stage = self.root / 'encrypted'

    def encrypt(self, ota=None):
        export.encrypt_candidate(self.root, self.stage, self.recipient,
                                 [ota or self.ota], self.public, microg=True)

    def decrypt(self, identity=0, envelope=1):
        key, cert = self.certs[identity]
        return subprocess.run(['openssl', 'cms', '-decrypt', '-binary',
                               '-inform', 'DER', '-in', str(self.stage / f'candidate-{envelope}.cms.der'),
                               '-recip', str(cert), '-inkey', str(key)], capture_output=True)

    def test_roundtrip_exports_only_allowlisted_members(self):
        self.encrypt()
        self.assertEqual({p.name for p in self.stage.iterdir()}, {'candidate-1.cms.der', 'manifest.json'})
        manifest = json.loads((self.stage / 'manifest.json').read_text())
        self.assertEqual(manifest['content_encryption'], 'AES-256-GCM')
        decrypted = self.decrypt()
        self.assertEqual(decrypted.returncode, 0, decrypted.stderr)
        with tarfile.open(fileobj=io.BytesIO(decrypted.stdout)) as archive:
            self.assertEqual(archive.getnames(), [self.ota.name, self.ota.name + '.selection.json',
                             self.ota.name + '.microg-patch-report.json', self.public.name])
            self.assertEqual(archive.extractfile(self.ota.name).read(), self.ota.read_bytes())

    def test_pair_envelopes_decrypt_and_merge_shared_public_key(self):
        secondary = self.root / 'secondary.zip'
        secondary.write_bytes(b'secondary fixture OTA')
        Path(str(secondary) + '.selection.json').write_text('{}')
        export.encrypt_candidate(self.root, self.stage, self.recipient,
                                 [self.ota, secondary], self.public, microg=True)
        manifest = json.loads((self.stage / 'manifest.json').read_text())
        self.assertEqual(len(manifest['envelopes']), 2)
        merged = {}
        for index in (1, 2):
            decrypted = self.decrypt(envelope=index)
            self.assertEqual(decrypted.returncode, 0, decrypted.stderr)
            with tarfile.open(fileobj=io.BytesIO(decrypted.stdout)) as archive:
                for name in archive.getnames():
                    data = archive.extractfile(name).read()
                    if name in merged:
                        self.assertEqual(name, self.public.name)
                        self.assertEqual(data, merged[name])
                    merged[name] = data
        self.assertEqual(set(merged), {self.ota.name, self.ota.name + '.selection.json',
                         self.ota.name + '.microg-patch-report.json', secondary.name,
                         secondary.name + '.selection.json', self.public.name})
        self.assertEqual(merged[secondary.name], secondary.read_bytes())

    def test_wrong_recipient_and_tamper_refused(self):
        self.encrypt()
        self.assertNotEqual(self.decrypt(1).returncode, 0)
        envelope = self.stage / 'candidate-1.cms.der'
        data = bytearray(envelope.read_bytes())
        data[-40] ^= 1
        envelope.write_bytes(data)
        self.assertNotEqual(self.decrypt().returncode, 0)

    def test_rejects_private_linked_traversal_and_external_inputs(self):
        private = self.root / '.keys'
        private.mkdir()
        (private / 'secret.zip').write_bytes(b'secret')
        link = self.root / 'linked.zip'
        link.symlink_to(self.ota)
        for unsafe in (private / 'secret.zip', link,
                       self.root / 'subdir' / '..' / self.ota.name,
                       Path(self.transport.name) / 'outside.zip'):
            with self.subTest(path=unsafe):
                with self.assertRaises((ValueError, OSError)):
                    self.encrypt(unsafe)
                self.assertFalse(self.stage.exists())
        self.stage.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.encrypt()
        self.assertEqual(self.ota.read_bytes(), b'fixture OTA content')

    def test_recipient_is_public_certificate_only(self):
        self.assertTrue(export.validate_recipient(self.recipient))
        for unsafe in (self.certs[0][0].read_bytes(),
                       self.certs[0][1].read_bytes() + self.certs[0][0].read_bytes(),
                       b'not a certificate'):
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                export.validate_recipient(base64.b64encode(unsafe).decode())

    def test_export_requires_explicit_repository_candidate(self):
        good = ['care-map-pdx235', 'repository', 'build-only', False, self.recipient]
        export.require_candidate(*good)
        for index, forbidden in enumerate(['none', 'disposable', 'default', True, '']):
            bad = good.copy()
            bad[index] = forbidden
            with self.assertRaises(ValueError):
                export.require_candidate(*bad)


if __name__ == '__main__':
    unittest.main()
