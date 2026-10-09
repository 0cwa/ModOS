#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# ROM differences are data consumed by the common build path. Provider-specific
# metadata parsing lives in ota_providers.sh.
declare -gA ROM_PROFILE

_rom_profiles_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "${_rom_profiles_source_dir}/ci/selection_variant.sh"
unset _rom_profiles_source_dir

function _require_profile_boolean() {
  local name="${1}"
  local value="${2}"

  if [[ "${value}" != 'true' && "${value}" != 'false' ]]; then
    echo "Error: ${name} must be true or false." >&2
    return 1
  fi
}

function validate_device_name() {
  if [[ ! "${DEVICE_NAME}" =~ ^[a-z0-9_]+$ ]]; then
    echo "Error: invalid device name." >&2
    return 1
  fi
}

function resolve_rom_profile() {
  case "${ROM_FAMILY}" in
  grapheneos)
    ROM_PROFILE[PROVIDER]="grapheneos"
    ROM_PROFILE[DEFAULT_UPDATE_CHANNEL]="stable"
    ROM_PROFILE[DEFAULT_UPDATE_TYPE]="ota_update"
    ROM_PROFILE[DEFAULT_COMPATIBLE_SEPOLICY]="false"
    ROM_PROFILE[CLEAR_VBMETA_FLAGS]="false"
    ROM_PROFILE[OTA_BASE_URL]="https://releases.grapheneos.org"
    ;;
  lineageos)
    ROM_PROFILE[PROVIDER]="lineageos"
    ROM_PROFILE[DEFAULT_UPDATE_CHANNEL]="nightly"
    ROM_PROFILE[DEFAULT_UPDATE_TYPE]="ota_update"
    ROM_PROFILE[DEFAULT_COMPATIBLE_SEPOLICY]="true"
    ROM_PROFILE[CLEAR_VBMETA_FLAGS]="true"
    ROM_PROFILE[OTA_BASE_URL]="https://download.lineageos.org/api/v2"
    ;;
  *)
    echo "Error: unsupported ROM_FAMILY: ${ROM_FAMILY}" >&2
    return 1
    ;;
  esac

  GRAPHENEOS[OTA_BASE_URL]="${ROM_PROFILE[OTA_BASE_URL]}"
  GRAPHENEOS[UPDATE_CHANNEL]="${GRAPHENEOS_UPDATE_CHANNEL:-${ROM_PROFILE[DEFAULT_UPDATE_CHANNEL]}}"
  GRAPHENEOS[UPDATE_TYPE]="${ROM_UPDATE_TYPE:-${ROM_PROFILE[DEFAULT_UPDATE_TYPE]}}"
  GRAPHENEOS[OTA_URL]="${GRAPHENEOS[OTA_URL]:-}"
  GRAPHENEOS[OTA_TARGET]="${GRAPHENEOS[OTA_TARGET]:-}"

  if [[ -n "${ADDITIONALS_MAS_COMPATIBLE_SEPOLICY:-}" ]]; then
    ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]="${ADDITIONALS_MAS_COMPATIBLE_SEPOLICY}"
  elif [[ "${ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]:-}" != 'true' &&
    "${ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]:-}" != 'false' ]]; then
    : # Preserve a direct invalid value so the validation below rejects it.
  else
    ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]="${ROM_PROFILE[DEFAULT_COMPATIBLE_SEPOLICY]}"
  fi

  _require_profile_boolean \
    ADDITIONALS_MAS_COMPATIBLE_SEPOLICY \
    "${ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]}" || return 1
  _require_profile_boolean \
    ROM_PROFILE_CLEAR_VBMETA_FLAGS \
    "${ROM_PROFILE[CLEAR_VBMETA_FLAGS]}" || return 1
  if [[ ! "${GRAPHENEOS[UPDATE_CHANNEL]}" =~ ^[a-z0-9]+([_-][a-z0-9]+)*$ ||
    ! "${GRAPHENEOS[UPDATE_TYPE]}" =~ ^[a-z0-9]+([_-][a-z0-9]+)*$ ]]; then
    echo "Error: invalid ROM update channel or type." >&2
    return 1
  fi
}

function enforce_output_policy() {
  local output_scope="${1}"

  case "${output_scope}" in
  local-unpublished | private | shared | published) ;;
  *)
    echo "Error: unknown output scope: ${output_scope}" >&2
    return 1
    ;;
  esac

  if [[ "${ADDITIONALS[DEBUG]}" == 'true' &&
    "${output_scope}" != 'local-unpublished' ]]; then
    echo "Error: debug ADB output must remain local and unpublished." >&2
    return 1
  fi

  if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" == 'true' &&
    "${output_scope}" != 'local-unpublished' ]]; then
    echo "Error: F-Droid output is restricted to local-unpublished." >&2
    return 1
  fi
}

