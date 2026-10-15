#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

function fetch_grapheneos_ota_metadata() {
  local release_metadata latest_version

  release_metadata="$(curl -sLf \
    "${ROM_PROFILE[OTA_BASE_URL]}/${DEVICE_NAME}-${GRAPHENEOS[UPDATE_CHANNEL]}")" || {
    echo "Error: failed to fetch GrapheneOS release metadata." >&2
    return 1
  }
  latest_version="${release_metadata%%[[:space:]]*}"
  if [[ ! "${latest_version}" =~ ^[0-9]{8,14}$ ]]; then
    echo "Error: GrapheneOS returned an invalid release version." >&2
    return 1
  fi

  VERSION[GRAPHENEOS]="${latest_version}"
  ROM_OTA_SHA256=""
  GRAPHENEOS[OTA_TARGET]="${DEVICE_NAME}-${GRAPHENEOS[UPDATE_TYPE]}-${latest_version}"
  GRAPHENEOS[OTA_URL]="${ROM_PROFILE[OTA_BASE_URL]}/${GRAPHENEOS[OTA_TARGET]}.zip"
}

function fetch_lineageos_ota_metadata() {
  local release_metadata parsed filename ota_url latest_version source_filename="" source_series=""
  local endpoint="${ROM_PROFILE[OTA_BASE_URL]}/devices/${DEVICE_NAME}/builds"

  # Reproduce the physical candidate failure without upgrading Android as well.
  # This reviewed development profile remains restricted to unpublished pdx235.
  case "${PIXENEOS_DEVELOPMENT_PROFILE:-}" in
    care-map-pdx235)
      [[ "${DEVICE_NAME}" == pdx235 ]] || {
        echo 'Error: the frozen care-map source is restricted to pdx235.' >&2
        return 1
      }
      source_filename='lineage-23.2-20261009-nightly-pdx235-signed.zip'
      ;;
    care-map-pdx235-lineage24)
      [[ "${DEVICE_NAME}" == pdx235 &&
        "${GRAPHENEOS[UPDATE_CHANNEL]}" == nightly ]] || {
        echo 'Error: LineageOS 24 care-map source requires pdx235/nightly.' >&2
        return 1
      }
      # Select the newest 24.0 nightly, never the older 23.2 fixture or a
      # future 24.1/25 series without its own compatibility qualification.
      source_series='24.0'
      ;;
  esac

  if [[ "${PIXENEOS_PDX235_PILOT_RELEASE:-false}" == true ]]; then
    [[ "${ROM_FAMILY:-}" == lineageos && "${DEVICE_NAME}" == pdx235 &&
      "${GRAPHENEOS[UPDATE_CHANNEL]}" == nightly &&
      "${OUTPUT_SCOPE:-}" == published && -z "${source_filename}" ]] || {
      echo 'Error: pdx235 pilot source selected outside approved publication scope.' >&2
      return 1
    }
    source_filename="${_PDX235_PILOT_SOURCE_TARGET}"
  fi

  release_metadata="$(curl -sLf "${endpoint}")" || {
    echo "Error: failed to fetch LineageOS release metadata." >&2
    return 1
  }
  parsed="$(printf '%s' "${release_metadata}" | python3 -c '
import json
import re
import sys
from urllib.parse import urlsplit

try:
    builds = json.load(sys.stdin)
    channel = sys.argv[1]
    device = sys.argv[2]
    source_filename = sys.argv[3]
    source_series = sys.argv[4]
    def in_approved_series(file):
        return not source_series or re.fullmatch(
            rf"lineage-{re.escape(source_series)}-[0-9]{{8}}-nightly-{re.escape(device)}-signed[.]zip",
            file["filename"],
        ) is not None
    build = max(
        (item for item in builds if item["type"] == channel and
         (not source_filename or any(file["filename"] == source_filename
                                     for file in item["files"])) and
         (not source_series or any(in_approved_series(file)
                                   for file in item["files"]))),
        key=lambda item: item.get("datetime", item.get("date", "")),
    )
    item = next(
        item for item in build["files"]
        if item["filename"].endswith(".zip") and item["type"] == channel
        and (not source_filename or item["filename"] == source_filename)
        and in_approved_series(item)
    )
    filename = item["filename"]
    url = item["url"]
    sha256 = item["sha256"]
except (KeyError, IndexError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9._+-]+[.]zip", filename):
    raise SystemExit(1)
if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
    raise SystemExit(1)
if source_filename and sha256 != "b2b4269738df1d3092de49bde6c7d0ffcb62ae19b342213a819187c30b7ab1f8":
    raise SystemExit(1)
if not re.fullmatch(r"[a-z0-9_]+", device) or f"-{device}-" not in filename:
    raise SystemExit(1)
parts = urlsplit(url)
if (
    parts.scheme != "https"
    or parts.hostname not in {"download.lineageos.org", "mirrorbits.lineageos.org"}
    or parts.username
    or parts.password
):
    raise SystemExit(1)
print(filename)
print(url)
print(sha256)
' "${GRAPHENEOS[UPDATE_CHANNEL]}" "${DEVICE_NAME}" "${source_filename}" "${source_series}")" || {
    echo "Error: LineageOS returned invalid release metadata." >&2
    return 1
  }
  filename="${parsed%%$'\n'*}"
  parsed="${parsed#*$'\n'}"
  ota_url="${parsed%%$'\n'*}"
  ROM_OTA_SHA256="${parsed#*$'\n'}"
  latest_version="$(printf '%s\n' "${filename}" | sed -nE 's/.*(^|[^0-9])([0-9]{8})([^0-9]|$).*/\2/p')"
  if [[ ! "${latest_version}" =~ ^[0-9]{8}$ ]]; then
    echo "Error: LineageOS filename lacks an unambiguous build date." >&2
    return 1
  fi

  VERSION[GRAPHENEOS]="${latest_version}"
  GRAPHENEOS[OTA_TARGET]="${filename%.zip}"
  GRAPHENEOS[OTA_URL]="${ota_url}"
}

function fetch_rom_ota_metadata() {
  validate_device_name || return 1

  case "${ROM_PROFILE[PROVIDER]}" in
  grapheneos) fetch_grapheneos_ota_metadata ;;
  lineageos) fetch_lineageos_ota_metadata ;;
  *)
    echo "Error: unsupported OTA metadata provider." >&2
    return 1
    ;;
  esac
}

function verify_rom_ota_digest() {
  local ota_path="${1}"
  local actual_digest

  if [[ "${ROM_PROFILE[PROVIDER]}" != 'lineageos' ]]; then
    return 0
  fi
  if [[ ! "${ROM_OTA_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Error: LineageOS OTA metadata lacks a valid SHA-256." >&2
    return 1
  fi
  actual_digest="$(sha256sum -- "${ota_path}" | awk '{print $1}')" || return 1
  if [[ "${actual_digest}" != "${ROM_OTA_SHA256}" ]]; then
    echo "Error: downloaded LineageOS OTA SHA-256 does not match metadata." >&2
    return 1
  fi
}
