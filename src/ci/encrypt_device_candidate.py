#!/usr/bin/env python3
"""Export only authenticated-encrypted device-acceptance material."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile


def require_candidate(profile, signing_mode, release_type, publish, recipient):
    if (profile != 'care-map-pdx235' or signing_mode != 'repository' or
            release_type != 'build-only' or publish or not recipient):
        raise ValueError('Encrypted export requires an explicit nonpublishing repository-signed care-map device candidate and public recipient certificate')


def validate_recipient(recipient_base64):
    recipient = base64.b64decode(recipient_base64, validate=True)
    if len(recipient) > 16384 or not re.fullmatch(
            rb'\s*-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\s*', recipient):
        raise ValueError('Recipient must contain one public PEM certificate only')
    subprocess.run(['openssl', 'x509', '-inform', 'PEM', '-noout'],
                   input=recipient, capture_output=True, check=True)
    return recipient


def open_input(root, path):
    """Allow regular workspace inputs, excluding key stores and linked paths."""
    supplied = Path(path)
    if '..' in supplied.parts or '.keys' in supplied.parts:
        raise ValueError('Candidate export rejects traversal and private-key paths')
    absolute = Path(os.path.abspath(supplied))
    relative = absolute.relative_to(root)
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ValueError('Candidate export rejects linked inputs')
    fd = os.open(absolute, os.O_RDONLY | os.O_NOFOLLOW)
    handle = os.fdopen(fd, 'rb')
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise ValueError('Candidate export requires regular inputs')
    return handle


def encrypt_candidate(root, stage, recipient_base64, otas, public_key, microg=False):
    root = Path(root).resolve(strict=True)
    stage = Path(stage)
    # A fresh, separate stage contains only opaque envelopes and one manifest.
    stage.mkdir(mode=0o700, parents=False, exist_ok=False)
    handles = []
    process = None
    try:
        recipient = validate_recipient(recipient_base64)
        public_key = Path(public_key)
        if public_key.name != 'device-candidate-avb_pkmd.bin':
            raise ValueError('Candidate public key must use the designated public export path')
        envelope_members = []
        for index, ota in enumerate(otas):
            if not str(ota).endswith('.zip'):
                raise ValueError('Candidate OTA must be a ZIP output')
            members = [Path(ota), Path(str(ota) + '.selection.json')]
            if microg and index == 0:
                members.append(Path(str(ota) + '.microg-patch-report.json'))
            envelope_members.append(members + [public_key])
        unique_members = [member for members in envelope_members for member in members if member != public_key] + [public_key]
        names = [member.name for member in unique_members]
        if len(set(names)) != len(names):
            raise ValueError('Candidate export member names collide')
        opened = {}
        for member in unique_members:
            handle = open_input(root, member)
            handles.append(handle)
            opened[member] = handle
        with tempfile.TemporaryDirectory(prefix='candidate-recipient-') as recipient_dir:
            certificate = Path(recipient_dir) / 'recipient.pem'
            certificate.write_bytes(recipient)
            der = subprocess.check_output(['openssl', 'x509', '-in', str(certificate), '-outform', 'DER'])
            envelopes = []
            # One authenticated envelope per OTA avoids combining the pair into
            # a CMS object larger than OpenSSL's ASN.1 decoder can represent.
            for index, members in enumerate(envelope_members, start=1):
                envelope = stage / f'candidate-{index}.cms.der'
                with tempfile.TemporaryFile() as errors:
                    process = subprocess.Popen([
                        'openssl', 'cms', '-encrypt', '-binary', '-stream', '-aes-256-gcm',
                        '-outform', 'DER', '-out', str(envelope), '-recip', str(certificate),
                    ], stdin=subprocess.PIPE, stderr=errors)
                    # Plaintext streams directly to encryption, never to an
                    # export directory or plaintext temporary archive.
                    with tarfile.open(fileobj=process.stdin, mode='w|') as archive:
                        for member in members:
                            handle = opened[member]
                            handle.seek(0)
                            info = tarfile.TarInfo(member.name)
                            info.size = os.fstat(handle.fileno()).st_size
                            info.mode = 0o600
                            archive.addfile(info, handle)
                    process.stdin.close()
                    if process.wait() != 0:
                        errors.seek(0)
                        raise ValueError('CMS encryption failed: ' + errors.read().decode(errors='replace'))
                os.chmod(envelope, 0o600)
                with envelope.open('rb') as encrypted_file:
                    digest = hashlib.file_digest(encrypted_file, 'sha256').hexdigest()
                envelopes.append({'envelope': envelope.name, 'envelope_sha256': digest})
            manifest = {
                'schema_version': 2,
                'purpose': 'encrypted-private-device-acceptance',
                'envelopes': envelopes,
                'content_encryption': 'AES-256-GCM',
                'format': 'one binary streaming CMS AuthEnvelopedData tar per OTA',
                'recipient_certificate_sha256': hashlib.sha256(der).hexdigest(),
            }
            (stage / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        shutil.rmtree(stage)
        raise
    finally:
        for handle in handles:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-request', action='store_true')
    parser.add_argument('--workspace')
    parser.add_argument('--stage')
    parser.add_argument('--ota', action='append')
    parser.add_argument('--public-key')
    parser.add_argument('--microg', action='store_true')
    args = parser.parse_args()
    require_candidate(os.environ.get('PIXENEOS_DEVELOPMENT_PROFILE'),
                      os.environ.get('SIGNING_MODE'), os.environ.get('RELEASE_TYPE'),
                      os.environ.get('PUBLISH') != 'false',
                      os.environ.get('DEVICE_CANDIDATE_RECIPIENT_BASE64'))
    if os.environ.get('OUTPUT_SCOPE') != 'local-unpublished' or os.environ.get('ROM_FAMILY') != 'lineageos' or os.environ.get('DEVICE_NAME') != 'pdx235':
        raise ValueError('Encrypted export requires local LineageOS/pdx235 device acceptance')
    if args.validate_request:
        validate_recipient(os.environ['DEVICE_CANDIDATE_RECIPIENT_BASE64'])
        return
    if not (args.workspace and args.stage and args.ota and args.public_key):
        parser.error('export requires workspace, fresh stage, OTA and public key')
    encrypt_candidate(args.workspace, args.stage,
                      os.environ['DEVICE_CANDIDATE_RECIPIENT_BASE64'],
                      args.ota, args.public_key, args.microg)


if __name__ == '__main__':
    main()
