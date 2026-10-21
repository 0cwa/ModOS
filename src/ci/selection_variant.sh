#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Canonical serialization for the build selection identity. Callers resolve
# profile values, validate local inputs, and populate these explicit fields;
# this helper owns the byte-for-byte representation that is hashed by both the
# build and release preflight paths.

BOOT_ANIMATION_SELECTION_CONTRACT="product-image-root-theme-context-deterministic-stored-v7"
_selection_variant_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "${_selection_variant_source_dir}/libclang_identity.sh"
unset _selection_variant_source_dir

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
    SELECTION_MODULE_MICROG
    SELECTION_MODULE_MSD
    SELECTION_MODULE_OEMUNLOCKONBOOT
    SELECTION_DISABLE_SYSTEM_UPDATER
    SELECTION_MODULE_SHIZUKU
    SELECTION_SHIZUKU_LOCK_SHA256
    SELECTION_SHIZUKU_ROOT_MODE
    SELECTION_MODULE_EXFATPROGS_FSCK
    SELECTION_EXFATPROGS_FSCK_LOCK_SHA256
    SELECTION_EXFATPROGS_FSCK_ADAPTER_SHA256
    SELECTION_EXFATPROGS_FSCK_PAYLOAD_SHA256
    SELECTION_EXFATPROGS_FSCK_PROVENANCE_SHA256
    SELECTION_MODULE_FSCK_SYSFS_SEPOLICY
    SELECTION_FSCK_SYSFS_SEPOLICY_ADAPTER_SHA256
    SELECTION_MODULE_LINEAGE_VOLD_EXFAT_TIMEOUT
    SELECTION_LINEAGE_VOLD_LOCK_SHA256
    SELECTION_LINEAGE_VOLD_ADAPTER_SHA256
    SELECTION_LINEAGE_VOLD_BUILDER_SHA256
    SELECTION_LINEAGE_VOLD_PATCH_SHA256
    SELECTION_LINEAGE_VOLD_MANIFEST_SHA256
    SELECTION_LINEAGE_VOLD_BINARY_SHA256
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
    SELECTION_MODULE_MICROG \
    SELECTION_MODULE_MSD \
    SELECTION_MODULE_OEMUNLOCKONBOOT \
    SELECTION_DISABLE_SYSTEM_UPDATER \
    SELECTION_MODULE_SHIZUKU \
    SELECTION_MODULE_EXFATPROGS_FSCK \
    SELECTION_MODULE_FSCK_SYSFS_SEPOLICY \
    SELECTION_MODULE_LINEAGE_VOLD_EXFAT_TIMEOUT \
    SELECTION_BOOT_ANIMATION; do
    value="${!field}"
    if [[ "${value}" != 'true' && "${value}" != 'false' ]]; then
      echo "Error: selection identity field ${field} must be true or false." >&2
      return 1
    fi
  done

  for field in SELECTION_LOCK_SHA256 SELECTION_PROFILE_SHA256 \
    SELECTION_SHIZUKU_LOCK_SHA256 SELECTION_EXFATPROGS_FSCK_LOCK_SHA256 \
    SELECTION_EXFATPROGS_FSCK_ADAPTER_SHA256 \
    SELECTION_EXFATPROGS_FSCK_PAYLOAD_SHA256 \
    SELECTION_EXFATPROGS_FSCK_PROVENANCE_SHA256 \
    SELECTION_FSCK_SYSFS_SEPOLICY_ADAPTER_SHA256 \
    SELECTION_LINEAGE_VOLD_LOCK_SHA256 \
    SELECTION_LINEAGE_VOLD_ADAPTER_SHA256 \
    SELECTION_LINEAGE_VOLD_BUILDER_SHA256 \
    SELECTION_LINEAGE_VOLD_PATCH_SHA256 \
    SELECTION_LINEAGE_VOLD_MANIFEST_SHA256 \
    SELECTION_LINEAGE_VOLD_BINARY_SHA256; do
    value="${!field}"
    if [[ "${value}" != 'disabled' && ! "${value}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Error: selection identity field ${field} is not a SHA-256 digest." >&2
      return 1
    fi
  done

  if [[ "${SELECTION_SHIZUKU_ROOT_MODE}" != disabled &&
    "${SELECTION_SHIZUKU_ROOT_MODE}" != rootless &&
    "${SELECTION_SHIZUKU_ROOT_MODE}" != magisk ]]; then
    echo "Error: selection identity field SELECTION_SHIZUKU_ROOT_MODE is invalid." >&2
    return 1
  fi

  if [[ "${SELECTION_BOOT_ANIMATION}" == 'true' ]]; then
    if [[ ! "${SELECTION_BOOT_ANIMATION_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Error: enabled boot animation lacks a SHA-256 digest." >&2
      return 1
    fi
  fi

  if [[ "${SELECTION_MODULE_EXFATPROGS_FSCK}" == 'true' ]]; then
    for field in SELECTION_EXFATPROGS_FSCK_LOCK_SHA256 \
      SELECTION_EXFATPROGS_FSCK_ADAPTER_SHA256 \
      SELECTION_EXFATPROGS_FSCK_PAYLOAD_SHA256 \
      SELECTION_EXFATPROGS_FSCK_PROVENANCE_SHA256; do
      if [[ ! "${!field}" =~ ^[0-9a-f]{64}$ ]]; then
        echo "Error: enabled exfatprogs fsck lacks a checked digest in ${field}." >&2
        return 1
      fi
    done
  else
    for field in SELECTION_EXFATPROGS_FSCK_LOCK_SHA256 \
      SELECTION_EXFATPROGS_FSCK_ADAPTER_SHA256 \
      SELECTION_EXFATPROGS_FSCK_PAYLOAD_SHA256 \
      SELECTION_EXFATPROGS_FSCK_PROVENANCE_SHA256; do
      if [[ "${!field}" != 'disabled' ]]; then
        echo "Error: disabled exfatprogs fsck has an unexpected digest in ${field}." >&2
        return 1
      fi
    done
  fi

  if [[ "${SELECTION_MODULE_FSCK_SYSFS_SEPOLICY}" == 'true' ]]; then
    if [[ ! "${SELECTION_FSCK_SYSFS_SEPOLICY_ADAPTER_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Error: enabled fsck sysfs SELinux module lacks a checked adapter digest." >&2
      return 1
    fi
  elif [[ "${SELECTION_FSCK_SYSFS_SEPOLICY_ADAPTER_SHA256}" != 'disabled' ]]; then
    echo "Error: disabled fsck sysfs SELinux module has an unexpected adapter digest." >&2
    return 1
  fi

  if [[ "${SELECTION_MODULE_LINEAGE_VOLD_EXFAT_TIMEOUT}" == 'true' ]]; then
    for field in SELECTION_LINEAGE_VOLD_LOCK_SHA256 \
      SELECTION_LINEAGE_VOLD_ADAPTER_SHA256 \
      SELECTION_LINEAGE_VOLD_BUILDER_SHA256 \
      SELECTION_LINEAGE_VOLD_PATCH_SHA256 \
      SELECTION_LINEAGE_VOLD_MANIFEST_SHA256 \
      SELECTION_LINEAGE_VOLD_BINARY_SHA256; do
      if [[ ! "${!field}" =~ ^[0-9a-f]{64}$ ]]; then
        echo "Error: enabled source-built Vold lacks a checked digest in ${field}." >&2
        return 1
      fi
    done
  else
    for field in SELECTION_LINEAGE_VOLD_LOCK_SHA256 \
      SELECTION_LINEAGE_VOLD_ADAPTER_SHA256 \
      SELECTION_LINEAGE_VOLD_BUILDER_SHA256 \
      SELECTION_LINEAGE_VOLD_PATCH_SHA256 \
      SELECTION_LINEAGE_VOLD_MANIFEST_SHA256 \
      SELECTION_LINEAGE_VOLD_BINARY_SHA256; do
      if [[ "${!field}" != 'disabled' ]]; then
        echo "Error: disabled source-built Vold has an unexpected digest in ${field}." >&2
        return 1
      fi
    done
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
    "module.microg=${SELECTION_MODULE_MICROG}" \
    "module.msd=${SELECTION_MODULE_MSD}" \
    "module.oemunlockonboot=${SELECTION_MODULE_OEMUNLOCKONBOOT}" \
    "disable_system_updater=${SELECTION_DISABLE_SYSTEM_UPDATER}" \
    "module.shizuku=${SELECTION_MODULE_SHIZUKU}" \
    "shizuku_lock_sha256=${SELECTION_SHIZUKU_LOCK_SHA256}" \
    "shizuku_root_mode=${SELECTION_SHIZUKU_ROOT_MODE}" \
    "module.exfatprogs-fsck=${SELECTION_MODULE_EXFATPROGS_FSCK}" \
    "exfatprogs_fsck_lock_sha256=${SELECTION_EXFATPROGS_FSCK_LOCK_SHA256}" \
    "exfatprogs_fsck_adapter_sha256=${SELECTION_EXFATPROGS_FSCK_ADAPTER_SHA256}" \
    "exfatprogs_fsck_payload_sha256=${SELECTION_EXFATPROGS_FSCK_PAYLOAD_SHA256}" \
    "exfatprogs_fsck_provenance_sha256=${SELECTION_EXFATPROGS_FSCK_PROVENANCE_SHA256}"

  printf '%s\n' \
    "module.fsck-sysfs-sepolicy=${SELECTION_MODULE_FSCK_SYSFS_SEPOLICY}" \
    "fsck_sysfs_sepolicy_adapter_sha256=${SELECTION_FSCK_SYSFS_SEPOLICY_ADAPTER_SHA256}"

  # Preserve the existing release fingerprint whenever this optional module
  # is disabled; include its artifact digests only for enabled builds.
  if [[ "${SELECTION_MODULE_LINEAGE_VOLD_EXFAT_TIMEOUT}" == "true" ]]; then
    printf '%s\n' \
      "module.lineage-vold-exfat-timeout=true" \
      "lineage_vold_lock_sha256=${SELECTION_LINEAGE_VOLD_LOCK_SHA256}" \
      "lineage_vold_adapter_sha256=${SELECTION_LINEAGE_VOLD_ADAPTER_SHA256}" \
      "lineage_vold_builder_sha256=${SELECTION_LINEAGE_VOLD_BUILDER_SHA256}" \
      "lineage_vold_patch_sha256=${SELECTION_LINEAGE_VOLD_PATCH_SHA256}" \
      "lineage_vold_manifest_sha256=${SELECTION_LINEAGE_VOLD_MANIFEST_SHA256}" \
      "lineage_vold_binary_sha256=${SELECTION_LINEAGE_VOLD_BINARY_SHA256}"
  fi

  if [[ "${SELECTION_MODULE_FSCK_SYSFS_SEPOLICY}" == 'true' ]]; then
    local policy_patcher_root libclang_identity policy_patcher_digest value
    local -a policy_patcher_inputs
    policy_patcher_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)" || return 1
    libclang_identity="$(resolve_libclang_identity)" || return 1
    policy_patcher_inputs=(
      "${policy_patcher_root}/src/exfatprogs_sepolicy/Cargo.toml"
      "${policy_patcher_root}/src/exfatprogs_sepolicy/Cargo.lock"
      "${policy_patcher_root}/src/exfatprogs_sepolicy/PROVENANCE.json"
      "${policy_patcher_root}/src/exfatprogs_sepolicy/src/main.rs"
      "${policy_patcher_root}/src/fsck_sysfs_sepolicy.py"
      "${policy_patcher_root}/src/ci/libclang_identity.sh"
    )
    for value in "${policy_patcher_inputs[@]}"; do
      if [[ ! -f "${value}" || -L "${value}" ]]; then
        echo "Error: fsck sysfs policy patcher input is missing or unsafe: ${value}" >&2
        return 1
      fi
    done
    policy_patcher_digest="$(
      {
        printf '%s\n' 'build-host=ubuntu-24.04' 'rust-toolchain=1.97.1'
        printf '%s\n' "${libclang_identity}"
        for value in "${policy_patcher_inputs[@]}"; do
          printf '%s ' "${value#"${policy_patcher_root}"/}"
          sha256sum <"${value}" | awk '{print $1}'
        done
      } | sha256sum | awk '{print $1}'
    )" || return 1
    if [[ ! "${policy_patcher_digest}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Error: failed to compute fsck sysfs policy patcher input digest." >&2
      return 1
    fi
    printf '%s\n' "fsck_sysfs_policy_patcher_sha256=${policy_patcher_digest}"
  fi

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
