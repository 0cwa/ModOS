#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors
set -euo pipefail

source src/source_care_map_policy.sh

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
printf 'synthetic reviewed source OTA bytes\n' >"$tmp/source.zip"
printf 'different future source OTA bytes\n' >"$tmp/future.zip"
sha="$(sha256sum -- "$tmp/source.zip")"
sha="${sha%% *}"
args=(lineageos pdx235 lineage-23.2-20261009-nightly-pdx235-signed "$tmp/source.zip" 'avbroot 3.34.1' "$sha")
cases=0

expect_allow() {
  ((cases+=1))
  source_care_map_known_exception "$@" || {
    echo "FAIL: expected exact identity acceptance" >&2
    exit 1
  }
}
expect_reject() {
  ((cases+=1))
  if source_care_map_known_exception "$@"; then
    echo "FAIL: unexpectedly accepted a nonallowlisted source" >&2
    exit 1
  fi
}
expect_production_reject() {
  ((cases+=1))
  if source_care_map_exception_allowed "$@"; then
    echo "FAIL: production must not enable care-map exception without provenance" >&2
    exit 1
  fi
}

# This exercises the *pure* identity predicate only: no real ZIP is authorized.
expect_allow "${args[@]}"
expect_reject lineageos pdx235 lineage-23.2-20261016-nightly-pdx235-signed "$tmp/future.zip" 'avbroot 3.34.1' "$sha"
expect_reject lineageos pdx235 "${args[2]}" "$tmp/future.zip" "${args[4]}" "$sha"
expect_reject lineageos other_device "${args[2]}" "${args[3]}" "${args[4]}" "$sha"
expect_reject grapheneos pdx235 "${args[2]}" "${args[3]}" "${args[4]}" "$sha"
expect_reject lineageos pdx235 "${args[2]}" "${args[3]}" 'avbroot 3.34.2' "$sha"
expect_reject lineageos pdx235 "${args[2]}" "${args[3]}" 'avbroot 3.33.0' "$sha"
expect_reject lineageos pdx235 "${args[2]}" "${args[3]}" "${args[4]}" ''
expect_reject lineageos pdx235 "${args[2]}" "${args[3]}" "${args[4]}" 'not-a-digest'
expect_reject lineageos pdx235 "${args[2]}" "$tmp/missing.zip" "${args[4]}" "$sha"
ln -s "$tmp/source.zip" "$tmp/link.zip"
expect_reject lineageos pdx235 "${args[2]}" "$tmp/link.zip" "${args[4]}" "$sha"

# No workflow/environment variable may become an unreviewed production pin.
export PIXENEOS_PDX235_CARE_MAP_SHA256="$sha"
expect_production_reject "${args[@]:0:5}"
expect_production_reject lineageos pdx235 lineage-23.2-20261016-nightly-pdx235-signed "$tmp/future.zip" 'avbroot 3.34.1'

# Mutation after prior SHA calculation must be rejected by pure predicate.
printf 'modified after digest\n' >>"$tmp/source.zip"
expect_reject "${args[@]}"
expect_production_reject "${args[@]:0:5}"

echo "PASS: $cases source care-map policy cases (1 pure allow, production fail-closed)"
