#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

set -euo pipefail

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT

[[ -f src/rom_profiles.sh ]] || {
  echo "missing ROM profile implementation: src/rom_profiles.sh" >&2
  exit 1
}
source src/rom_profiles.sh
source src/util_functions.sh

fail() {
  echo "$*" >&2
  exit 1
}

assert_equals() {
  local expected="${1}"
  local actual="${2}"
  local context="${3}"

  [[ "${actual}" == "${expected}" ]] ||
    fail "${context}: expected ${expected}, got ${actual}"
}

load_contract() {
  declare -F resolve_rom_profile >/dev/null ||
    fail "resolve_rom_profile is not defined"
  declare -F module_selection_fingerprint >/dev/null ||
    fail "module_selection_fingerprint is not defined"
  declare -F selection_variant_manifest >/dev/null ||
    fail "selection_variant_manifest is not defined"
  declare -F selection_variant_fingerprint >/dev/null ||
    fail "selection_variant_fingerprint is not defined"
  declare -F enforce_output_policy >/dev/null ||
    fail "enforce_output_policy is not defined"
  declare -p ROM_PROFILE >/dev/null 2>&1 ||
    fail "ROM_PROFILE is not declared"
}

assert_profile() (
  local family="${1}"
  local provider="${2}"
  local channel="${3}"
  local update_type="${4}"
  local compatible_sepolicy="${5}"
  local base_url="${6}"

  load_contract
  ROM_FAMILY="${family}"
  resolve_rom_profile

  assert_equals "${provider}" "${ROM_PROFILE[PROVIDER]}" \
    "${family} provider"
  assert_equals "${channel}" "${ROM_PROFILE[DEFAULT_UPDATE_CHANNEL]}" \
    "${family} default update channel"
  assert_equals "${update_type}" "${ROM_PROFILE[DEFAULT_UPDATE_TYPE]}" \
    "${family} default update type"
  assert_equals "${compatible_sepolicy}" \
    "${ROM_PROFILE[DEFAULT_COMPATIBLE_SEPOLICY]}" \
    "${family} compatible-SEPolicy default"
  assert_equals "${base_url}" "${ROM_PROFILE[OTA_BASE_URL]}" \
    "${family} OTA base URL"
)

test_profiles_are_stable() {
  assert_profile \
    grapheneos grapheneos stable ota_update false \
    https://releases.grapheneos.org
  assert_profile \
    lineageos lineageos nightly ota_update true \
    https://download.lineageos.org/api/v2
}

test_magisk_compatibility_defaults() (
  load_contract
  assert_equals "topjohnwu/Magisk" "${MAGISK[REPOSITORY]}" \
    "default Magisk repository"
  assert_equals "v30.7" "${VERSION[MAGISK]}" \
    "default Magisk compatibility version"
)

test_unknown_rom_fails_closed() (
  load_contract
  ROM_FAMILY="unknown-rom"

  if resolve_rom_profile >/dev/null 2>&1; then
    fail "unknown ROM family unexpectedly resolved"
  fi
)

test_invalid_compatible_sepolicy_fails_closed() (
  load_contract
  ROM_FAMILY="grapheneos"
  ADDITIONALS_MAS_COMPATIBLE_SEPOLICY="yes"

  if resolve_rom_profile >/dev/null 2>&1; then
    fail "invalid compatible-SEPolicy value unexpectedly resolved"
  fi
)

set_selection_fixture() {
  ROM_FAMILY="grapheneos"
  ADDITIONALS[ROOT]="false"
  ADDITIONALS[AFSR]="true"
  ADDITIONALS[CUSTOTA]="true"
  ADDITIONALS[MSD]="true"
  ADDITIONALS[BCR]="true"
  ADDITIONALS[OEMUNLOCKONBOOT]="true"
  ADDITIONALS[ALTERINSTALLER]="true"
  ADDITIONALS[BOOT_ANIMATION]="false"
  ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]="false"
  resolve_rom_profile
}

