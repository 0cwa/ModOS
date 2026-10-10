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

# Synthetic certificate tests demonstrate that a self-contained ZIP signer is
# NOT automatically authorized: only an independently pinned key can match.
openssl req -new -x509 -nodes -newkey rsa:2048 \
  -keyout "$tmp/signer.key" -out "$tmp/signer.der" -outform DER \
  -subj '/CN=synthetic-lineage-signing-fixture' -days 1 >/dev/null 2>&1
openssl req -new -x509 -nodes -newkey rsa:2048 \
  -keyout "$tmp/other.key" -out "$tmp/other.der" -outform DER \
  -subj '/CN=untrusted-signing-fixture' -days 1 >/dev/null 2>&1
cert_digest="$(openssl x509 -inform DER -in "$tmp/signer.der" -pubkey -noout |
  openssl pkey -pubin -outform DER | sha256sum)"
cert_digest="${cert_digest%% *}"

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
expect_production_reject "${args[@]:0:5}" "$tmp/signer.der"
expect_production_reject lineageos pdx235 lineage-23.2-20261016-nightly-pdx235-signed "$tmp/future.zip" 'avbroot 3.34.1' "$tmp/signer.der"

# Mutation after prior SHA calculation must be rejected by pure predicate.
printf 'modified after digest\n' >>"$tmp/source.zip"
expect_reject "${args[@]}"
expect_production_reject "${args[@]:0:5}" "$tmp/signer.der"

# A key copied out of a ZIP is NOT a publisher trust root. Explicit key
# identity matching must reject any other certificate or corrupt DER.
((cases+=1))
source_care_map_certificate_has_public_key_digest "$tmp/signer.der" "$cert_digest" ||
  { echo "FAIL: expected synthetic certificate identity to match" >&2; exit 1; }
((cases+=1))
if source_care_map_certificate_has_public_key_digest "$tmp/other.der" "$cert_digest"; then
  echo "FAIL: untrusted certificate matched different pinned identity" >&2
  exit 1
fi
((cases+=1))
if source_care_map_certificate_has_public_key_digest "$tmp/missing.der" "$cert_digest"; then
  echo "FAIL: missing cert accepted" >&2
  exit 1
fi
printf 'corrupt DER' >"$tmp/corrupt.der"
((cases+=1))
if source_care_map_certificate_has_public_key_digest "$tmp/corrupt.der" "$cert_digest"; then
  echo "FAIL: malformed cert accepted" >&2
  exit 1
fi
ln -s "$tmp/signer.der" "$tmp/cert-link.der"
((cases+=1))
if source_care_map_certificate_has_public_key_digest "$tmp/cert-link.der" "$cert_digest"; then
  echo "FAIL: symlinked cert accepted" >&2
  exit 1
fi
((cases+=1))
if source_care_map_certificate_has_public_key_digest "$tmp/signer.der" 'not-a-digest'; then
  echo "FAIL: malformed public key pin accepted" >&2
  exit 1
fi
((cases+=1))
if source_care_map_certificate_has_public_key_digest "$tmp/signer.der" \
  "$_LINEAGEOS_OFFICIAL_OTA_PUBKEY_SPKI_SHA256"; then
  echo "FAIL: synthetic signer masqueraded as the official LineageOS key" >&2
  exit 1
fi

echo "PASS: $cases source care-map policy cases (1 pure allow, production fail-closed)"
