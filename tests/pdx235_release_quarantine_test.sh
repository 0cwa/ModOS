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

# Private/shared output scopes remain unsupported for pdx235. Published scope
# is restricted to the supported LineageOS nightly; source identity is checked
# after selection, before signing.
expect_reject 'pdx235 publication requires channel identity' \
  enforce_pdx235_publication_scope lineageos pdx235 published
for scope in private shared; do
  expect_reject "pdx235 ${scope}" \
    enforce_pdx235_publication_scope lineageos pdx235 "${scope}"
done
expect_allow 'pdx235 build-only workspace' \
  enforce_pdx235_publication_scope lineageos pdx235 local-unpublished
expect_allow 'publisher accepts supported source scope' \
  enforce_pdx235_publication_scope lineageos pdx235 published nightly 20261019
expect_allow 'GrapheneOS unaffected' \
  enforce_pdx235_publication_scope grapheneos shiba published
expect_allow 'other LineageOS device unaffected' \
  enforce_pdx235_publication_scope lineageos other_device published
expect_reject 'missing context is not allowed' enforce_pdx235_publication_scope lineageos

ROM_FAMILY=lineageos
DEVICE_NAME=pdx235
ADDITIONALS[DEBUG]=false
ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]=false
resolve_rom_profile

# Cover a normal rootless request and an adversarial paired/microG forced
# request. The gate depends only on ROM/device/scope; a Cartesian product of
# unrelated module and root settings repeats the same policy invocation.
for scenario in normal forced-pair; do
  if [[ "${scenario}" == normal ]]; then
    ROOT_MODE=rootless
    ADDITIONALS[MICROG]=false
    RELEASE_TYPE=default
  else
    ROOT_MODE=both
    ADDITIONALS[MICROG]=true
    RELEASE_TYPE=force-publish
  fi
  FORCE_UPDATE=true
  PUBLISH=true
  PIXENEOS_PDX235_CARE_MAP_SHA256=b2b4269738df1d3092de49bde6c7d0ffcb62ae19b342213a819187c30b7ab1f8
  export ROOT_MODE RELEASE_TYPE FORCE_UPDATE PUBLISH PIXENEOS_PDX235_CARE_MAP_SHA256
  expect_allow "${scenario} local-unpublished" enforce_output_policy local-unpublished
  expect_allow "${scenario} early publication scope" enforce_output_policy published
  expect_allow "${scenario} publication evidence" enforce_publication_evidence published
done

# The exfat module adds one additional fail-closed condition: a verified
# corresponding-source bundle must exist before a published selection passes.
ADDITIONALS[EXFATPROGS_FSCK]=true
provided_source_bundle_path="${EXFAT_SOURCE_BUNDLE_PATH:-}"
provided_source_bundle_sha256="${EXFAT_SOURCE_BUNDLE_SHA256:-}"
provided_source_bundle_verified="${EXFAT_SOURCE_BUNDLE_VERIFIED:-}"
unset EXFAT_SOURCE_BUNDLE_PATH EXFAT_SOURCE_BUNDLE_SHA256 EXFAT_SOURCE_BUNDLE_VERIFIED
source_bundle_inputs_satisfy_requirement() {
  local path="${1:-}" digest="${2:-}" verified="${3:-}" required="${4:-false}"
  if [[ -n "${path}" && -f "${path}" && ! -L "${path}" &&
    "${digest}" =~ ^[0-9a-f]{64}$ && "${verified}" == true ]]; then
    return 0
  fi
  [[ "${required}" != true ]]
}
if source_bundle_inputs_satisfy_requirement '' '' '' true; then
  fail 'required bundle inputs were not rejected when missing'
fi
if ! source_bundle_inputs_satisfy_requirement '' '' '' false; then
  fail 'optional bundle inputs were not allowed to be absent'
fi
if source_bundle_inputs_satisfy_requirement "$0" "$(printf '%065d' 0)" true true; then
  fail 'overlong source-bundle digest passed the anchored requirement check'
fi
expect_allow 'exfat remains available for local-unpublished output' enforce_output_policy local-unpublished
expect_reject 'exfat publication requires a verified source bundle' enforce_output_policy published
if ! source_bundle_inputs_satisfy_requirement \
  "${provided_source_bundle_path}" "${provided_source_bundle_sha256}" \
  "${provided_source_bundle_verified}" "${REQUIRE_EXFAT_SOURCE_BUNDLE:-false}"; then
  fail 'required deterministic source bundle is missing or invalid'
fi
if [[ -n "${provided_source_bundle_path}" && -f "${provided_source_bundle_path}" &&
  ! -L "${provided_source_bundle_path}" &&
  "${provided_source_bundle_sha256}" =~ ^[0-9a-f]{64}$ &&
  "${provided_source_bundle_verified}" == true ]]; then
  source_bundle="${tmp}/$(basename -- "${provided_source_bundle_path}")"
  cp -- "${provided_source_bundle_path}" "${source_bundle}"
  EXFAT_SOURCE_BUNDLE_PATH="${source_bundle}"
  EXFAT_SOURCE_BUNDLE_SHA256="${provided_source_bundle_sha256}"
  EXFAT_SOURCE_BUNDLE_VERIFIED=true
  export EXFAT_SOURCE_BUNDLE_PATH EXFAT_SOURCE_BUNDLE_SHA256 EXFAT_SOURCE_BUNDLE_VERIFIED
  expect_allow 'verified source bundle permits exfat publication' enforce_output_policy published
  printf 'tampered' >>"${source_bundle}"
  expect_reject 'changed source bundle blocks exfat publication' enforce_output_policy published
  unset EXFAT_SOURCE_BUNDLE_PATH EXFAT_SOURCE_BUNDLE_SHA256 EXFAT_SOURCE_BUNDLE_VERIFIED