function enforce_publication_evidence() {
  local output_scope="${1}"

  if [[ "${output_scope}" != 'published' ]]; then
    echo "Error: publication requires the published output scope." >&2
    return 1
  fi
  enforce_output_policy "${output_scope}" || return 1

  # No locked adapter currently has a reviewed source-delivery publication
  # path. The helper report remains authoritative once such a path exists.
  if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" == 'true' ]]; then
    echo "Error: locked-module publication evidence is unavailable." >&2
    return 1
  fi
}

function _locked_input_digest() {
  local input_path="${1}"

  if ! declare -F verify_checked_in_locked_input >/dev/null; then
    source src/verifier.sh
  fi
  verify_checked_in_locked_input "${input_path}" || return 1
  local digest
  digest="$(sha256sum -- "${input_path}")" || return 1
  digest="${digest%% *}"
  [[ "${digest}" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "${digest}"
}

function _resolve_boot_animation_payloads() {
  local repository_root
  repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || return 1

  BOOT_ANIMATION_LIGHT_PAYLOAD="${repository_root}/custom/boot-animation/bootanimation.zip"
  BOOT_ANIMATION_DARK_PAYLOAD="${repository_root}/custom/boot-animation/bootanimation-dark.zip"

  if [[ ! -e "${BOOT_ANIMATION_LIGHT_PAYLOAD}" && ! -L "${BOOT_ANIMATION_LIGHT_PAYLOAD}" ]]; then
    BOOT_ANIMATION_LIGHT_PAYLOAD=''
  fi
  if [[ ! -e "${BOOT_ANIMATION_DARK_PAYLOAD}" && ! -L "${BOOT_ANIMATION_DARK_PAYLOAD}" ]]; then
    BOOT_ANIMATION_DARK_PAYLOAD=''
  fi
  if [[ -z "${BOOT_ANIMATION_LIGHT_PAYLOAD}" && -z "${BOOT_ANIMATION_DARK_PAYLOAD}" ]]; then
    echo "Error: enabled boot animation requires bootanimation.zip or bootanimation-dark.zip." >&2
    return 1
  fi

  BOOT_ANIMATION_LIGHT_PAYLOAD="${BOOT_ANIMATION_LIGHT_PAYLOAD:-${BOOT_ANIMATION_DARK_PAYLOAD}}"
  BOOT_ANIMATION_DARK_PAYLOAD="${BOOT_ANIMATION_DARK_PAYLOAD:-${BOOT_ANIMATION_LIGHT_PAYLOAD}}"
}

function _boot_animation_payload_path() {
  _resolve_boot_animation_payloads || return 1
  printf '%s\n' "${BOOT_ANIMATION_LIGHT_PAYLOAD}"
}

function _boot_animation_payload_digest() {
  local light_digest dark_digest
  _resolve_boot_animation_payloads || return 1
  light_digest="$(python3 src/boot_animation.py digest "${BOOT_ANIMATION_LIGHT_PAYLOAD}")" || return 1
  dark_digest="$(python3 src/boot_animation.py digest "${BOOT_ANIMATION_DARK_PAYLOAD}")" || return 1
  printf 'light=%s\ndark=%s\n' "${light_digest}" "${dark_digest}" |
    sha256sum | awk '{print $1}'
}

function module_selection_fingerprint() {
  local lock_digest="disabled"
  local profile_digest="disabled"
  local magisk_preinit="disabled"
  local magisk_repository="disabled"
  local magisk_version="disabled"
  local boot_animation_digest="disabled"
  local entry
  local -a module_entries=(
    "afsr:AFSR"
    "alterinstaller:ALTERINSTALLER"
    "bcr:BCR"
    "custota:CUSTOTA"
    "fdroid-privileged-extension:FDROID_PRIVILEGED_EXTENSION"
    "msd:MSD"
    "oemunlockonboot:OEMUNLOCKONBOOT"
    "disable-system-updater:DISABLE_SYSTEM_UPDATER"
  )

  resolve_rom_profile || return 1
  enforce_output_policy "${OUTPUT_SCOPE}" || return 1

  _require_profile_boolean ADDITIONALS_ROOT "${ADDITIONALS[ROOT]}" || return 1
  _require_profile_boolean ADDITIONALS_DEBUG "${ADDITIONALS[DEBUG]}" || return 1
  _require_profile_boolean ADDITIONALS_BOOT_ANIMATION \
    "${ADDITIONALS[BOOT_ANIMATION]}" || return 1
  for entry in "${module_entries[@]}"; do
    _require_profile_boolean \
      "ADDITIONALS_${entry#*:}" \
      "${ADDITIONALS[${entry#*:}]}" || return 1
  done

  if [[ "${ADDITIONALS[BOOT_ANIMATION]}" == 'true' ]]; then
    boot_animation_digest="$(_boot_animation_payload_digest)" || {
      echo "Error: enabled boot animation payload failed validation." >&2
      return 1
    }
  fi

  if [[ "${ADDITIONALS[ROOT]}" == 'true' ]]; then
    magisk_preinit="${MAGISK[PREINIT]}"
    magisk_repository="${MAGISK[REPOSITORY]}"
    magisk_version="${VERSION[MAGISK]}"
    if [[ ! "${magisk_preinit}" =~ ^[A-Za-z0-9._-]+$ ]]; then
      echo "Error: rooted profiles require a canonical Magisk preinit device." >&2
      return 1
    fi
    if [[ ! "${magisk_repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
      echo "Error: rooted profiles require a canonical Magisk repository." >&2
      return 1
    fi
    if [[ ! "${magisk_version}" =~ ^v[0-9]+([.][0-9A-Za-z_-]+)*$ ]]; then
      echo "Error: rooted profiles require a canonical Magisk version tag." >&2
      return 1
    fi
  fi

  if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" == 'true' ]]; then
    lock_digest="$(_locked_input_digest "${FDROID_PRIVILEGED_EXTENSION_LOCK}")" || {
      echo "Error: the F-Droid lock is not clean and checked in." >&2
      return 1
    }
    profile_digest="$(_locked_input_digest "${FDROID_PRIVILEGED_EXTENSION_PROFILE}")" || {
      echo "Error: the F-Droid profile is not clean and checked in." >&2
      return 1
    }
  fi

  SELECTION_ROM_FAMILY="${ROM_FAMILY}"
  SELECTION_UPDATE_CHANNEL="${GRAPHENEOS[UPDATE_CHANNEL]}"
  SELECTION_UPDATE_TYPE="${GRAPHENEOS[UPDATE_TYPE]}"
  SELECTION_OUTPUT_SCOPE="${OUTPUT_SCOPE}"
  SELECTION_ROOT="${ADDITIONALS[ROOT]}"
  SELECTION_MAGISK_PREINIT="${magisk_preinit}"
  SELECTION_MAGISK_REPOSITORY="${magisk_repository}"
  SELECTION_MAGISK_VERSION="${magisk_version}"
  SELECTION_DEBUG="${ADDITIONALS[DEBUG]}"
  SELECTION_COMPATIBLE_SEPOLICY="${ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]}"
  SELECTION_CLEAR_VBMETA_FLAGS="${ROM_PROFILE[CLEAR_VBMETA_FLAGS]}"
  SELECTION_HELPER_COMMIT="${VERSION[AVBROOT_SETUP]}"
  SELECTION_LOCK_SHA256="${lock_digest}"
  SELECTION_PROFILE_SHA256="${profile_digest}"
  SELECTION_MODULE_AFSR="${ADDITIONALS[AFSR]}"
  SELECTION_MODULE_ALTERINSTALLER="${ADDITIONALS[ALTERINSTALLER]}"
  SELECTION_MODULE_BCR="${ADDITIONALS[BCR]}"
  SELECTION_MODULE_CUSTOTA="${ADDITIONALS[CUSTOTA]}"
  SELECTION_MODULE_FDROID_PRIVILEGED_EXTENSION="${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}"
  SELECTION_MODULE_MSD="${ADDITIONALS[MSD]}"
  SELECTION_MODULE_OEMUNLOCKONBOOT="${ADDITIONALS[OEMUNLOCKONBOOT]}"
  SELECTION_DISABLE_SYSTEM_UPDATER="${ADDITIONALS[DISABLE_SYSTEM_UPDATER]}"
  SELECTION_BOOT_ANIMATION="${ADDITIONALS[BOOT_ANIMATION]}"
  SELECTION_BOOT_ANIMATION_SHA256="${boot_animation_digest}"

  MODULE_SELECTION_FINGERPRINT="$(selection_variant_fingerprint)"

  if [[ ! "${MODULE_SELECTION_FINGERPRINT}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Error: failed to compute the module-selection fingerprint." >&2
    return 1
  fi
  printf '%s\n' "${MODULE_SELECTION_FINGERPRINT}"
}
