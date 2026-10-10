#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# No publisher-authenticated SHA-256 of the original October 9 source OTA has
# been reviewed. Leave this pin empty and use strict verification by default.
# Never fill this from a workflow input or environment variable. A release
# digest does not replace independent verification of the source publisher.
readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256=''

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

# Production gate: only the checked-in reviewed pin can enable compatibility.
# For now it is intentionally unconfigured, so EVERY source is verified strictly.
source_care_map_exception_allowed() {
  [[ "$#" -eq 5 ]] || return 1
  [[ -n "$_SOURCE_CARE_MAP_PDX235_20261009_SHA256" ]] || return 1
  source_care_map_known_exception "$@" "$_SOURCE_CARE_MAP_PDX235_20261009_SHA256"
}
