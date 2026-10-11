#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors
set -euo pipefail

# Hermetic release-policy tests only. No OTA downloads, signatures, GH API
# mutations, or publishing commands are performed by this harness.
source src/declarations.sh
source src/rom_profiles.sh

cases=0
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

expect_allow() {
  local label="$1"
  shift
  ((cases+=1))
  "$@" >"${tmp}/out" 2>&1 ||
    fail "${label}: expected acceptance; got $(cat "${tmp}/out")"
}

expect_reject() {
  local label="$1"
  shift
  ((cases+=1))
  if "$@" >"${tmp}/out" 2>&1; then
    fail "${label}: unexpectedly accepted"
  fi
}

expect_quarantine_failure() {
  local label="$1"
  shift
  expect_reject "${label}" "$@"
  grep -Fq 'ModOS#114' "${tmp}/out" ||
    fail "${label}: rejection was not from the care-map release quarantine"
}

# The shared gate rejects any nonlocal scope; it has no environment or
# workflow-input override and is not tied to only the October 9 ZIP.
for scope in published private shared; do
  expect_quarantine_failure "pdx235 ${scope}" \
    enforce_pdx235_care_map_quarantine lineageos pdx235 "${scope}"
done
expect_allow 'pdx235 build-only workspace' \
  enforce_pdx235_care_map_quarantine lineageos pdx235 local-unpublished
expect_allow 'GrapheneOS unaffected' \
  enforce_pdx235_care_map_quarantine grapheneos shiba published
expect_allow 'other LineageOS device unaffected' \
  enforce_pdx235_care_map_quarantine lineageos other_device published
expect_reject 'missing context is not allowed' enforce_pdx235_care_map_quarantine lineageos

ROM_FAMILY=lineageos
DEVICE_NAME=pdx235
ADDITIONALS[DEBUG]=false
ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]=false
resolve_rom_profile

# Test early policy enforcement and the later publication-evidence gate for
# both flavors, independently of microG and force-publish/forced rebuild.
for root_mode in rootless magisk both; do
  for microg in false true; do
    for release_type in default force-publish; do
      ROOT_MODE="${root_mode}"
      ADDITIONALS[MICROG]="${microg}"
      RELEASE_TYPE="${release_type}"
      FORCE_UPDATE=true
      PUBLISH=true
      PIXENEOS_PDX235_CARE_MAP_SHA256=b2b4269738df1d3092de49bde6c7d0ffcb62ae19b342213a819187c30b7ab1f8
      export ROOT_MODE RELEASE_TYPE FORCE_UPDATE PUBLISH PIXENEOS_PDX235_CARE_MAP_SHA256
      expect_allow "${root_mode}/microg=${microg} local-unpublished" \
        enforce_output_policy local-unpublished
      expect_quarantine_failure "${root_mode}/microg=${microg}/${release_type} early" \
        enforce_output_policy published
      expect_quarantine_failure "${root_mode}/microg=${microg}/${release_type} late" \
        enforce_publication_evidence published
    done
  done
done

# Both release helpers must independently refuse direct publication attempts,
# even without artifacts. They must reject for #114 *before* contacting GH.
expect_quarantine_failure 'direct single-flavor publisher' env \
  ROM_FAMILY=lineageos DEVICE_NAME=pdx235 OUTPUT_SCOPE=published \
  RELEASE_TYPE=force-publish bash src/ci/publish_ota.sh

expect_quarantine_failure 'direct paired-output publisher' env \
  ROM_FAMILY=lineageos ROM_PROFILE_PROVIDER=lineageos \
  DEVICE_NAME=pdx235 GRAPHENEOS_VERSION=20261009 \
  OUTPUT_SCOPE=published RELEASE_TYPE=default \
  bash src/ci/publish_ota_pair.sh

# Existing release support for non-pdx235 profiles must remain unchanged.
ROM_FAMILY=grapheneos
DEVICE_NAME=shiba
resolve_rom_profile
ADDITIONALS[MICROG]=false
expect_allow 'GrapheneOS publication policy unaffected' enforce_output_policy published
expect_allow 'GrapheneOS publication evidence unaffected' enforce_publication_evidence published

# The single-output build-only helper is a no-op, even if called directly.
expect_allow 'single build-only publication helper is a no-op' env \
  ROM_FAMILY=lineageos DEVICE_NAME=pdx235 \
  OUTPUT_SCOPE=local-unpublished RELEASE_TYPE=build-only \
  bash src/ci/publish_ota.sh

echo "PASS: ${cases} pdx235 publication-quarantine regression cases (no OTA build or publication)"