fingerprint() {
  local output_file value

  output_file="${TEST_ROOT}/fingerprint-${BASHPID}"
  module_selection_fingerprint >"${output_file}"
  value="$(<"${output_file}")"
  [[ "${value}" =~ ^[0-9a-f]{64}$ ]] ||
    fail "module selection fingerprint is not a full lowercase SHA-256: ${value}"
  assert_equals "${value}" "${MODULE_SELECTION_FINGERPRINT}" \
    "fingerprint function side effect"
  printf '%s\n' "${value}"
}

test_selection_fingerprint() (
  local baseline repeated afsr_on afsr_off root_changed rom_changed module_changed
  local boot_changed second_boot_changed fingerprint_input
  local rootless_repo_changed rooted_repo_changed rooted_version_changed

  load_contract
  set_selection_fixture
  baseline="$(fingerprint)"
  repeated="$(fingerprint)"
  assert_equals "${baseline}" "${repeated}" "deterministic fingerprint"
  module_selection_fingerprint >/dev/null
  assert_equals "${baseline}" "$(selection_variant_fingerprint)" \
    "build fingerprint uses the shared selection helper"

  afsr_on="${baseline}"
  ADDITIONALS[AFSR]="false"
  afsr_off="$(fingerprint)"
  [[ "${afsr_off}" != "${afsr_on}" ]] ||
    fail "AFSR-on and AFSR-off selections collided"
  ADDITIONALS[AFSR]="true"
  assert_equals "${afsr_on}" "$(fingerprint)" \
    "AFSR-on selection did not preserve the exact identity"

  MAGISK[REPOSITORY]="pixincreate/Magisk"
  rootless_repo_changed="$(fingerprint)"
  assert_equals "${baseline}" "${rootless_repo_changed}" \
    "rootless selection must not depend on the Magisk repository"
  MAGISK[REPOSITORY]="topjohnwu/Magisk"

  fingerprint_input="${TEST_ROOT}/fingerprint-input-${BASHPID}"
  sha256sum() {
    tee "${fingerprint_input}" | command sha256sum
  }
  module_selection_fingerprint >/dev/null
  unset -f sha256sum
  if grep -Eq '^boot_animation(_sha256)?=' "${fingerprint_input}"; then
    fail "default-off fingerprint must omit boot-animation fields"
  fi

  MAGISK[PREINIT]="sda10"
  ADDITIONALS[ROOT]="true"
  root_changed="$(fingerprint)"
  [[ "${root_changed}" != "${baseline}" ]] ||
    fail "root selection did not change the fingerprint"

  MAGISK[REPOSITORY]="pixincreate/Magisk"
  rooted_repo_changed="$(fingerprint)"
  [[ "${rooted_repo_changed}" != "${root_changed}" ]] ||
    fail "Magisk repository selection did not change the rooted fingerprint"
  MAGISK[REPOSITORY]="topjohnwu/Magisk"

  VERSION[MAGISK]="v30.6"
  rooted_version_changed="$(fingerprint)"
  [[ "${rooted_version_changed}" != "${root_changed}" ]] ||
    fail "Magisk version selection did not change the rooted fingerprint"
  VERSION[MAGISK]="v30.7"

  MAGISK[PREINIT]="sda47"
  local preinit_changed
  preinit_changed="$(fingerprint)"
  [[ "${preinit_changed}" != "${root_changed}" ]] ||
    fail "Magisk preinit selection did not change the fingerprint"

  ADDITIONALS[ROOT]="false"
  ROM_FAMILY="lineageos"
  resolve_rom_profile
  rom_changed="$(fingerprint)"
  [[ "${rom_changed}" != "${baseline}" ]] ||
    fail "ROM family did not change the fingerprint"

  ROM_FAMILY="grapheneos"
  resolve_rom_profile
  ADDITIONALS[BCR]="false"
  module_changed="$(fingerprint)"
  [[ "${module_changed}" != "${baseline}" ]] ||
    fail "module selection did not change the fingerprint"

  ADDITIONALS[BOOT_ANIMATION]="true"
  mkdir -p "${TEST_ROOT}/custom/boot-animation"
  python3 - "${TEST_ROOT}/custom/boot-animation/bootanimation.zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
    archive.writestr("part0/frame.png", b"frame")
PY
  _resolve_boot_animation_payloads() {
    BOOT_ANIMATION_LIGHT_PAYLOAD="${TEST_ROOT}/custom/boot-animation/bootanimation.zip"
    BOOT_ANIMATION_DARK_PAYLOAD="${BOOT_ANIMATION_LIGHT_PAYLOAD}"
  }
  boot_changed="$(fingerprint)"
  [[ "${boot_changed}" != "${module_changed}" ]] ||
    fail "boot animation selection did not change the fingerprint"

  python3 - "${TEST_ROOT}/custom/boot-animation/bootanimation.zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
    archive.writestr("part0/frame.png", b"different-frame")
PY
  second_boot_changed="$(fingerprint)"
  [[ "${second_boot_changed}" != "${boot_changed}" ]] ||
    fail "enabled boot-animation payload change did not change the fingerprint"

  python3 - "${TEST_ROOT}/custom/boot-animation/bootanimation-dark.zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
    archive.writestr("part0/frame.png", b"dark-frame")
PY
  _resolve_boot_animation_payloads() {
    BOOT_ANIMATION_LIGHT_PAYLOAD="${TEST_ROOT}/custom/boot-animation/bootanimation.zip"
    BOOT_ANIMATION_DARK_PAYLOAD="${TEST_ROOT}/custom/boot-animation/bootanimation-dark.zip"
  }
  local dark_boot_changed
  dark_boot_changed="$(fingerprint)"
  [[ "${dark_boot_changed}" != "${second_boot_changed}" ]] ||
    fail "dark boot-animation payload did not change the fingerprint"
)

