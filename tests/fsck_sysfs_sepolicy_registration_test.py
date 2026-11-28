#!/usr/bin/env python3
"""Verify fsck policy uses the explicit external adapter extension route."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = json.loads((ROOT / 'tools/modules/adapter_extension.json').read_text())
    entry = next(item for item in config['adapters'] if item['id'] == 'fsck-sysfs-sepolicy')
    assert entry['kind'] == 'legacy-cli'
    assert entry['target'] == '_module_class'
    assert entry['source_path'] == 'src/fsck_sysfs_sepolicy.py'
    assert entry['source'] == 'adapters/fsck_sysfs_sepolicy.py'
    assert entry['sha256'] == hashlib.sha256((ROOT / entry['source_path']).read_bytes()).hexdigest()

    util = (ROOT / 'src/util_functions.sh').read_text()
    start = util.index('function prepare_fsck_sysfs_sepolicy_module()')
    end = util.index('\n# Register the locked Termux exfatprogs binary adapter', start)
    preparation = util[start:end]
    assert 'registry.py' not in preparation
    assert 'lib/modules' not in preparation
    assert 'prepare_adapter_extension' in util
    assert 'args+=(--adapter-extension "${ADAPTER_EXTENSION_PATH}")' in util

    temp_root = Path(tempfile.mkdtemp(
        prefix='fsck-sysfs-extension-',
        dir=os.environ['TMPDIR'],
    ))
    try:
        script = '''
set -euo pipefail
source src/util_functions.sh
WORKDIR="$1/work"
ROM_FAMILY=lineageos
DEVICE_NAME=pdx235
EXFATPROGS_POLICY_PATCHER_PATH="$1/patcher"
ADDITIONALS[FSCK_SYSFS_SEPOLICY]=true
prepare_fsck_sysfs_sepolicy_module
[[ -f "$WORKDIR/modules/fsck-sysfs-sepolicy.zip" ]]
[[ -f "$WORKDIR/signatures/fsck-sysfs-sepolicy.zip.sig" ]]
[[ "$PIXENEOS_FSCK_SYSFS_POLICY_PATCHER" == "$1/patcher" ]]
'''
        result = subprocess.run(
            ['bash', '-c', script, 'fsck-registration-test', str(temp_root)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise AssertionError(
                'local fsck preparation failed:\n'
                f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}'
            )
    finally:
        for child in sorted(temp_root.rglob('*'), reverse=True):
            if child.is_dir():
                child.rmdir()
            else:
                child.unlink()
        temp_root.rmdir()

    print('fsck sysfs external adapter preparation contract passed')


if __name__ == '__main__':
    main()
