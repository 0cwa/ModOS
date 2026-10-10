#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# No publisher-authenticated SHA-256 of the original October 9 source OTA has
# been reviewed. Leave this pin empty and use strict verification by default.
# Never fill this from a workflow input or environment variable. A release
# digest does not replace independent verification of the source publisher.
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

# Verify the public key in a DER-encoded OTA X.509 certificate against an
# independently pinned signing key. The embedded certificate is not by itself
# a trust anchor; avbroot still verifies the actual OTA's whole-file signature.
source_care_map_certificate_has_public_key_digest() (
  [[ "$#" -eq 2 ]] || return 1
  local cert="$1" expected="$2" actual
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ -s "$cert" && -f "$cert" && ! -L "$cert" ]] || return 1
  set -o pipefail
  actual="$(openssl x509 -inform DER -in "$cert" -pubkey -noout 2>/dev/null |
    openssl pkey -pubin -outform DER 2>/dev/null |
    sha256sum)" || return 1
  [[ "${actual%% *}" == "$expected" ]]
)

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
