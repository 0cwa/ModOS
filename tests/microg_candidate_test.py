#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise source identity, repeatability and unsigned APK acceptance."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate', ROOT / 'tools/build_microg_candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'upstream'
        self.repo.mkdir()
        candidate.git(self.repo, 'init', '--quiet')
        self.lock = copy.deepcopy(json.loads(candidate.LOCK_PATH.read_text()))
        properties = self.repo / 'gradle/wrapper/gradle-wrapper.properties'
        properties.parent.mkdir(parents=True)
        properties.write_text('distributionUrl=https\\://services.gradle.org/distributions/gradle-8.13-bin.zip\n')
        (self.repo / 'loader.txt').write_text('load library\n')
        candidate.git(self.repo, 'add', '.')
        candidate.git(self.repo, 'commit', '--quiet', '-m', 'Fixture base',
                      timestamp=self.lock['recipe_timestamp'])
        self.lock['source_commit'] = candidate.git(self.repo, 'rev-parse', 'HEAD')
        candidate.git(self.repo, 'tag', self.lock['release'])
        self.lock['gradle']['wrapper_files'] = {
            'gradle/wrapper/gradle-wrapper.properties': candidate.digest(properties)}
        (self.repo / 'loader.txt').write_text('make library read-only\nload library\n')
        candidate.git(self.repo, 'commit', '--quiet', '-am', 'Fixture upstream fix',
                      timestamp=self.lock['recipe_timestamp'])
        self.lock['upstream_fix_commit'] = candidate.git(self.repo, 'rev-parse', 'HEAD')
        self.patch = self.root / 'fix.patch'
        self.patch.write_text(candidate.git(self.repo, 'format-patch', '-1', '--stdout') + '\n')
        self.lock['patch'] = 'fix.patch'
        self.lock['patch_sha256'] = candidate.digest(self.patch)

    def prepare(self, name='output'):
        return candidate.build(self.lock, self.patch, self.root / name,
                               prepare_only=True, source_repository=self.repo)

    def test_two_real_git_preparations_have_identical_receipts_and_clean_trees(self):
        before = candidate.git(self.repo, 'rev-parse', 'HEAD')
        first, second = self.prepare('one'), self.prepare('two')
        self.assertEqual(first, second)
        self.assertEqual(candidate.git(self.repo, 'rev-parse', 'HEAD'), before)
        prepared = self.root / 'one/src'
        self.assertEqual(candidate.git(prepared, 'status', '--porcelain'), '')
        self.assertEqual((prepared / 'loader.txt').read_text(),
                         'make library read-only\nload library\n')
        self.assertIn(self.lock['gradle']['distribution_sha256'],
                      (prepared / 'gradle/wrapper/gradle-wrapper.properties').read_text())
        self.assertFalse(first['production_eligible'])
        self.assertNotIn('apk', first)

    def test_moved_release_tag_fails_without_publishing_output(self):
        candidate.git(self.repo, 'tag', '--force', self.lock['release'])
        with self.assertRaisesRegex(candidate.CandidateError, 'locked source commit'):
            self.prepare()
        self.assertFalse((self.root / 'output').exists())

    def test_source_change_after_preparation_cannot_keep_the_original_receipt(self):
        receipt = self.prepare()
        source = self.root / 'output/src'
        candidate.check_prepared_source(source, receipt)
        (source / 'loader.txt').write_text('unexpected build-time modification\n')
        with self.assertRaisesRegex(candidate.CandidateError, 'Prepared source changed'):
            candidate.check_prepared_source(source, receipt)

    def test_patch_tampering_is_rejected_before_preparation(self):
        self.patch.write_text(self.patch.read_text() + 'unreviewed change\n')
        with self.assertRaisesRegex(candidate.CandidateError, 'patch SHA-256'):
            candidate.checked_patch(self.lock, self.root)

    def test_unreviewed_wrapper_fails_without_publishing_output(self):
        self.lock['gradle']['wrapper_files']['gradle/wrapper/gradle-wrapper.properties'] = '0' * 64
        with self.assertRaisesRegex(candidate.CandidateError, 'Unreviewed Gradle wrapper'):
            self.prepare()
        self.assertFalse((self.root / 'output').exists())

    def test_existing_output_and_dangling_symlink_are_never_overwritten(self):
        output = self.root / 'output'
        output.mkdir()
        sentinel = output / 'important.txt'
        sentinel.write_text('keep me')
        with self.assertRaisesRegex(candidate.CandidateError, 'Output already exists'):
            self.prepare()
        self.assertEqual(sentinel.read_text(), 'keep me')
        (self.root / 'link').symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(candidate.CandidateError, 'Output already exists'):
            self.prepare('link')

    def test_failed_compilation_does_not_publish_a_success_receipt(self):
        real_run = subprocess.run

        def fail_gradle(args, **kwargs):
            if args[0] == './gradlew':
                kwargs['stdout'].write('Fixture compiler failure\n')
                return subprocess.CompletedProcess(args, 1)
            return real_run(args, **kwargs)

        with mock.patch.object(candidate, 'check_toolchain',
                               return_value=(self.root, self.root, 'fixture Java 17')), \
                mock.patch.object(candidate.subprocess, 'run', side_effect=fail_gradle):
            with self.assertRaisesRegex(candidate.CandidateError, 'Fixture compiler failure'):
                candidate.build(self.lock, self.patch, self.root / 'output',
                                source_repository=self.repo)
        self.assertFalse((self.root / 'output').exists())

    def apk(self, entries=('lib/arm64-v8a/libmapbox-gl.so',)):
        path = self.root / 'candidate.apk'
        with zipfile.ZipFile(path, 'w') as archive:
            for entry in entries:
                archive.writestr(entry, b'fixture')
        return path

    def inspect(self, apk, *, package='com.google.android.gms', code=252432035,
                signature_returncode=1, debuggable=False):
        badging = f"package: name='{package}' versionCode='{code}' versionName='candidate'"
        if debuggable:
            badging += '\napplication-debuggable'
        with mock.patch.object(candidate, 'run', return_value=badging), \
                mock.patch.object(candidate.subprocess, 'run', return_value=
                                  subprocess.CompletedProcess([], signature_returncode)):
            return candidate.inspect_apk(apk, self.lock, self.root)

    def test_apk_identity_and_mapbox_payload_are_required(self):
        self.assertEqual(self.inspect(self.apk())['signing'], 'unsigned')
        for package, code in [('org.microg.gms', 252432035), ('com.google.android.gms', 252432034)]:
            with self.subTest(package=package, code=code):
                with self.assertRaisesRegex(candidate.CandidateError, 'package or versionCode'):
                    self.inspect(self.apk(), package=package, code=code)
        with self.assertRaisesRegex(candidate.CandidateError, 'ARM64 Mapbox'):
            self.inspect(self.apk(('classes.dex',)))

    def test_debuggable_apk_cannot_be_reported_as_a_release_candidate(self):
        with self.assertRaisesRegex(candidate.CandidateError, 'APK is debuggable'):
            self.inspect(self.apk(), debuggable=True)

    def test_signed_apk_cannot_be_reported_as_unsigned(self):
        with self.assertRaisesRegex(candidate.CandidateError, 'signature verification passed'):
            self.inspect(self.apk(), signature_returncode=0)
        with self.assertRaisesRegex(candidate.CandidateError, 'signing certificate'):
            self.inspect(self.apk(('META-INF/CERT.RSA', 'lib/arm64-v8a/libmapbox-gl.so')))


if __name__ == '__main__':
    unittest.main()
