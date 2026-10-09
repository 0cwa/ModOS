#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

set -euo pipefail

[[ -f src/fetcher.sh ]] || {
  echo "missing OTA fetcher: src/fetcher.sh" >&2
  exit 1
}
source src/fetcher.sh

fail() {
  echo "$*" >&2
  exit 1
}

assert_equals() {
  local expected="${1}"
  local actual="${2}"
  local context="${3}"

  [[ "${actual}" == "${expected}" ]] ||
    fail "${context}: expected ${expected}, got ${actual}"
}

mock_magisk_tags() {
  if [[ "${1:-}" == "ls-remote" ]]; then
    printf '1111111111111111111111111111111111111111\trefs/tags/v28.1\n'
    printf '2222222222222222222222222222222222222222\trefs/tags/v29.0\n'
    return 0
  fi
  fail "unexpected git invocation: $*"
}

reset_acquisition_fixture() {
  unset GRAPHENEOS_VERSION GRAPHENEOS_UPDATE_CHANNEL ROM_UPDATE_TYPE
  VERSION[GRAPHENEOS]=""
  VERSION[MAGISK]=""
  GRAPHENEOS[OTA_URL]=""
  GRAPHENEOS[OTA_TARGET]=""
  ROM_OTA_SHA256=""
  ADDITIONALS_MAS_COMPATIBLE_SEPOLICY=""
}

test_pinned_magisk_version_skips_tag_discovery() (
  reset_acquisition_fixture
  ROM_FAMILY="grapheneos"
  DEVICE_NAME="shiba"
  VERSION[MAGISK]="v30.7"

  git() { fail "pinned Magisk version unexpectedly queried git tags"; }
  curl() {
    local url="${!#}"
    [[ "${url}" == "https://releases.grapheneos.org/shiba-stable" ]] ||
      fail "unexpected GrapheneOS metadata URL: ${url}"
    printf '%s\n' '2026071700 1784260800'
  }

  get_latest_version >/dev/null
  assert_equals "v30.7" "${VERSION[MAGISK]}" "pinned Magisk version"
)

test_grapheneos_text_metadata() (
  reset_acquisition_fixture
  ROM_FAMILY="grapheneos"
  DEVICE_NAME="shiba"

  git() { mock_magisk_tags "$@"; }
  curl() {
    local url="${!#}"
    [[ "${url}" == "https://releases.grapheneos.org/shiba-stable" ]] ||
      fail "unexpected GrapheneOS metadata URL: ${url}"
    printf '%s\n' '2026071700 1784260800'
  }

  get_latest_version >/dev/null

  assert_equals "2026071700" "${VERSION[GRAPHENEOS]}" \
    "GrapheneOS version"
  assert_equals "shiba-ota_update-2026071700" \
    "${GRAPHENEOS[OTA_TARGET]}" "GrapheneOS OTA target"
  assert_equals \
    "https://releases.grapheneos.org/shiba-ota_update-2026071700.zip" \
    "${GRAPHENEOS[OTA_URL]}" "GrapheneOS OTA URL"
  assert_equals "v29.0" "${VERSION[MAGISK]}" "Magisk version"
  assert_equals "" "${ROM_OTA_SHA256}" "GrapheneOS digest availability"
)

