#!/usr/bin/env python3
"""Reject a microG release proposal without the matching immutable helper lock."""
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microg_spoof_patch import load_certificate_der, validate_companion_lock


def main():
    declarations = Path('src/declarations.sh').read_text()
    release = re.search(r'^VERSION\[MICROG\]="(v[0-9.]+)"', declarations, re.M)[1]
    revision = re.search(r'^VERSION\[AVBROOT_SETUP\]="([0-9a-f]{40})"', declarations, re.M)[1]
    for suffix in ('', '-modos'):
        with urlopen(f'https://raw.githubusercontent.com/0cwa/my-avbroot-setup/{revision}/locks/microg-{release}{suffix}.json', timeout=30) as response:
            lock = json.load(response)
        modules = lock['modules']
        if len(modules) != 1 or modules[0]['id'] != 'microg' or modules[0]['version'] != release:
            raise SystemExit('microG release selector does not match the pinned helper lock')
        if suffix:
            with tempfile.TemporaryDirectory(prefix='microg-lock-') as tmp:
                path = Path(tmp) / 'lock.json'
                path.write_text(json.dumps(lock))
                validate_companion_lock(path, load_certificate_der(
                    'custom/microg-companion-signer/cert.pem'))
    print(f'microG {release} matches immutable helper {revision}')


if __name__ == '__main__':
    main()
