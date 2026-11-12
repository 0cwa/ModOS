#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Validate the native helper contract; no helper source is rewritten.
set -euo pipefail
python3 tests/helper_contract_test.py
if [[ "${1:-}" == --strict ]]; then
  python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import urlopen

manifest = json.loads(Path('tools/compat/avbroot_setup_compat.json').read_text())
source = Path('src/declarations.sh').read_text()
pin = re.search(r'^VERSION\[AVBROOT_SETUP\]="([0-9a-f]{40})"', source, re.M)[1]
assert pin == manifest['revision'], 'helper declaration differs from native contract'
# Reuse an existing checkout when supplied. CI may fetch the one small source
# target over HTTPS rather than clone another copy of the repository.
local = os.environ.get('MODOS_HELPER_TEST_SOURCE')
for relative, entry in manifest['targets'].items():
    if local:
        data = subprocess.check_output(['git', '-C', local, 'show', f'{pin}:{relative}'])
    else:
        url = f'https://raw.githubusercontent.com/0cwa/my-avbroot-setup/{pin}/{relative}'
        with urlopen(url, timeout=30) as response:
            data = response.read(4 * 1024 * 1024 + 1)
    assert len(data) <= 4 * 1024 * 1024, 'unexpected helper source size'
    assert hashlib.sha256(data).hexdigest() == entry['sha256'], 'pinned helper source hash mismatch'
print('Immutable native helper source contract passed')
PY
elif [[ "$#" != 0 ]]; then
  echo 'Usage: tests/avbroot_setup_compat_test.sh [--strict]' >&2
  exit 1
fi