test_output_filename_contains_fingerprint() (
  local full

  load_contract
  set_selection_fixture
  DEVICE_NAME="shiba"
  VERSION[GRAPHENEOS]="2026071700"
  full="$(fingerprint)"

  git() {
    if [[ "${1:-}" == "rev-parse" ]]; then
      printf '%s\n' deadbee
    else
      command git "$@"
    fi
  }
  dirty_suffix() { :; }

  generate_ota_info
  [[ "${OUTPUTS[PATCHED_OTA]}" == *"-${full}-"* ]] ||
    fail "output filename does not include selection fingerprint: ${OUTPUTS[PATCHED_OTA]}"
)

test_output_policy() (
  local scope

  load_contract
  set_selection_fixture

  for scope in local-unpublished private shared published; do
    enforce_output_policy "${scope}" >/dev/null ||
      fail "disabled legacy profile rejected allowed scope ${scope}"
  done
  enforce_publication_evidence published >/dev/null ||
    fail "legacy profile rejected publication evidence gate"

  ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]="true"
  enforce_output_policy local-unpublished >/dev/null ||
    fail "F-Droid profile rejected local-unpublished output"
  for scope in private shared published; do
    if enforce_output_policy "${scope}" >/dev/null 2>&1; then
      fail "F-Droid profile unexpectedly allowed ${scope} output"
    fi
  done
  if enforce_publication_evidence published >/dev/null 2>&1; then
    fail "F-Droid profile unexpectedly passed publication evidence gate"
  fi

  if enforce_output_policy unknown-scope >/dev/null 2>&1; then
    fail "unknown output scope unexpectedly passed policy enforcement"
  fi
)

test_profiles_are_stable
test_magisk_compatibility_defaults
test_unknown_rom_fails_closed
test_invalid_compatible_sepolicy_fails_closed
test_selection_fingerprint
test_output_filename_contains_fingerprint
test_output_policy

echo "ROM contract tests passed"
