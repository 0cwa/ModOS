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
  ADDITIONALS[MICROG]="false"
  ADDITIONALS[SHIZUKU]="false"
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
  local baseline repeated afsr_on afsr_off root_changed rom_changed microg_changed module_changed
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
    if (($#)); then
      command sha256sum "$@"
    else
      tee "${fingerprint_input}" | command sha256sum
    fi
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

  ADDITIONALS[MICROG]="true"
  microg_changed="$(fingerprint)"
  [[ "${microg_changed}" != "${rom_changed}" ]] ||
    fail "microG selection did not change the fingerprint"
  ADDITIONALS[MICROG]="false"

  ROM_FAMILY="grapheneos"
  resolve_rom_profile
  ADDITIONALS[MICROG]="true"
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "GrapheneOS unexpectedly accepted microG"
  fi
  ADDITIONALS[MICROG]="false"
  ADDITIONALS[CUSTOTA]="false"
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

test_executable_lock_is_required_and_fingerprinted() (
  local fixture_repo="${TEST_ROOT}/executable-lock-contract"
  local baseline updated

  load_contract
  set_selection_fixture
  mkdir -p "${fixture_repo}/locks" "${fixture_repo}/src"
  cp locks/executable-tools-v1.json "${fixture_repo}/locks/"
  cp src/build_diagnostics.py "${fixture_repo}/src/"
  git -C "${fixture_repo}" init -q
  git -C "${fixture_repo}" config user.email fixture@example.invalid
  git -C "${fixture_repo}" config user.name fixture
  git -C "${fixture_repo}" add locks/executable-tools-v1.json src/build_diagnostics.py
  git -C "${fixture_repo}" commit -q -m 'Original executable lock'
  cd "${fixture_repo}"

  baseline="$(fingerprint)"
  assert_equals "${baseline}" "$(fingerprint)" \
    "unchanged committed executable lock"
  module_selection_fingerprint >/dev/null
  SELECTION_EXECUTABLE_TOOLS_LOCK_SHA256=disabled
  if selection_variant_fingerprint >/dev/null 2>&1; then
    fail "executable lock digest was unexpectedly optional"
  fi

  # Model an executable payload update without changing modules or helper.
  python3 - <<'PY'
import json
from pathlib import Path

path = Path("locks/executable-tools-v1.json")
lock = json.loads(path.read_text())
lock["tools"][0]["sha256"] = "0" * 64
path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
PY
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "dirty executable lock unexpectedly produced a fingerprint"
  fi
  git add locks/executable-tools-v1.json
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "staged executable lock unexpectedly produced a fingerprint"
  fi
  git commit -q -m 'Updated executable payload'
  updated="$(fingerprint)"
  [[ "${updated}" != "${baseline}" ]] ||
    fail "committed executable lock update did not change the fingerprint"
  module_selection_fingerprint >/dev/null
  assert_equals "${updated}" "$(selection_variant_fingerprint)" \
    "updated executable lock preserves build/preflight identity parity"

  baseline="${updated}"
  printf '\n# Reviewed diagnostic-contract fixture change.\n' >> src/build_diagnostics.py
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail 'Dirty diagnostic guard unexpectedly produced a fingerprint'
  fi
  git add src/build_diagnostics.py
  git commit -q -m 'Updated diagnostic contract'
  updated="$(fingerprint)"
  [[ "${updated}" != "${baseline}" ]] ||
    fail 'Diagnostic guard update did not change the build selection identity'
  module_selection_fingerprint >/dev/null
  SELECTION_BUILD_DIAGNOSTICS_SHA256=disabled
  if selection_variant_fingerprint >/dev/null 2>&1; then
    fail 'Diagnostic guard digest was unexpectedly optional'
  fi

  mv locks/executable-tools-v1.json "${TEST_ROOT}/saved-executable-lock.json"
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "missing executable lock unexpectedly produced a fingerprint"
  fi
  ln -s "${TEST_ROOT}/saved-executable-lock.json" locks/executable-tools-v1.json
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "symlink executable lock unexpectedly produced a fingerprint"
  fi
  rm locks/executable-tools-v1.json
  mv "${TEST_ROOT}/saved-executable-lock.json" locks/executable-tools-v1.json
  git rm -q --cached locks/executable-tools-v1.json
  git commit -q -m 'Remove executable lock from fixture index'
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "untracked executable lock unexpectedly produced a fingerprint"
  fi
)

test_selected_legacy_module_versions_are_fingerprinted() (
  local module original baseline changed disabled invalid
  local -a modules=(ALTERINSTALLER BCR CUSTOTA MSD OEMUNLOCKONBOOT)

  load_contract
  set_selection_fixture
  # BCR is selectable only on LineageOS; exercise enabled-version identity
  # using the family that can actually inject every legacy module.
  ROM_FAMILY=lineageos
  ADDITIONALS[BCR]=true
  baseline="$(fingerprint)"
  for module in "${modules[@]}"; do
    original="${VERSION[${module}]}"
    VERSION[${module}]="${original}.1"
    changed="$(fingerprint)"
    [[ "${changed}" != "${baseline}" ]] ||
      fail "enabled ${module} version update did not change the fingerprint"

    unset 'VERSION['"${module}"']'
    if module_selection_fingerprint >/dev/null 2>&1; then
      fail "enabled ${module} accepted a missing version"
    fi
    for invalid in '' disabled '3/11' '3 11' $'3.11\ninjected=true'; do
      VERSION[${module}]="${invalid}"
      if module_selection_fingerprint >/dev/null 2>&1; then
        fail "enabled ${module} accepted an invalid version"
      fi
    done

    ADDITIONALS[${module}]=false
    unset 'VERSION['"${module}"']'
    disabled="$(fingerprint)"
    VERSION[${module}]="${original}.1"
    assert_equals "${disabled}" "$(fingerprint)" \
      "disabled ${module} version must not affect selection"
    module_selection_fingerprint >/dev/null
    local version_field="SELECTION_MODULE_${module}_VERSION"
    assert_equals disabled "${!version_field}" \
      "disabled ${module} canonical version"

    VERSION[${module}]="${original}"
    ADDITIONALS[${module}]=true
    assert_equals "${baseline}" "$(fingerprint)" \
      "restored ${module} version must restore selection"
  done
)

test_bcr_family_policy_and_effective_identity() (
  local enabled disabled graphene invalid

  load_contract
  ADDITIONALS[BOOT_ANIMATION]=false
  ROM_FAMILY=lineageos
  resolve_rom_profile
  assert_equals true "${ADDITIONALS[BCR]}" "LineageOS default BCR selection"
  enabled="$(fingerprint)"
  module_selection_fingerprint >/dev/null
  assert_equals true "${SELECTION_MODULE_BCR}" "LineageOS enabled BCR identity"
  assert_equals "${VERSION[BCR]}" "${SELECTION_MODULE_BCR_VERSION}" \
    "LineageOS enabled BCR version"

  ADDITIONALS[BCR]=false
  disabled="$(fingerprint)"
  [[ "${disabled}" != "${enabled}" ]] ||
    fail 'LineageOS BCR opt-out did not change the fingerprint'
  assert_equals false "${ADDITIONALS[BCR]}" "LineageOS explicit BCR opt-out"

  ROM_FAMILY=grapheneos
  # A stale true from env.toml or a workflow must be normalized before identity
  # generation, so GrapheneOS cannot reuse an older BCR-enabled build.
  ADDITIONALS[BCR]=true
  module_selection_fingerprint >/dev/null
  graphene="${MODULE_SELECTION_FINGERPRINT}"
  assert_equals false "${ADDITIONALS[BCR]}" "GrapheneOS effective BCR selection"
  assert_equals false "${SELECTION_MODULE_BCR}" "GrapheneOS BCR identity"
  assert_equals disabled "${SELECTION_MODULE_BCR_VERSION}" "GrapheneOS BCR version"
  VERSION[BCR]=unused-invalid-version/
  assert_equals "${graphene}" "$(fingerprint)" "GrapheneOS ignores disabled BCR version"
  ADDITIONALS[BCR]=true
  assert_equals "${graphene}" "$(fingerprint)" "GrapheneOS stale true has canonical identity"

  # Invalid configuration remains an error even in the family that suppresses
  # this module; normalization must not hide a malformed boolean.
  for ROM_FAMILY in grapheneos lineageos; do
    for invalid in '' yes 1; do
      ADDITIONALS[BCR]="${invalid}"
      if resolve_rom_profile >/dev/null 2>&1; then
        fail "${ROM_FAMILY} accepted malformed BCR selection ${invalid@Q}"
      fi
    done
  done
)

test_shizuku_selection_follows_supported_rom_profiles() (
  local profile family device
  local -a cases=(
    "grapheneos bramble"
    "grapheneos shiba"
    "lineageos pdx235"
    "lineageos provider_device"
  )

  load_contract
  set_selection_fixture
  _locked_input_digest() {
    [[ "${1}" == "locks/shizuku-v13.6.0.json" ||
      "${1}" == "src/shizuku.py" ||
      "${1}" == "locks/executable-tools-v1.json" ||
      "${1}" == "src/build_diagnostics.py" ]] || return 1
    printf '%064d\n' 0
  }
  ADDITIONALS[SHIZUKU]="true"
  ROOT_MODE="rootless"

  for profile in "${cases[@]}"; do
    family="${profile%% *}"
    device="${profile#* }"
    ROM_FAMILY="${family}"
    DEVICE_NAME="${device}"
    resolve_rom_profile
    module_selection_fingerprint >/dev/null ||
      fail "Shizuku selection rejected ${family}/${device} under a supported profile"
    assert_equals "rootless" "${SELECTION_SHIZUKU_ROOT_MODE}" \
      "Shizuku selection mode for ${family}/${device}"
  done

  ROOT_MODE="both"
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "Shizuku selection unexpectedly accepted shared rootless/Magisk mode"
  fi
)

test_shizuku_adapter_is_required_and_fingerprinted_only_when_enabled() (
  local fixture_repo="${TEST_ROOT}/shizuku-adapter-contract"
  local disabled baseline updated adapter_digest manifest

  load_contract
  set_selection_fixture
  ROOT_MODE="rootless"
  mkdir -p "${fixture_repo}/locks" "${fixture_repo}/src"
  cp locks/executable-tools-v1.json locks/shizuku-v13.6.0.json "${fixture_repo}/locks/"
  cp src/shizuku.py src/build_diagnostics.py "${fixture_repo}/src/"
  git -C "${fixture_repo}" init -q
  git -C "${fixture_repo}" config user.email fixture@example.invalid
  git -C "${fixture_repo}" config user.name fixture
  git -C "${fixture_repo}" add locks src
  git -C "${fixture_repo}" commit -q -m 'Original Shizuku adapter'
  cd "${fixture_repo}"

  disabled="$(fingerprint)"
  module_selection_fingerprint >/dev/null
  unset SELECTION_SHIZUKU_ADAPTER_SHA256
  assert_equals "${disabled}" "$(selection_variant_fingerprint)" \
    "disabled Shizuku must not require the new adapter identity field"
  manifest="$(selection_variant_manifest)"
  if grep -q '^shizuku_adapter_sha256=' <<<"${manifest}"; then
    fail "disabled Shizuku changed the serialized selection contract"
  fi

  ADDITIONALS[SHIZUKU]="true"
  baseline="$(fingerprint)"
  module_selection_fingerprint >/dev/null
  adapter_digest="$(sha256sum src/shizuku.py | awk '{print $1}')"
  assert_equals "${adapter_digest}" "${SELECTION_SHIZUKU_ADAPTER_SHA256}" \
    "enabled Shizuku must bind its actual adapter bytes"
  manifest="$(selection_variant_manifest)"
  grep -qx "shizuku_adapter_sha256=${adapter_digest}" <<<"${manifest}" ||
    fail "enabled Shizuku did not serialize its adapter digest"
  unset SELECTION_SHIZUKU_ADAPTER_SHA256
  if selection_variant_fingerprint >/dev/null 2>&1; then
    fail "enabled Shizuku unexpectedly accepted a missing adapter digest"
  fi
  SELECTION_SHIZUKU_ADAPTER_SHA256=invalid
  if selection_variant_fingerprint >/dev/null 2>&1; then
    fail "enabled Shizuku unexpectedly accepted an invalid adapter digest"
  fi

  # Keep the APK lock and helper fixed: launcher changes alone must rebuild.
  printf '\n# changed launcher fixture\n' >>src/shizuku.py
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "dirty Shizuku adapter unexpectedly produced a fingerprint"
  fi
  ADDITIONALS[SHIZUKU]="false"
  assert_equals "${disabled}" "$(fingerprint)" \
    "disabled Shizuku must ignore adapter changes"
  ADDITIONALS[SHIZUKU]="true"
  git add src/shizuku.py
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "staged Shizuku adapter unexpectedly produced a fingerprint"
  fi
  git commit -q -m 'Updated Shizuku launcher'
  updated="$(fingerprint)"
  [[ "${updated}" != "${baseline}" ]] ||
    fail "committed Shizuku launcher update reused the old selection identity"
  module_selection_fingerprint >/dev/null
  assert_equals "${updated}" "$(selection_variant_fingerprint)" \
    "updated Shizuku adapter preserves build/preflight identity parity"

  mv src/shizuku.py "${TEST_ROOT}/saved-shizuku-adapter.py"
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "missing Shizuku adapter unexpectedly produced a fingerprint"
  fi
  ln -s "${TEST_ROOT}/saved-shizuku-adapter.py" src/shizuku.py
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "symlink Shizuku adapter unexpectedly produced a fingerprint"
  fi
  ADDITIONALS[SHIZUKU]="false"
  assert_equals "${disabled}" "$(fingerprint)" \
    "disabled Shizuku must not require a usable adapter"
)

test_fsck_sysfs_sepolicy_is_pdx235_scoped_and_fingerprinted() (
  local disabled enabled

  resolve_libclang_identity() {
    printf '%s\n' \
      'libclang_selected_path=/fixture/libclang.so' \
      'libclang_path=/fixture/libclang.so' \
      'libclang_sha256=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' \
      'libclang_owner=fixture:1.0'
  }

  load_contract
  set_selection_fixture
  ROM_FAMILY="lineageos"
  DEVICE_NAME="pdx235"
  ADDITIONALS[FSCK_SYSFS_SEPOLICY]="false"
  disabled="$(fingerprint)"

  ADDITIONALS[FSCK_SYSFS_SEPOLICY]="true"
  enabled="$(fingerprint)"
  [[ "${enabled}" != "${disabled}" ]] ||
    fail "fsck sysfs SELinux selection did not change the fingerprint"

  DEVICE_NAME="shiba"
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "fsck sysfs SELinux selection unexpectedly accepted a non-pdx235 device"
  fi
)

# GrapheneOS builds must fail closed even if a caller explicitly requests
# a pdx235-only patch through the reusable workflow or local configuration.
test_grapheneos_rejects_pdx235_fsck_modules() (
  local device module

  load_contract
  set_selection_fixture
  ROM_FAMILY="grapheneos"
  ADDITIONALS[EXFATPROGS_FSCK]="false"
  ADDITIONALS[FSCK_SYSFS_SEPOLICY]="false"

  for device in shiba pdx235; do
    DEVICE_NAME="${device}"
    for module in EXFATPROGS_FSCK FSCK_SYSFS_SEPOLICY; do
      ADDITIONALS["${module}"]="true"
      if module_selection_fingerprint >/dev/null 2>&1; then
        fail "GrapheneOS/${device} unexpectedly accepted pdx235-only ${module}"
      fi
      ADDITIONALS["${module}"]="false"
    done
  done
)

test_output_filename_contains_fingerprint() (
  local full

  load_contract
  set_selection_fixture
  DEVICE_NAME="shiba"
  VERSION[GRAPHENEOS]="2026071700"
  full="$(fingerprint)"

  git() {
    if [[ "${1:-}" == "rev-parse" && "${2:-}" == "--short" ]]; then
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


test_official_key_extraction_uses_root_vbmeta_only() (
  # The root vbmeta can contain chained partitions with their own keys.
  # Assert that the dedicated root key extractor, not avb info, is used.
  WORKDIR="${TEST_ROOT}/key-extract"
  GRAPHENEOS[OTA_TARGET]="lineage-test-signed"
  mkdir -p "${WORKDIR}"
  : >"${WORKDIR}/${GRAPHENEOS[OTA_TARGET]}.zip"

  local expected_key="${WORKDIR}/extracted/avb_pkmd.bin"
  run_executable_tool() {
    [[ "${1:-}" == 'avbroot' && "${2:-}" == 'ota' && "${3:-}" == 'extract' ]] ||
      fail "unexpected AVB extraction command: $*"
    [[ " $* " == *" --public-key-avb ${expected_key} "* ]] ||
      fail "root vbmeta key extraction flag was not passed"
    [[ " $* " == *" --all "* ]] ||
      fail "legacy image extraction was dropped"
    [[ " $* " == *" --input ${WORKDIR}/${GRAPHENEOS[OTA_TARGET]}.zip "* ]] ||
      fail "the wrong OTA was passed for AVB extraction"
    printf 'root-key-only' >"${expected_key}"
  }
  unzip() {
    [[ "${1:-}" == "${WORKDIR}/${GRAPHENEOS[OTA_TARGET]}.zip" ]] ||
      fail "unexpected OTA for certificate extraction"
    mkdir -p "${WORKDIR}/extracted/ota/META-INF/com/android"
    : >"${WORKDIR}/extracted/ota/META-INF/com/android/otacert"
  }

  # A previous attempt may have left a malformed concatenated key.
  mkdir -p "${WORKDIR}/extracted"
  printf 'bad-previous-key' >"${expected_key}"
  extract_official_keys
  assert_equals 'root-key-only' "$(<"${expected_key}")" \
    "root AVB key must come from the scoped extractor"
  [[ -f "${WORKDIR}/extracted/ota/META-INF/com/android/otacert" ]] ||
    fail "OTA certificate extraction changed"
)

test_official_key_extraction_rejects_missing_root_key() (
  WORKDIR="${TEST_ROOT}/unsigned-key-extract"
  GRAPHENEOS[OTA_TARGET]="lineage-test-signed"
  mkdir -p "${WORKDIR}"
  : >"${WORKDIR}/${GRAPHENEOS[OTA_TARGET]}.zip"

  run_executable_tool() {
    [[ "${1:-}" == 'avbroot' && "${2:-}" == 'ota' && "${3:-}" == 'extract' ]] ||
      fail "unexpected AVB extraction command: $*"
    local last='' value=''
    for value in "$@"; do
      if [[ "${last}" == '--public-key-avb' ]]; then
        : >"${value}"
        return
      fi
      last="${value}"
    done
    fail "root AVB output path is missing"
  }
  unzip() { fail "missing root key must fail before certificate extraction"; }

  if extract_official_keys >/dev/null 2>&1; then
    fail "a missing root vbmeta key was unexpectedly accepted"
  fi
)

test_microg_spoof_patch_is_lineage_only_and_fingerprinted_when_enabled() (
  local fixture_repo="${TEST_ROOT}/microg-spoof-contract"
  local disabled enabled cert_changed manifest cert_digest adapter_digest
  local cert_one="${TEST_ROOT}/spoof-cert-one.pem"
  local cert_two="${TEST_ROOT}/spoof-cert-two.pem"

  python3 - "${cert_one}" "${cert_two}" <<'PY'
import base64, sys
for path, fill in zip(sys.argv[1:], (1, 2)):
    der = b"\x30\x82\x01\x00" + bytes([fill]) * 512
    open(path, "w").write(
        "-----BEGIN CERTIFICATE-----\n"
        + base64.b64encode(der).decode()
        + "\n-----END CERTIFICATE-----\n"
    )
PY

  load_contract
  set_selection_fixture
  ROM_FAMILY="lineageos"
  resolve_rom_profile
  ROOT_MODE="rootless"
  mkdir -p "${fixture_repo}/locks" "${fixture_repo}/src"
  cp locks/executable-tools-v1.json "${fixture_repo}/locks/"
  cp src/build_diagnostics.py src/microg_spoof_patch.py "${fixture_repo}/src/"
  git -C "${fixture_repo}" init -q
  git -C "${fixture_repo}" config user.email fixture@example.invalid
  git -C "${fixture_repo}" config user.name fixture
  git -C "${fixture_repo}" add locks src
  git -C "${fixture_repo}" commit -q -m 'Original signer patch adapter'
  cd "${fixture_repo}"

  disabled="$(fingerprint)"
  module_selection_fingerprint >/dev/null
  unset SELECTION_MICROG_SPOOF_PATCH_ADAPTER_SHA256 SELECTION_MICROG_SPOOF_PATCH_CERT_SHA256
  assert_equals "${disabled}" "$(selection_variant_fingerprint)" \
    "disabled signer patch must not require its identity fields"
  manifest="$(selection_variant_manifest)"
  if grep -q 'microg.spoof.patch' <<<"${manifest}"; then
    fail "disabled signer patch changed the serialized selection contract"
  fi

  ADDITIONALS[MICROG_SPOOF_PATCH]="true"
  MICROG_COMPANION_CERT="${cert_one}"
  enabled="$(fingerprint)"
  [[ "${enabled}" != "${disabled}" ]] ||
    fail "enabling the signer patch did not change the fingerprint"
  module_selection_fingerprint >/dev/null
  adapter_digest="$(sha256sum src/microg_spoof_patch.py | awk '{print $1}')"
  assert_equals "${adapter_digest}" "${SELECTION_MICROG_SPOOF_PATCH_ADAPTER_SHA256}" \
    "signer patch must bind its actual adapter bytes"
  cert_digest="$(python3 src/microg_spoof_patch.py check-cert "${cert_one}")"
  assert_equals "${cert_digest}" "${SELECTION_MICROG_SPOOF_PATCH_CERT_SHA256}" \
    "signer patch must bind the trusted certificate"
  manifest="$(selection_variant_manifest)"
  grep -qx "microg_spoof_patch_cert_sha256=${cert_digest}" <<<"${manifest}" ||
    fail "enabled signer patch did not serialize its certificate digest"

  MICROG_COMPANION_CERT="${cert_two}"
  cert_changed="$(fingerprint)"
  [[ "${cert_changed}" != "${enabled}" ]] ||
    fail "changing the trusted certificate did not change the fingerprint"

  MICROG_COMPANION_CERT="${TEST_ROOT}/missing-cert.pem"
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "a missing signer certificate was unexpectedly accepted"
  fi

  MICROG_COMPANION_CERT="${cert_one}"
  SELECTION_MICROG_SPOOF_PATCH_CERT_SHA256=invalid
  if selection_variant_fingerprint >/dev/null 2>&1; then
    fail "an invalid certificate identity was unexpectedly accepted"
  fi

  ROM_FAMILY="grapheneos"
  resolve_rom_profile
  if module_selection_fingerprint >/dev/null 2>&1; then
    fail "GrapheneOS unexpectedly accepted the microG signer patch"
  fi
)

test_official_key_extraction_uses_root_vbmeta_only
test_official_key_extraction_rejects_missing_root_key
test_profiles_are_stable
test_magisk_compatibility_defaults
test_unknown_rom_fails_closed
test_invalid_compatible_sepolicy_fails_closed
test_selection_fingerprint
test_executable_lock_is_required_and_fingerprinted
test_selected_legacy_module_versions_are_fingerprinted
test_shizuku_selection_follows_supported_rom_profiles
test_shizuku_adapter_is_required_and_fingerprinted_only_when_enabled
test_microg_spoof_patch_is_lineage_only_and_fingerprinted_when_enabled
test_bcr_family_policy_and_effective_identity
test_fsck_sysfs_sepolicy_is_pdx235_scoped_and_fingerprinted
test_grapheneos_rejects_pdx235_fsck_modules
test_output_filename_contains_fingerprint
test_output_policy

echo "ROM contract tests passed"
