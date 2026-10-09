#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

fail() {
  echo "$*" >&2
  exit 1
}

(
  export DEVICE_NAME=shiba
  export ROM_FAMILY=grapheneos
  export GRAPHENEOS_UPDATE_CHANNEL=stable
  export INTERACTIVE_MODE=false
  export OUTPUT_SCOPE=local-unpublished
  export ADDITIONALS_ROOT=false
  export MAGISK_PREINIT=sda10
  export ROOT_MODE=both

  source src/util_functions.sh

  VERSION[GRAPHENEOS]='2026091800'
  VERSION[MAGISK]='30.0'
  ADDITIONALS[DEBUG]=false

  validate_device_name() { return 0; }
  module_selection_fingerprint() {
    if [[ "${ADDITIONALS[ROOT]}" == true ]]; then
      MODULE_SELECTION_FINGERPRINT="$(printf 'b%.0s' {1..64})"
    else
      MODULE_SELECTION_FINGERPRINT="$(printf 'a%.0s' {1..64})"
    fi
    printf '%s\n' "${MODULE_SELECTION_FINGERPRINT}"
  }
  dirty_suffix() { printf ''; }

  generate_ota_info

  [[ "${ROOT_MODE}" == both ]] || fail "ROOT_MODE request was not preserved"
  [[ "${RESOLVED_ROOT_MODE}" == both ]] || fail "ROOT_MODE=both did not resolve to both"
  [[ "${ADDITIONALS[ROOT]}" == false ]] ||
    fail "dual identity generation did not restore legacy ROOT state"
  [[ "${MODULE_SELECTION_FINGERPRINT_ROOTLESS}" == "$(printf 'a%.0s' {1..64})" ]] ||
    fail "rootless fingerprint is wrong"
  [[ "${MODULE_SELECTION_FINGERPRINT_MAGISK}" == "$(printf 'b%.0s' {1..64})" ]] ||
    fail "Magisk fingerprint is wrong"
  [[ "${MODULE_SELECTION_FINGERPRINT}" == "${MODULE_SELECTION_FINGERPRINT_ROOTLESS}" ]] ||
    fail "legacy singular fingerprint must stay bound to the primary rootless output"
  [[ "${OUTPUTS[PATCHED_OTA]}" == "${OUTPUTS[PATCHED_OTA_ROOTLESS]}" ]] ||
    fail "legacy singular OTA must stay bound to the primary rootless output"
  [[ "${OUTPUTS[PATCHED_OTA_ROOTLESS]}" == *'-rootless-'* ]] ||
    fail "rootless output is not labeled rootless"
  [[ "${OUTPUTS[PATCHED_OTA_MAGISK]}" == *'-magisk-30.0-'* ]] ||
    fail "Magisk output is not version-labeled"
  [[ "${OUTPUTS[OTA_METADATA_ROOTLESS]}" == 'shiba-rootless.json' ]] ||
    fail "rootless update-info path is wrong"
  [[ "${OUTPUTS[OTA_METADATA_MAGISK]}" == 'shiba-magisk.json' ]] ||
    fail "Magisk update-info path is wrong"
)

(
  export ADDITIONALS_ROOT=true
  export MAGISK_PREINIT=sda10
  export ROOT_MODE=''
  source src/util_functions.sh
  resolve_root_mode
  [[ -z "${ROOT_MODE}" ]] || fail "legacy ROOT=true unexpectedly mutated ROOT_MODE"
  [[ "${RESOLVED_ROOT_MODE}" == magisk ]] ||
    fail "legacy ROOT=true did not resolve to magisk"
)

(
  export ADDITIONALS_ROOT=false
  export MAGISK_PREINIT=sda10
  export ROOT_MODE=''
  source src/util_functions.sh
  resolve_root_mode
  [[ -z "${ROOT_MODE}" ]] || fail "legacy ROOT=false unexpectedly mutated ROOT_MODE"
  [[ "${RESOLVED_ROOT_MODE}" == rootless ]] ||
    fail "legacy ROOT=false did not resolve to rootless"
)

if (
  export ADDITIONALS_ROOT=false
  export ROOT_MODE=unsupported
  source src/util_functions.sh
  resolve_root_mode >/dev/null 2>&1
); then
  fail "invalid ROOT_MODE was accepted"
fi

grep -Fq -- '--secondary-output' src/util_functions.sh ||
  fail "dual patch path does not use the helper secondary output"
grep -Fq -- '--secondary-patch-arg=--magisk' src/util_functions.sh ||
  fail "dual patch path does not provide the Magisk secondary plan"
grep -Fq -- '--skip-custota-tool' src/util_functions.sh ||
  fail "dual patch path must delegate sidecars to PixeneOS"
grep -Fq 'root-mode:' .github/workflows/build-rom.yml ||
  fail "reusable workflow does not expose root-mode"
grep -Fq 'publish_ota_pair.sh' .github/workflows/build-rom.yml ||
  fail "paired publication helper is not wired"
grep -Fq 'root-mode:' .github/workflows/release.yml ||
  fail "GrapheneOS release workflow does not expose root-mode"
grep -Fq 'root-mode:' .github/workflows/release-lineage.yml ||
  fail "LineageOS release workflow does not expose root-mode"
grep -Fq 'root-mode:' .github/workflows/multi-release.yml ||
  fail "multi-device release workflow does not expose root-mode"

expected_helper='6a495af445227fa573da4a990bf6434a69f9533f'
grep -Fq "VERSION[AVBROOT_SETUP]=\"${expected_helper}\"" src/declarations.sh ||
  fail "PixeneOS is not pinned to the green dual-output helper"
python3 - "${expected_helper}" <<'PY'
import json
import sys

expected = sys.argv[1]
with open('tools/compat/avbroot_setup_compat.json', encoding='utf-8') as f:
    manifest = json.load(f)
if manifest['revision'] != expected:
    raise SystemExit('compatibility manifest helper revision mismatch')
PY

echo "root-mode both tests passed"
