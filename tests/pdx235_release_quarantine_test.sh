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
  expect_quarantine_failure "${scenario} early" enforce_output_policy published
  expect_quarantine_failure "${scenario} late" enforce_publication_evidence published
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

# Scheduled Android 17 series: the preliminary gate is allowed only under
# the trusted workflow flag; actual official API data and final signatures
# must be verified before any signing or publishing.
PIXENEOS_PDX235_PILOT_RELEASE=true
ROM_FAMILY=lineageos DEVICE_NAME=pdx235 OUTPUT_SCOPE=published
GRAPHENEOS[UPDATE_CHANNEL]=nightly
GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261012-nightly-pdx235-signed
ROM_OTA_SHA256=29cb72bbdf808807d27f834a889df7a96ad85cdeed42b45dfb6748cdf05c3820
VERSION[GRAPHENEOS]=20261012
expect_allow 'exact pilot source identity' enforce_pdx235_pilot_source
expect_allow 'pilot published scope only' enforce_output_policy published
expect_allow 'pilot paired publication scope' enforce_pdx235_care_map_quarantine lineageos pdx235 published
expect_reject 'pilot cannot become private/shared' enforce_pdx235_care_map_quarantine lineageos pdx235 shared
for case_name in wrong-digest wrong-date wrong-build wrong-channel wrong-family wrong-device; do
  (
    case "${case_name}" in
      wrong-digest) ROM_OTA_SHA256='not-a-sha256' ;;
      wrong-date) VERSION[GRAPHENEOS]=20261013 ;;
      wrong-build) GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261015-nightly-pdx235-signed ;;
      wrong-channel) GRAPHENEOS[UPDATE_CHANNEL]=weekly ;;
      wrong-family) ROM_FAMILY=grapheneos ;;
      wrong-device) DEVICE_NAME=other ;;
    esac
    if enforce_pdx235_pilot_source >/dev/null 2>&1; then
      fail "pilot accepted ${case_name}"
    fi
  )
  ((cases+=1))
done
GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261019-nightly-pdx235-signed
VERSION[GRAPHENEOS]=20261019
ROM_OTA_SHA256="$(printf 'a%.0s' {1..64})"
expect_allow 'later 24.0 nightly with official API digest' enforce_pdx235_pilot_source
GRAPHENEOS[OTA_TARGET]=lineage-24.1-20261019-nightly-pdx235-signed
expect_reject '24.1 outside qualified Android series' enforce_pdx235_pilot_source
GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261011-nightly-pdx235-signed
VERSION[GRAPHENEOS]=20261011
expect_reject 'prequalification source cannot publish' enforce_pdx235_pilot_source
GRAPHENEOS[OTA_TARGET]=lineage-24.0-20261012-nightly-pdx235-signed
VERSION[GRAPHENEOS]=20261012
ROM_OTA_SHA256=29cb72bbdf808807d27f834a889df7a96ad85cdeed42b45dfb6748cdf05c3820
unset PIXENEOS_PDX235_PILOT_RELEASE
expect_quarantine_failure 'no pilot flag still quarantined' enforce_pdx235_care_map_quarantine lineageos pdx235 published
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

echo "PASS: ${cases} pdx235 publication-quarantine regression cases (no OTA build or publication)"
