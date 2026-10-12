#!/usr/bin/env python3
"""Read-only transport of an existing encrypted candidate as artifact parts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.error
import urllib.request

# The connector accepts at most 512MiB per ZIP. Leave archive-header headroom
# by transporting 256MiB encrypted parts; this does not change the OTA payload.
CHUNK_BYTES = 256 * 1024 * 1024


def github_api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def plan_artifact(repository, artifact_id, expected_sha256, api=github_api):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository selector')
    if not str(artifact_id).isdigit() or int(artifact_id) <= 0:
        raise ValueError('Source artifact ID must be positive')
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('Expected source ZIP SHA-256 is required')
    artifact = api(f'/repos/{repository}/actions/artifacts/{artifact_id}')
    source = artifact['workflow_run']
    if (artifact['id'] != int(artifact_id) or artifact['name'] != 'encrypted-device-candidate' or
            artifact['expired'] or artifact['digest'] != 'sha256:' + expected_sha256 or
            artifact['size_in_bytes'] <= 0 or source['repository_id'] != source['head_repository_id']):
        raise ValueError('Source encrypted artifact identity differs')
    run = api(f'/repos/{repository}/actions/runs/{source["id"]}')
    if (run['repository']['full_name'] != repository or run['head_repository']['full_name'] != repository or
            run['head_sha'] != source['head_sha'] or run['status'] != 'completed' or
            run['conclusion'] != 'success' or run['path'] != '.github/workflows/release-lineage.yml' or
            run['event'] != 'workflow_dispatch'):
        raise ValueError('Source must be a successful same-repository Lineage workflow run')
    size = artifact['size_in_bytes']
    return {
        'artifact_id': int(artifact_id), 'source_run_id': source['id'],
        'source_head_sha': source['head_sha'], 'source_zip_sha256': expected_sha256,
        'source_zip_size': size,
        'parts': [{'index': index, 'start': start, 'length': min(CHUNK_BYTES, size - start)}
                  for index, start in enumerate(range(0, size, CHUNK_BYTES))],
    }


def signed_artifact_url(repository, artifact_id):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip')
    request.add_header('Authorization', 'Bearer ' + (os.environ.get('GH_TOKEN') or os.environ['GITHUB_TOKEN']))
    request.add_header('User-Agent', 'ModOS encrypted artifact transport')
    try:
        urllib.request.build_opener(NoRedirect).open(request, timeout=60)
    except urllib.error.HTTPError as error:
        if error.code == 302:
            return error.headers['Location']
        raise
    raise ValueError('GitHub did not return an artifact redirect')


def fetch_part(url, start, length, output_dir, source_metadata, part_index, opener=urllib.request.urlopen):
    end = start + length - 1
    request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'})
    # This request has no GitHub Authorization; only the supplied signed URL
    # authorizes the Azure read. All HTTPS/proxy/CA handling remains standard.
    output = Path(output_dir)
    output.mkdir(mode=0o700, exist_ok=False)
    try:
        with opener(request, timeout=120) as response:
            expected_range = f'bytes {start}-{end}/{source_metadata["source_zip_size"]}'
            if response.status != 206 or response.headers.get('Content-Range') != expected_range:
                raise ValueError('Server did not honor the exact requested byte range')
            if int(response.headers.get('Content-Length', '-1')) != length:
                raise ValueError('Artifact part length differs')
            digest = hashlib.sha256()
            received = 0
            with (output / 'chunk.bin').open('xb') as target:
                os.chmod(target.name, 0o600)
                while block := response.read(min(1024 * 1024, length - received + 1)):
                    received += len(block)
                    if received > length:
                        raise ValueError('Artifact part exceeds requested range')
                    target.write(block)
                    digest.update(block)
            if received != length:
                raise ValueError('Artifact part was truncated')
        manifest = {key: value for key, value in source_metadata.items() if key != 'parts'}
        manifest.update(part_index=part_index, start=start, length=length, chunk_sha256=digest.hexdigest())
        (output / 'part-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'fetch-part'))
    parser.add_argument('--part-index', type=int)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    plan = plan_artifact(os.environ['GITHUB_REPOSITORY'], os.environ['SOURCE_ARTIFACT_ID'],
                         os.environ['EXPECTED_SOURCE_ZIP_SHA256'])
    if args.command == 'plan':
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('matrix=' + json.dumps({'include': plan['parts']}, separators=(',', ':')) + '\n')
        print(json.dumps({key: value for key, value in plan.items() if key != 'parts'}, sort_keys=True))
        return
    if args.part_index is None or args.output_dir is None or not 0 <= args.part_index < len(plan['parts']):
        parser.error('fetch-part requires a planned part index and fresh output directory')
    part = plan['parts'][args.part_index]
    url = signed_artifact_url(os.environ['GITHUB_REPOSITORY'], plan['artifact_id'])
    fetch_part(url, part['start'], part['length'], args.output_dir, plan, part['index'])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Signed URLs are capabilities, so do not render HTTP tracebacks/URLs.
        print('Encrypted artifact transport failed: ' + type(error).__name__)
        raise SystemExit(1)
