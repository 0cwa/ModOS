#!/usr/bin/env python3
"""Reject a microG release proposal without the matching immutable helper lock."""
import json
from pathlib import Path
import re
from urllib.request import urlopen


def main():
    declarations = Path('src/declarations.sh').read_text()
    release = re.search(r'^VERSION\[MICROG\]="(v[0-9.]+)"', declarations, re.M)[1]
    revision = re.search(r'^VERSION\[AVBROOT_SETUP\]="([0-9a-f]{40})"', declarations, re.M)[1]
    with urlopen(f'https://raw.githubusercontent.com/0cwa/my-avbroot-setup/{revision}/locks/microg-{release}.json', timeout=30) as response:
        lock = json.load(response)
    modules = lock['modules']
    if len(modules) != 1 or modules[0]['id'] != 'microg' or modules[0]['version'] != release:
        raise SystemExit('microG release selector does not match the pinned helper lock')
    print(f'microG {release} matches immutable helper {revision}')


if __name__ == '__main__':
    main()
