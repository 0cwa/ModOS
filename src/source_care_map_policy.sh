#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# The October 9 archive's SHA-256 and official LineageOS publisher signature
# were independently verified in read-only forensic run 38180526270.
# Runtime care-map fingerprints and regenerated-output safety remain unproven.
# Keep this production exception pin EMPTY until those safety gates pass.
# Never fill it from a workflow input or environment variable.
readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256=''

# Official LineageOS ZIP signing public key from the independently maintained
# LineageOS/update_verifier repository (commit 9ffcf56a0fe152467da2971f0e6b2b79a42f7890):
# https://github.com/LineageOS/update_verifier/blob/9ffcf56a0fe152467da2971f0e6b2b79a42f7890/lineageos_pubkey
# SHA-256 of the RSA public key encoded as X.509 SubjectPublicKeyInfo DER.
# This is not an AVB key: LineageOS legitimately uses public AOSP AVB test keys.
readonly _LINEAGEOS_OFFICIAL_OTA_PUBKEY_SPKI_SHA256='0848ca38cb604887cea216a7f39d7dd62bad20b513cfdeff357ece4e731d6372'

# Pure identity predicate, also exercised with synthetic fixture digests.
# An accepted identity only grants permission to request the helper's narrow
# exception; it does not prove the source publisher or fingerprint provenance.
source_care_map_known_exception() {
  [[ "$#" -eq 6 ]] || return 1
  local family="$1" device="$2" target="$3" source_zip="$4"
  local avbroot_version="$5" reviewed_sha256="$6" digest_line actual_sha256

  [[ "$family" == 'lineageos' && "$device" == 'pdx235' ]] || return 1
  [[ "$target" == 'lineage-23.2-20261009-nightly-pdx235-signed' ]] || return 1
  [[ "$avbroot_version" == 'avbroot 3.34.1' ]] || return 1
  [[ "$reviewed_sha256" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ -f "$source_zip" && ! -L "$source_zip" ]] || return 1

  digest_line="$(sha256sum -- "$source_zip")" || return 1
  actual_sha256="${digest_line%% *}"
  [[ "$actual_sha256" == "$reviewed_sha256" ]]
}

# The LineageOS OTA's META-INF/com/android/otacert is a PEM certificate,
# extracted unchanged by unzip in extract_official_keys(). Check its public
# key against the independent publisher pin. This does NOT make an embedded
# certificate a trust anchor; avbroot also verifies the actual OTA signature.
source_care_map_certificate_has_public_key_digest() (
  [[ "$#" -eq 2 ]] || return 1
  local cert="$1" expected="$2" actual
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ -s "$cert" && -f "$cert" && ! -L "$cert" ]] || return 1
  set -o pipefail
  actual="$(openssl x509 -inform PEM -in "$cert" -pubkey -noout 2>/dev/null |
    openssl pkey -pubin -outform DER 2>/dev/null |
    sha256sum)" || return 1
  [[ "${actual%% *}" == "$expected" ]]
)

# Independent source-publisher check for every LineageOS/pdx235 patch path,
# not just the dormant October 9 exception. The ZIP member must be unique and
# byte-for-byte equal to the extracted certificate handed to the helper. A
# stale extracted certificate from a different downloaded OTA must not pass.
# The helper must STILL verify the whole-file OTA signature against this cert.
source_care_map_source_publisher_matches_pin() {
  [[ "$#" -eq 3 ]] || return 1
  local source_zip="$1" extracted_cert="$2" expected_pubkey="$3"
  local policy_dir

  source_care_map_certificate_has_public_key_digest \
    "${extracted_cert}" "${expected_pubkey}" || return 1
  policy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return 1
  python3 "${policy_dir}/ci/check_ota_certificate_member.py" \
    "${source_zip}" "${extracted_cert}"
}

# Scope strictly to pdx235; other ROMs/devices retain their existing policy.
source_care_map_require_lineageos_pdx235_publisher() {
  [[ "$#" -eq 4 ]] || return 1
  local family="$1" device="$2" source_zip="$3" extracted_cert="$4"
  [[ "${family}" == 'lineageos' && "${device}" == 'pdx235' ]] || return 0

  if ! source_care_map_source_publisher_matches_pin \
    "${source_zip}" "${extracted_cert}" \
    "${_LINEAGEOS_OFFICIAL_OTA_PUBKEY_SPKI_SHA256}"; then
    echo "Error: independently pinned LineageOS/pdx235 publisher certificate does not match the current source OTA." >&2
    return 1
  fi
}

# Production gate: strict by default. A future exception requires both the
# exact independently reviewed ZIP identity and the trusted LineageOS OTA
# signing identity before allowing avbroot's single care-map-only exception.
source_care_map_exception_allowed() {
  [[ "$#" -eq 6 ]] || return 1
  [[ -n "$_SOURCE_CARE_MAP_PDX235_20261009_SHA256" ]] || return 1
  source_care_map_known_exception "$1" "$2" "$3" "$4" "$5" \
    "$_SOURCE_CARE_MAP_PDX235_20261009_SHA256" || return 1
  source_care_map_certificate_has_public_key_digest "$6" \
    "$_LINEAGEOS_OFFICIAL_OTA_PUBKEY_SPKI_SHA256"
}
