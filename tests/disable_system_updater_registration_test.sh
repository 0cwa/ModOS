#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Verify the updater adapter uses the external trusted extension route.
set -euo pipefail
: "${TMPDIR:?set TMPDIR to a task scratch directory}"
source src/util_functions.sh

fixture_root="$(mktemp -d "${TMPDIR}/updater-extension.XXXXXX")"
trap 'rm -rf -- "${fixture_root}"' EXIT
WORKDIR="${fixture_root}/work"
helper_root="${fixture_root}/helper"
ADDITIONALS[DISABLE_SYSTEM_UPDATER]=false
prepare_disable_system_updater_module "${helper_root}"
[[ ! -e "${helper_root}" && ! -e "${WORKDIR}" ]]

ADDITIONALS[DISABLE_SYSTEM_UPDATER]=true
for ROM_FAMILY in grapheneos lineageos; do
  WORKDIR="${fixture_root}/work-${ROM_FAMILY}"
  prepare_disable_system_updater_module "${helper_root}"
  [[ -f "${WORKDIR}/modules/disable-system-updater.zip" ]]
  [[ -f "${WORKDIR}/signatures/disable-system-updater.zip.sig" ]]
  [[ ! -e "${helper_root}" ]]
  [[ "${PIXENEOS_ROM_FAMILY}" == "${ROM_FAMILY}" ]]
done

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path.cwd()
config = json.loads((root / 'tools/modules/adapter_extension.json').read_text())
entry = next(item for item in config['adapters'] if item['id'] == 'disable-system-updater')
assert entry['kind'] == 'legacy-cli'
assert entry['target'] == '_module_class'
assert entry['source_path'] == 'src/disable_system_updater.py'
assert entry['source'] == 'adapters/disable_system_updater.py'
assert entry['sha256'] == hashlib.sha256((root / entry['source_path']).read_bytes()).hexdigest()

util = (root / 'src/util_functions.sh').read_text()
start = util.index('function prepare_disable_system_updater_module()')
end = util.index('\n# Register the local Shizuku adapter', start)
body = util[start:end]
assert 'registry.py' not in body
assert 'helper_root}/lib/modules' not in body
assert 'disable-system-updater.zip.sig' in body
print('system updater external adapter preparation contract passed')
PY
