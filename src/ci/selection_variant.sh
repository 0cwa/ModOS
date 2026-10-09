#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Canonical serialization for the build selection identity. Callers resolve
# profile values, validate local inputs, and populate these explicit fields;
# this helper owns the byte-for-byte representation that is hashed by both the
# build and release preflight paths.

BOOT_ANIMATION_SELECTION_CONTRACT="product-image-root-theme-context-deterministic-stored-v7"

function selection_variant_manifest() {
  local field value
  local -a required_fields=(
    SELECTION_ROM_FAMILY
    SELECTION_UPDATE_CHANNEL
    SELECTION_UPDATE_TYPE
    SELECTION_OUTPUT_SCOPE
    SELECTION_ROOT
    SELECTION_MAGISK_PREINIT
    SELECTION_MAGISK_REPOSITORY
    SELECTION_MAGISK_VERSION
    SELECTION_DEBUG
    SELECTION_COMPATIBLE_SEPOLICY
    SELECTION_CLEAR_VBMETA_FLAGS
    SELECTION_HELPER_COMMIT
    SELECTION_LOCK_SHA256
    SELECTION_PROFILE_SHA256
    SELECTION_MODULE_AFSR
    SELECTION_MODULE_ALTERINSTALLER
    SELECTION_MODULE_BCR
    SELECTION_MODULE_CUSTOTA
    SELECTION_MODULE_FDROID_PRIVILEGED_EXTENSION
    SELECTION_MODULE_MSD
    SELECTION_MODULE_OEMUNLOCKONBOOT
    SELECTION_DISABLE_SYSTEM_UPDATER
    SELECTION_BOOT_ANIMATION
  )

  for field in "${required_fields[@]}"; do
    value="${!field-}"
    if [[ -z "${value}" || "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
      echo "Error: selection identity field ${field} is missing or contains a line break." >&2
      return 1
    fi
  done

  for field in \
    SELECTION_ROOT \
    SELECTION_DEBUG \
    SELECTION_COMPATIBLE_SEPOLICY \
    SELECTION_CLEAR_VBMETA_FLAGS \
    SELECTION_MODULE_AFSR \
    SELECTION_MODULE_ALTERINSTALLER \
    SELECTION_MODULE_BCR \
    SELECTION_MODULE_CUSTOTA \
    SELECTION_MODULE_FDROID_PRIVILEGED_EXTENSION \
    SELECTION_MODULE_MSD \
    SELECTION_MODULE_OEMUNLOCKONBOOT \
    SELECTION_DISABLE_SYSTEM_UPDATER \
    SELECTION_BOOT_ANIMATION; do
    value="${!field}"
    if [[ "${value}" != 'true' && "${value}" != 'false' ]]; then
      echo "Error: selection identity field ${field} must be true or false." >&2
      return 1
    fi
  done

  for field in SELECTION_LOCK_SHA256 SELECTION_PROFILE_SHA256; do
    value="${!field}"
    if [[ "${value}" != 'disabled' && ! "${value}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Error: selection identity field ${field} is not a SHA-256 digest." >&2
      return 1
    fi
  done

  if [[ "${SELECTION_BOOT_ANIMATION}" == 'true' ]]; then
    if [[ ! "${SELECTION_BOOT_ANIMATION_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Error: enabled boot animation lacks a SHA-256 digest." >&2
      return 1
    fi
  fi

  printf '%s\n' \
    'pixene-module-selection-v1' \
    "rom_family=${SELECTION_ROM_FAMILY}" \
    "update_channel=${SELECTION_UPDATE_CHANNEL}" \
    "update_type=${SELECTION_UPDATE_TYPE}" \
    "output_scope=${SELECTION_OUTPUT_SCOPE}" \
    "root=${SELECTION_ROOT}" \
    "magisk_preinit=${SELECTION_MAGISK_PREINIT}" \
    "debug=${SELECTION_DEBUG}" \
    "compatible_sepolicy=${SELECTION_COMPATIBLE_SEPOLICY}" \
    "clear_vbmeta_flags=${SELECTION_CLEAR_VBMETA_FLAGS}" \
    "helper_commit=${SELECTION_HELPER_COMMIT}" \
    "lock_sha256=${SELECTION_LOCK_SHA256}" \
    "profile_sha256=${SELECTION_PROFILE_SHA256}" \
    "module.afsr=${SELECTION_MODULE_AFSR}" \
    "module.alterinstaller=${SELECTION_MODULE_ALTERINSTALLER}" \
    "module.bcr=${SELECTION_MODULE_BCR}" \
    "module.custota=${SELECTION_MODULE_CUSTOTA}" \
    "module.fdroid-privileged-extension=${SELECTION_MODULE_FDROID_PRIVILEGED_EXTENSION}" \
    "module.msd=${SELECTION_MODULE_MSD}" \
    "module.oemunlockonboot=${SELECTION_MODULE_OEMUNLOCKONBOOT}" \
    "disable_system_updater=${SELECTION_DISABLE_SYSTEM_UPDATER}"

  if [[ "${SELECTION_ROOT}" == 'true' ]]; then
    if [[ ! "${SELECTION_MAGISK_REPOSITORY}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
      echo "Error: rooted selection identity has an invalid Magisk repository." >&2
      return 1
    fi
    if [[ ! "${SELECTION_MAGISK_VERSION}" =~ ^v[0-9]+([.][0-9A-Za-z_-]+)*$ ]]; then
      echo "Error: rooted selection identity has an invalid Magisk version." >&2
      return 1
    fi
    printf '%s\n' \
      "magisk_repository=${SELECTION_MAGISK_REPOSITORY}" \
      "magisk_version=${SELECTION_MAGISK_VERSION}"
  fi

  if [[ "${SELECTION_BOOT_ANIMATION}" == 'true' ]]; then
    printf '%s\n' \
      'boot_animation=true' \
      "boot_animation_contract=${BOOT_ANIMATION_SELECTION_CONTRACT}" \
      "boot_animation_sha256=${SELECTION_BOOT_ANIMATION_SHA256}"
  fi
}

function selection_variant_fingerprint() {
  local fingerprint manifest

  manifest="$(selection_variant_manifest)" || return 1
  fingerprint="$(printf '%s\n' "${manifest}" | sha256sum | awk '{print $1}')" || return 1
  if [[ ! "${fingerprint}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Error: failed to compute the selection identity fingerprint." >&2
    return 1
  fi
  printf '%s\n' "${fingerprint}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    manifest)
      selection_variant_manifest
      ;;
    fingerprint)
      selection_variant_fingerprint
      ;;
    *)
      echo "usage: ${0} {manifest|fingerprint}" >&2
      exit 2
      ;;
  esac
fi
