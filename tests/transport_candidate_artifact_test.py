#!/usr/bin/env python3
"""Exercise ciphertext range transport without real networks or credentials."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest


spec = importlib.util.spec_from_file_location(
    'transport', Path(__file__).resolve().parents[1] / 'src/ci/transport_candidate_artifact.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class ArtifactPlanTest(unittest.TestCase):
    def setUp(self):
        self.repository = '0cwa/ModOS'
        self.sha256 = 'a' * 64
        self.head = '1234567890abcdef1234567890abcdef12345678'
        self.artifact = {
            'id': 123, 'name': 'encrypted-device-candidate', 'expired': False,
            'size_in_bytes': 2048, 'digest': 'sha256:' + self.sha256,
            'workflow_run': {
                'id': 456, 'repository_id': 789, 'head_repository_id': 789,
                'head_sha': self.head,
            },
        }
        self.run = {
            'id': 456, 'status': 'completed', 'conclusion': 'success',
            'head_sha': self.head, 'path': '.github/workflows/release-lineage.yml',
            'event': 'workflow_dispatch',
            'repository': {'id': 789, 'full_name': self.repository},
            'head_repository': {'id': 789, 'full_name': self.repository},
        }
        self.requests = []

    def api(self, path):
        self.requests.append(path)
        if path == '/repos/0cwa/ModOS/actions/artifacts/123':
            return copy.deepcopy(self.artifact)
        if path == '/repos/0cwa/ModOS/actions/runs/456':
            return copy.deepcopy(self.run)
        self.fail(f'Unexpected API request: {path}')

    def plan(self):
        return transport.plan_artifact(self.repository, 123, self.sha256, self.api)

    def test_metadata_and_successful_dispatch_produce_plan(self):
        plan = self.plan()
        self.assertEqual(plan['source_head_sha'], self.head)
        self.assertEqual(plan['source_zip_sha256'], self.sha256)
        self.assertEqual(plan['source_zip_size'], 2048)
        self.assertTrue(plan['parts'])
        self.assertEqual(self.requests, [
            '/repos/0cwa/ModOS/actions/artifacts/123',
            '/repos/0cwa/ModOS/actions/runs/456',
        ])

    def test_mismatched_digest_or_unrelated_artifact_is_refused(self):
        for field, value in [('digest', 'sha256:' + 'b' * 64),
                             ('name', 'other-build'), ('expired', True)]:
            with self.subTest(field=field):
                original = self.artifact[field]
                self.artifact[field] = value
                with self.assertRaises((ValueError, RuntimeError)):
                    self.plan()
                self.artifact[field] = original

    def test_source_and_failed_or_unrelated_run_are_refused(self):
        cases = [
            (self.artifact['workflow_run'], 'head_repository_id', 999),
            (self.run, 'head_sha', 'f' * 40),
            (self.run, 'conclusion', 'failure'),
            (self.run, 'status', 'in_progress'),
            (self.run, 'event', 'push'),
            (self.run, 'path', '.github/workflows/other.yml'),
            (self.run['repository'], 'full_name', 'someone/else'),
        ]
        for obj, field, value in cases:
            with self.subTest(field=field, value=value):
                original = obj[field]
                obj[field] = value
                with self.assertRaises((ValueError, RuntimeError)):
                    self.plan()
                obj[field] = original


class RangeTransportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'part'
        self.payload = bytes(range(256)) * 8
        self.requests = []
        self.response_mode = 'exact'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(dict(self.headers))
                start, end = map(int, self.headers['Range'].removeprefix('bytes=').split('-'))
                data = owner.payload[start:end + 1]
                if owner.response_mode == 'ignored':
                    self.send_response(200)
                    data = owner.payload
                else:
                    self.send_response(206)
                    reported_start = start + (owner.response_mode == 'wrong-range')
                    self.send_header('Content-Range', f'bytes {reported_start}-{end}/{len(owner.payload)}')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}/ciphertext'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def fetch(self):
        source = {
            'artifact_id': 123, 'source_run_id': 456,
            'source_head_sha': '1234567890abcdef1234567890abcdef12345678',
            'source_zip_sha256': hashlib.sha256(self.payload).hexdigest(),
            'source_zip_size': len(self.payload),
        }
        return transport.fetch_part(self.url, 113, 511, self.root, source, 0)

    def test_exact_range_streams_requested_bytes_and_digest(self):
        self.fetch()
        expected = self.payload[113:624]
        self.assertEqual((self.root / 'chunk.bin').read_bytes(), expected)
        manifest = json.loads((self.root / 'part-manifest.json').read_text())
        self.assertEqual(manifest['chunk_sha256'], hashlib.sha256(expected).hexdigest())
        self.assertEqual(self.requests[0]['Range'], 'bytes=113-623')
        self.assertEqual(self.requests[0]['Accept-Encoding'], 'identity')
        self.assertNotIn('Authorization', self.requests[0])

    def test_ignored_range_is_refused_without_success_manifest(self):
        self.response_mode = 'ignored'
        with self.assertRaises((ValueError, RuntimeError)):
            self.fetch()
        self.assertFalse((self.root / 'part-manifest.json').exists())

    def test_wrong_range_is_refused_without_success_manifest(self):
        self.response_mode = 'wrong-range'
        with self.assertRaises((ValueError, RuntimeError)):
            self.fetch()
        self.assertFalse((self.root / 'part-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
