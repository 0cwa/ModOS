#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Read-only, pre-signing check for the scheduled pdx235 LineageOS 24.0 pair.
# Missing/ambiguous assets or an unrecognized source must never mean "skip".
set -euo pipefail

[[ "${ROM_FAMILY:-}" == lineageos && "${DEVICE_NAME:-}" == pdx235 &&
   "${OUTPUT_SCOPE:-}" == published &&
   "${ROOT_MODE:-}" == both && -n "${GITHUB_OUTPUT:-}" &&
   "${GITHUB_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
  echo 'Error: scheduled existing-release preflight received unexpected scope.' >&2
  exit 1
}

source src/util_functions.sh
resolve_rom_profile
enforce_output_policy "${OUTPUT_SCOPE}"
activate_development_profile
get_latest_version
resolve_root_mode
generate_ota_info

# The source is selected by the official LineageOS builds API. The source
# byte digest MUST also match metadata recorded with both existing outputs.
enforce_pdx235_supported_source
[[ "${ROM_OTA_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || {
  echo 'Error: source release lacks the official SHA-256.' >&2
  exit 1
}

export REPOSITORY="${GITHUB_REPOSITORY}"
export GRAPHENEOS_VERSION="${VERSION[GRAPHENEOS]}"
export MAGISK_VERSION="${VERSION[MAGISK]}"
export SOURCE_OTA_SHA256="${ROM_OTA_SHA256}"
export SOURCE_OTA_TARGET="${GRAPHENEOS[OTA_TARGET]}"
export BUILD_COMMIT
BUILD_COMMIT="$(git rev-parse --verify HEAD)"
export EXPECTED_VARIANT_ROOTLESS="${MODULE_SELECTION_FINGERPRINT_ROOTLESS}"
export EXPECTED_VARIANT_MAGISK="${MODULE_SELECTION_FINGERPRINT_MAGISK}"

bash src/ci/check_existing_pair.sh
