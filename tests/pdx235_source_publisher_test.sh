#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors
set -euo pipefail

# Synthetic archive/certificate fixtures, NOT a claim of an officially signed
# OTA. Full source whole-file signature checks remain owned by the helper.
source src/source_care_map_policy.sh

tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT

openssl req -x509 -nodes -newkey rsa:2048 -keyout "${tmp}/key.pem" \
  -out "${tmp}/good.pem" -subj '/CN=synthetic-fixture-publisher' \
  -days 1 >/dev/null 2>&1
openssl req -x509 -nodes -newkey rsa:2048 -keyout "${tmp}/other.key" \
  -out "${tmp}/other.pem" -subj '/CN=synthetic-other-publisher' \
  -days 1 >/dev/null 2>&1

digest="$(openssl x509 -inform PEM -in "${tmp}/good.pem" -pubkey -noout |
  openssl pkey -pubin -outform DER | sha256sum)"
digest="${digest%% *}"

python3 - "${tmp}" <<'PY'
import os
import pathlib
import stat
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
good = (root / "good.pem").read_bytes()
other = (root / "other.pem").read_bytes()
member = "META-INF/com/android/otacert"

with zipfile.ZipFile(root / "good.zip", "w") as archive:
    archive.writestr(member, good)
with zipfile.ZipFile(root / "other.zip", "w") as archive:
    archive.writestr(member, other)
with zipfile.ZipFile(root / "missing.zip", "w") as archive:
    archive.writestr("META-INF/com/android/README", b"no certificate")
with zipfile.ZipFile(root / "duplicate.zip", "w") as archive:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        archive.writestr(member, good)
        archive.writestr(member, good)
with zipfile.ZipFile(root / "symlink-member.zip", "w") as archive:
    info = zipfile.ZipInfo(member)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive.writestr(info, good)
with zipfile.ZipFile(root / "oversized.zip", "w") as archive:
    archive.writestr(member, b"X" * 65537)
with zipfile.ZipFile(root / "multi-pem.zip", "w") as archive:
    archive.writestr(member, good + good)
with zipfile.ZipFile(root / "bad-zip.zip", "w") as archive:
    archive.writestr(member, good)
with open(root / "bad-zip.zip", "r+b") as stream:
    stream.truncate(8)
PY
ln -s "${tmp}/good.zip" "${tmp}/linked.zip"
ln -s "${tmp}/good.pem" "${tmp}/linked.pem"

cases=0
expect_allow() {
  local name="$1"
  shift
  ((cases+=1))
  if ! "$@" >"${tmp}/result" 2>&1; then
    echo "FAIL ${name}: expected acceptance: $(cat "${tmp}/result")" >&2
    exit 1
  fi
}
expect_reject() {
  local name="$1"
  shift
  ((cases+=1))
  if "$@" >"${tmp}/result" 2>&1; then
    echo "FAIL ${name}: unexpected acceptance" >&2
    exit 1
  fi
}

expect_allow 'exact certificate and independently supplied synthetic key' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/good.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'wrong independent signing public key' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/good.zip" "${tmp}/good.pem" "$(printf '%064d' 0)"
expect_reject 'stale extracted certificate after downloaded OTA switches' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/other.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'missing archive certificate' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/missing.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'duplicate archive certificates' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/duplicate.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'symlink archive ZIP' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/linked.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'symlink extracted certificate' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/good.zip" "${tmp}/linked.pem" "${digest}"
expect_reject 'symlink ZIP member' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/symlink-member.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'overlarge archive certificate member' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/oversized.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'multiple concatenated certificates' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/multi-pem.zip" "${tmp}/good.pem" "${digest}"
expect_reject 'broken ZIP central directory' \
  source_care_map_source_publisher_matches_pin \
    "${tmp}/bad-zip.zip" "${tmp}/good.pem" "${digest}"

# The actual production wrapper MUST use the checked-in official LineageOS
# key. No synthetic cert can activate this scoped production trust decision.
expect_reject 'synthetic signer never satisfies production LineageOS pin' \
  source_care_map_require_lineageos_pdx235_publisher \
    lineageos pdx235 "${tmp}/good.zip" "${tmp}/good.pem"
expect_allow 'unrelated device unaffected' \
  source_care_map_require_lineageos_pdx235_publisher \
    lineageos other_device "${tmp}/missing.zip" "${tmp}/other.pem"
expect_allow 'unrelated ROM unaffected' \
  source_care_map_require_lineageos_pdx235_publisher \
    grapheneos pdx235 "${tmp}/missing.zip" "${tmp}/other.pem"

# Pin protection is unchanged; this certificate check cannot activate the
# separate care-map exception for the historical October 9 source digest.
[[ -z "${_SOURCE_CARE_MAP_PDX235_20261009_SHA256}" ]] ||
  { echo "FAIL: production source exception pin became active" >&2; exit 1; }

echo "PASS: ${cases} source publisher/archive identity regressions (synthetic fixtures only)"