test_lineageos_v2_metadata() (
  reset_acquisition_fixture
  ROM_FAMILY="lineageos"
  DEVICE_NAME="pdx235"

  git() { mock_magisk_tags "$@"; }
  curl() {
    local url="${!#}"
    [[ "${url}" == \
      "https://download.lineageos.org/api/v2/devices/pdx235/builds" ]] ||
      fail "unexpected LineageOS metadata URL: ${url}"
    printf '%s\n' '[
      {
        "date": "2026-07-16",
        "datetime": 1784185320,
        "files": [
          {
            "filename": "lineage-23.2-20260716-nightly-pdx235-signed.zip",
            "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "type": "nightly",
            "url": "https://mirrorbits.lineageos.org/full/pdx235/20260716/lineage-23.2-20260716-nightly-pdx235-signed.zip"
          }
        ],
        "type": "nightly",
        "version": "23.2"
      },
      {
        "date": "2026-07-17",
        "datetime": 1784271720,
        "files": [
          {
            "filename": "lineage-23.2-20260717-nightly-pdx235-signed.zip",
            "sha256": "df27d06052a79f0acc24e8862b70a0c32f188e4a6f107964c93bdb54ade7accc",
            "type": "nightly",
            "url": "https://mirrorbits.lineageos.org/full/pdx235/20260717/lineage-23.2-20260717-nightly-pdx235-signed.zip"
          },
          {
            "filename": "boot.img",
            "url": "https://mirrorbits.lineageos.org/full/pdx235/20260717/boot.img"
          }
        ],
        "type": "nightly",
        "version": "23.2"
      }
    ]'
  }

  get_latest_version >/dev/null

  assert_equals "20260717" "${VERSION[GRAPHENEOS]}" "LineageOS version"
  assert_equals "lineage-23.2-20260717-nightly-pdx235-signed" \
    "${GRAPHENEOS[OTA_TARGET]}" "LineageOS OTA target"
  assert_equals \
    "https://mirrorbits.lineageos.org/full/pdx235/20260717/lineage-23.2-20260717-nightly-pdx235-signed.zip" \
    "${GRAPHENEOS[OTA_URL]}" "LineageOS OTA URL"
  assert_equals "v29.0" "${VERSION[MAGISK]}" "Magisk version"
  assert_equals \
    "df27d06052a79f0acc24e8862b70a0c32f188e4a6f107964c93bdb54ade7accc" \
    "${ROM_OTA_SHA256}" "LineageOS OTA SHA-256"
)

test_lineageos_digest_verification() (
  local ota_path actual

  reset_acquisition_fixture
  ROM_FAMILY="lineageos"
  DEVICE_NAME="pdx235"
  resolve_rom_profile
  ota_path="$(mktemp)"
  trap 'rm -f -- "${ota_path}"' EXIT
  printf '%s' 'fixture OTA bytes' >"${ota_path}"
  actual="$(sha256sum -- "${ota_path}" | awk '{print $1}')"
  ROM_OTA_SHA256="${actual}"
  verify_rom_ota_digest "${ota_path}" ||
    fail "matching LineageOS OTA digest was rejected"

  ROM_OTA_SHA256="$(printf '0%.0s' {1..64})"
  if verify_rom_ota_digest "${ota_path}" >/dev/null 2>&1; then
    fail "mismatched LineageOS OTA digest unexpectedly succeeded"
  fi
)

test_invalid_grapheneos_metadata_fails_closed() (
  reset_acquisition_fixture
  ROM_FAMILY="grapheneos"
  DEVICE_NAME="shiba"

  git() { mock_magisk_tags "$@"; }
  curl() { printf '%s\n' '<html>not release metadata</html>'; }

  if get_latest_version >/dev/null 2>&1; then
    fail "invalid GrapheneOS metadata unexpectedly succeeded"
  fi
)

test_unsafe_grapheneos_device_fails_closed() (
  reset_acquisition_fixture
  ROM_FAMILY="grapheneos"
  DEVICE_NAME="../shiba"

  git() { mock_magisk_tags "$@"; }
  curl() { fail "unsafe GrapheneOS device reached the network"; }

  if get_latest_version >/dev/null 2>&1; then
    fail "unsafe GrapheneOS device unexpectedly succeeded"
  fi
)

test_unsafe_lineageos_metadata_fails_closed() (
  reset_acquisition_fixture
  ROM_FAMILY="lineageos"
  DEVICE_NAME="pdx235"

  git() { mock_magisk_tags "$@"; }
  curl() {
    printf '%s\n' '[{
      "date": "2026-07-17",
      "files": [{
        "filename": "lineage-23.2-20260717-nightly-pdx235-signed.zip",
        "url": "http://mirror.example/lineage-23.2-20260717-nightly-pdx235-signed.zip"
      }],
      "type": "nightly",
      "version": "23.2"
    }]'
  }

  if get_latest_version >/dev/null 2>&1; then
    fail "LineageOS metadata with a non-HTTPS OTA URL unexpectedly succeeded"
  fi
)

test_pinned_magisk_version_skips_tag_discovery
test_grapheneos_text_metadata
test_lineageos_v2_metadata
test_lineageos_digest_verification
test_invalid_grapheneos_metadata_fails_closed
test_unsafe_grapheneos_device_fails_closed
test_unsafe_lineageos_metadata_fails_closed

echo "Provider acquisition tests passed"
