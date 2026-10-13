#!/usr/bin/env python3
"""Collect private, read-only device observations; never decide acceptance."""
import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', help='ADB device serial (required when several devices are connected)')
    parser.add_argument('--output', type=pathlib.Path, required=True, help='New private local directory')
    parser.add_argument('--artifact', type=pathlib.Path, help='Optional locally verified OTA whose digest should accompany observations')
    parser.add_argument('--microg-state', action='store_true', help='Record installed microG package/permission state, not account contents')
    parser.add_argument('--root-state', action='store_true', help='Record shell identity and Magisk manager package state; does not invoke su')
    args = parser.parse_args()
    adb = ['adb'] + (['-s', args.serial] if args.serial else [])
    try:
        state = subprocess.run(adb + ['get-state'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        parser.error(f'Cannot reach adb: {error}')
    if state.returncode or state.stdout.strip() != 'device':
        parser.error('ADB must already have one authorized, connected device; no authorization or root changes are attempted')
    try:
        args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as error:
        parser.error(f'Cannot create new evidence directory: {error}')

    observations = []

    def record(name, command):
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        status = 'observed'
        try:
            result = subprocess.run(adb + ['shell', command], capture_output=True, timeout=30)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
            if code:
                status = 'unavailable'
        except (OSError, subprocess.TimeoutExpired) as error:
            stdout = getattr(error, 'stdout', None) or b''
            stderr = str(error).encode()
            code, status = None, 'unavailable'
        for suffix, data in [('stdout', stdout), ('stderr', stderr)]:
            path = args.output / f'{name}.{suffix}.txt'
            with path.open('xb') as stream:
                path.chmod(0o600)
                stream.write(data)
        observations.append({'name': name, 'command': command, 'started_utc': started,
                             'status': status, 'returncode': code})
        return stdout.decode('utf-8', errors='replace').strip()

    boot_start = record('boot-identity-start', 'cat /proc/sys/kernel/random/boot_id')
    record('boot-timing', 'cat /proc/uptime; date -u')
    properties = record('properties', 'getprop')
    record('mounts', 'cat /proc/mounts')
    record('device-mapper', 'dmctl list devices')
    record('device-mapper-names', 'for p in /sys/block/dm-*/dm/name; do echo "$p"; cat "$p"; done')
    slot = record('current-slot', 'bootctl get-current-slot')
    if slot in ('0', '1'):
        record('slot-successful', f'bootctl is-slot-marked-successful {slot}')
    record('snapshots', 'snapshotctl dump')
    record('update-engine', 'dumpsys update_engine')
    record('first-boot-logs', 'logcat -b all -d -v threadtime -t 20000')
    record('kernel-logs', 'dmesg')
    if args.microg_state:
        record('microg-services-package', 'dumpsys package com.google.android.gms')
        record('microg-companion-package', 'dumpsys package com.android.vending')
    if args.root_state:
        record('shell-identity', 'id')
        record('magisk-manager-package', 'dumpsys package com.topjohnwu.magisk')
    boot_end = record('boot-identity-end', 'cat /proc/sys/kernel/random/boot_id')
    artifact = None
    if args.artifact:
        digest = hashlib.sha256()
        with args.artifact.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        artifact = {'filename': args.artifact.name, 'size': args.artifact.stat().st_size,
                    'sha256': digest.hexdigest(), 'authentication': 'not performed by collector'}
    def boot_id(value):
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return None

    start_id, end_id = boot_id(boot_start), boot_id(boot_end)
    receipt = {
        'schema_version': 1, 'kind': 'device-observations-only', 'acceptance': 'not evaluated',
        'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'serial': args.serial, 'boot_id_start': start_id, 'boot_id_end': end_id,
        'same_boot_observed': bool(start_id and start_id == end_id),
        'artifact': artifact, 'observations': observations,
        'limitations': [
            'Unavailable commands and absent logs do not establish a failure or success.',
            'Collection does not establish that this device booted the optional artifact.',
            'A marked successful slot alone does not establish effective verity/care-map verification.',
            'Package state alone does not establish root, microG self-check, spoofing restriction, account, push, location, or OTA persistence.',
            'Logs are bounded to the last 20000 entries; preserve first-boot observations promptly.',
        ],
    }
    receipt_path = args.output / 'receipt.json'
    with receipt_path.open('x', encoding='utf-8') as stream:
        receipt_path.chmod(0o600)
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(f'Private observations saved to {args.output}; acceptance remains unevaluated.')
    if '[ro.product.device]: [pdx235]' not in properties:
        print('The observed product device was not confirmed as pdx235; review properties.', file=sys.stderr)


if __name__ == '__main__':
    main()
