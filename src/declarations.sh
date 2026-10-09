#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

# Declare associative arrays and variables
declare -A ADDITIONALS
declare -A AVBROOT
declare -A GRAPHENEOS
declare -A KEYS
declare -A MAGISK
declare -A OUTPUTS
declare -A ROM_PROFILE
declare -A VERSION
declare -A DECLARATION_CALLER_PRESENT

_declarations_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "${_declarations_source_dir}/config_schema.sh"
source "${_declarations_source_dir}/logger.sh"
unset _declarations_source_dir

if [[ ! ${DECLARATION_CALLER_CAPTURED+x} ]]; then
  # Capture explicitly supplied caller values before declaration defaults are
  # installed. Nested sources include this file again; they must not turn
  # declaration defaults into apparent caller overrides.
  for _canonical in "${CONFIG_SCHEMA_KEYS[@]}"; do
    config_schema_caller_supplied "${_canonical}" &&
      DECLARATION_CALLER_PRESENT[${_canonical}]=true
  done
  unset _canonical
  DECLARATION_CALLER_CAPTURED=true
fi

# Build Specifications
ARCH="x86_64-unknown-linux-gnu" # for Linux
# ARCH="universal-apple-darwin" # for macOS
# ARCH="x86_64-pc-windows-msvc" # for Windows

# Initial setup environment variables
CLEANUP="${CLEANUP:-'false'}"                # Clean up after the script finishes

function _declarations_apply_schema_defaults() {
  local canonical destination destination_kind destination_key policy
  local caller_value current_value default_value

  for canonical in "${CONFIG_SCHEMA_KEYS[@]}"; do
    policy="${CONFIG_SCHEMA_DECLARATION_POLICY[${canonical}]}"
    [[ "${policy}" != workflow-owned ]] || continue

    destination="${CONFIG_SCHEMA_SHELL_DESTINATION[${canonical}]}"
    destination_kind="${CONFIG_SCHEMA_SHELL_DESTINATION_KIND[${canonical}]}"
    destination_key="${CONFIG_SCHEMA_SHELL_DESTINATION_KEY[${canonical}]}"
    default_value="$(config_schema_default "${canonical}")"
    caller_value=''
    if [[ -n "${CONFIG_SCHEMA_CALLER_DESTINATION[${canonical}]}" ]]; then
      caller_value="$(config_schema_caller_value "${canonical}")"
    fi

    case "${destination_kind}:${policy}" in
      scalar:caller-or-default)
        if [[ -v "${destination}" ]]; then
          current_value="${!destination}"
        else
          current_value=''
        fi
        printf -v "${destination}" '%s' "${current_value:-${default_value}}"
        ;;
      array:caller-or-default)
        local -n destination_ref="${destination}"
        destination_ref[${destination_key}]="${caller_value:-${default_value}}"
        ;;
      array:existing-or-default)
        local -n destination_ref="${destination}"
        destination_ref[${destination_key}]="${destination_ref[${destination_key}]:-${default_value}}"
        ;;
      array:caller-or-existing-or-default)
        local -n destination_ref="${destination}"
        destination_ref[${destination_key}]="${caller_value:-${destination_ref[${destination_key}]:-${default_value}}}"
        ;;
      *)
        echo "Error: unsupported declaration policy for ${canonical}." >&2
        return 1
        ;;
    esac
  done
}

_declarations_apply_schema_defaults
unset -f _declarations_apply_schema_defaults

MODULE_SELECTION_FINGERPRINT="${MODULE_SELECTION_FINGERPRINT:-}"
RESOLVED_ROOT_MODE="${RESOLVED_ROOT_MODE:-}"
MODULE_SELECTION_FINGERPRINT_ROOTLESS="${MODULE_SELECTION_FINGERPRINT_ROOTLESS:-}"
MODULE_SELECTION_FINGERPRINT_MAGISK="${MODULE_SELECTION_FINGERPRINT_MAGISK:-}"
ROM_OTA_SHA256="${ROM_OTA_SHA256:-}"
WORKDIR=".tmp"

# GitHub variables
DOMAIN="https://github.com"
# Release asset owner/repository. Resolved at use time so env.toml values loaded
# after this file, GitHub Actions context, and defaults all participate safely.
PIXENEOS_RELEASE_OWNER="${PIXENEOS_RELEASE_OWNER:-}"
PIXENEOS_RELEASE_REPOSITORY="${PIXENEOS_RELEASE_REPOSITORY:-}"
PIXENEOS_RELEASE_BASE_URL="${PIXENEOS_RELEASE_BASE_URL:-}"
PIXENEOS_AVBROOT_SETUP_SOURCE="${PIXENEOS_AVBROOT_SETUP_SOURCE:-}"