else
  echo 'SKIP: source-bundle acceptance/tamper cases (no verified bundle supplied)'
fi
ADDITIONALS[EXFATPROGS_FSCK]=false

# Both release helpers still fail closed on malformed direct invocation before
# contacting GH; valid workflow calls receive published scope only after the
# main pipeline has checked source identity and rebuilt/postverified outputs.
expect_reject 'direct single-flavor publisher' env \
  ROM_FAMILY=lineageos DEVICE_NAME=pdx235 OUTPUT_SCOPE=published \
  RELEASE_TYPE=force-publish bash src/ci/publish_ota.sh

expect_reject 'direct paired-output publisher' env \
  ROM_FAMILY=lineageos ROM_PROFILE_PROVIDER=lineageos \
  DEVICE_NAME=pdx235 GRAPHENEOS_VERSION=20261019 \
  OUTPUT_SCOPE=published RELEASE_TYPE=default \
  bash src/ci/publish_ota_pair.sh

# Published source identity must match the official API target and digest.
ROM_FAMILY=lineageos DEVICE_NAME=pdx235 OUTPUT_SCOPE=published
GRAPHENEOS[UPDATE_CHANNEL]=nightly
GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261012-nightly-pdx235-signed
ROM_OTA_SHA256=29cb72bbdf808807d27f834a889df7a96ad85cdeed42b45dfb6748cdf05c3820
VERSION[GRAPHENEOS]=20261012
expect_allow 'supported source identity without workflow override' enforce_pdx235_supported_source
expect_allow 'unquarantined published scope' enforce_pdx235_publication_scope lineageos pdx235 published
expect_allow 'supported rootless publication policy' enforce_output_policy published
expect_allow 'supported paired publication policy' enforce_publication_evidence published
for case_name in wrong-digest wrong-version wrong-build wrong-channel wrong-family wrong-device; do
  (
    case "${case_name}" in
      wrong-digest) ROM_OTA_SHA256='not-a-sha256' ;;
      wrong-version) VERSION[GRAPHENEOS]=20261013 ;;
      wrong-build) GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261015-nightly-pdx235-signed ;;
      wrong-channel) GRAPHENEOS[UPDATE_CHANNEL]=weekly ;;
      wrong-family) ROM_FAMILY=grapheneos ;;
      wrong-device) DEVICE_NAME=other ;;
    esac
    if enforce_pdx235_supported_source >/dev/null 2>&1; then
      fail "supported source check accepted ${case_name}"
    fi
  )
  ((cases+=1))
done
GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261019-nightly-pdx235-signed
VERSION[GRAPHENEOS]=20261019
ROM_OTA_SHA256="$(printf 'a%.0s' {1..64})"
expect_allow 'later 24.0 nightly with official API digest' enforce_pdx235_supported_source
GRAPHENEOS[OTA_TARGET]=lineage-24.1-20261019-nightly-pdx235-signed
expect_reject '24.1 outside supported Android series' enforce_pdx235_supported_source
GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261011-nightly-pdx235-signed
VERSION[GRAPHENEOS]=20261011
expect_allow '24.0 source has no temporary date cutoff' enforce_pdx235_supported_source
VERSION[GRAPHENEOS]=''
ROM_OTA_SHA256=''
OUTPUT_SCOPE=local-unpublished

# Reusing a locally present pdx235 ZIP must never bypass postflight checks.
# These are only synthetic local files; no OTA is created or verified here.
rootless="${tmp}/pdx235-rootless.zip"
magisk="${tmp}/pdx235-magisk.zip"
expect_allow 'fresh pdx235 rootless' \
  reject_pdx235_preexisting_outputs lineageos pdx235 rootless "${rootless}" ''
expect_allow 'fresh pdx235 pair' \
  reject_pdx235_preexisting_outputs lineageos pdx235 both "${rootless}" "${magisk}"
expect_reject 'missing primary output path' \
  reject_pdx235_preexisting_outputs lineageos pdx235 rootless '' ''
expect_reject 'identical paired paths' \
  reject_pdx235_preexisting_outputs lineageos pdx235 both "${rootless}" "${rootless}"
expect_reject 'missing Magisk paired output path' \
  reject_pdx235_preexisting_outputs lineageos pdx235 both "${rootless}" ''
for suffix in '' '.csig' '.selection.json' '.patch-report.json' '.microg-patch-report.json'; do
  touch -- "${rootless}${suffix}"
  expect_reject "rootless stale ${suffix:-OTA}" \
    reject_pdx235_preexisting_outputs lineageos pdx235 rootless "${rootless}" ''
  rm -- "${rootless}${suffix}"
  touch -- "${magisk}${suffix}"
  expect_reject "secondary stale ${suffix:-OTA}" \
    reject_pdx235_preexisting_outputs lineageos pdx235 both "${rootless}" "${magisk}"
  rm -- "${magisk}${suffix}"
done
ln -s "${tmp}/missing-OTA" "${rootless}"
expect_reject 'dangling symlink output' \
  reject_pdx235_preexisting_outputs lineageos pdx235 rootless "${rootless}" ''
rm -- "${rootless}"
expect_allow 'unaffected GrapheneOS cached output' \
  reject_pdx235_preexisting_outputs grapheneos shiba both "${rootless}" "${magisk}"

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

echo "PASS: ${cases} pdx235 publication-policy regression cases (no OTA build or publication)"