# Application version variables
VERSION[ALTERINSTALLER]="${VERSION[ALTERINSTALLER]:-2.4}"
VERSION[AVBROOT_SETUP]="6a495af445227fa573da4a990bf6434a69f9533f" # green locked microG helper integration
VERSION[BCR]="${VERSION[BCR]:-3.9}"
VERSION[CUSTOTA]="${VERSION[CUSTOTA]:-6.5}"
VERSION[GRAPHENEOS]="${VERSION[GRAPHENEOS]:-}"
VERSION[MAGISK]="${VERSION[MAGISK]:-}"
VERSION[MSD]="${VERSION[MSD]:-2.4}"
VERSION[OEMUNLOCKONBOOT]="${VERSION[OEMUNLOCKONBOOT]:-1.4}"

# Magisk
MAGISK[URL]="${DOMAIN}/${MAGISK[REPOSITORY]}"

# Keys
KEYS[AVB]="${KEYS[AVB]:-avb.key}"
KEYS[AVB_BASE64]="${KEYS[AVB_BASE64]:-''}"
KEYS[CERT_OTA]="${KEYS[CERT_OTA]:-ota.crt}"
KEYS[CERT_OTA_BASE64]="${KEYS[CERT_OTA_BASE64]:-''}"
KEYS[OTA]="${KEYS[OTA]:-ota.key}"
KEYS[OTA_BASE64]="${KEYS[OTA_BASE64]:-''}"
KEYS[PKMD]="${KEYS[PKMD]:-avb_pkmd.bin}"

# Compatibility keys retained for existing callers. resolve_rom_profile fills
# these through the common ROM capability profile.
# TODO: Track a follow-up issue to rename these GRAPHENEOS keys to ROM-neutral
# names across the OTA fetcher, verifier, workflow exports, and phase 3 tests.
GRAPHENEOS[OTA_BASE_URL]="${GRAPHENEOS[OTA_BASE_URL]:-}"
GRAPHENEOS[UPDATE_CHANNEL]="${GRAPHENEOS[UPDATE_CHANNEL]:-}"
GRAPHENEOS[UPDATE_TYPE]="${GRAPHENEOS[UPDATE_TYPE]:-}"
GRAPHENEOS[OTA_URL]="${GRAPHENEOS[OTA_URL]:-}"
GRAPHENEOS[OTA_TARGET]="${GRAPHENEOS[OTA_TARGET]:-}"

# Additionals

# Modules
# Directly configurable module defaults are applied from config_schema.sh.
# F-Droid remains default-off until its lock/profile path has a production policy.
FDROID_PRIVILEGED_EXTENSION_LOCK="${FDROID_PRIVILEGED_EXTENSION_LOCK:-}"
FDROID_PRIVILEGED_EXTENSION_PROFILE="${FDROID_PRIVILEGED_EXTENSION_PROFILE:-}"
FDROID_PRIVILEGED_EXTENSION_CACHE="${FDROID_PRIVILEGED_EXTENSION_CACHE:-}"
FDROID_PRIVILEGED_EXTENSION_PATCH_REPORT="${FDROID_PRIVILEGED_EXTENSION_PATCH_REPORT:-}"
# Tools
ADDITIONALS[AVBROOT]="${ADDITIONALS_AVBROOT:-${ADDITIONALS[AVBROOT]:-true}}" # Android Verified Boot Root
ADDITIONALS[CUSTOTA_TOOL]="${ADDITIONALS_CUSTOTA_TOOL:-${ADDITIONALS[CUSTOTA_TOOL]:-true}}" # Custom OTA Tool
ADDITIONALS[MY_AVBROOT_SETUP]="${ADDITIONALS_MY_AVBROOT_SETUP:-${ADDITIONALS[MY_AVBROOT_SETUP]:-true}}" # My AVBRoot setup
ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]="${ADDITIONALS_MAS_COMPATIBLE_SEPOLICY:-false}" # Compatible SELinux policy

ADDITIONALS[ROOT]="${ADDITIONALS_ROOT:-false}"   # Only Magisk is supported
ADDITIONALS[RETRY]="${ADDITIONALS[RETRY]:-true}" # Auto download signatures
ADDITIONALS[DEBUG]="${ADDITIONALS_DEBUG:-false}" # Enable unauthorized ADB

# Outputs
OUTPUTS[PATCHED_OTA]="${OUTPUTS[PATCHED_OTA]:-}"
OUTPUTS[PATCHED_OTA_ROOTLESS]="${OUTPUTS[PATCHED_OTA_ROOTLESS]:-}"
OUTPUTS[PATCHED_OTA_MAGISK]="${OUTPUTS[PATCHED_OTA_MAGISK]:-}"
OUTPUTS[OTA_METADATA_ROOTLESS]="${OUTPUTS[OTA_METADATA_ROOTLESS]:-}"
OUTPUTS[OTA_METADATA_MAGISK]="${OUTPUTS[OTA_METADATA_MAGISK]:-}"
