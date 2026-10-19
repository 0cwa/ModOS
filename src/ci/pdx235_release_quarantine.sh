#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Published pdx235 builds are supported only for the LineageOS 24.0 nightly
# series. The source API identity, independent publisher key, regenerated
# signatures, and strict runtime care-map postflight remain mandatory.
pdx235_supported_source_matches() {
  [[ "${ROM_FAMILY:-}" == lineageos && "${DEVICE_NAME:-}" == pdx235 &&
    "${GRAPHENEOS[UPDATE_CHANNEL]:-}" == nightly &&
    "${VERSION[GRAPHENEOS]:-}" =~ ^20[0-9]{6}$ &&
    "${GRAPHENEOS[OTA_TARGET]:-}" == "lineage-24.0-${VERSION[GRAPHENEOS]}-nightly-pdx235-signed" &&
    "${ROM_OTA_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]
}

enforce_pdx235_supported_source() {
  pdx235_supported_source_matches && return 0
  echo 'Error: pdx235 publishing requires an official LineageOS 24.0 pdx235 nightly with a valid API SHA-256.' >&2
  return 1
}
enforce_pdx235_publication_scope() {
  if [[ "$#" -lt 3 || "$#" -gt 5 ]]; then
    echo "Error: pdx235 publication policy requires ROM family, device, and output scope." >&2
    return 1
  fi

  local rom_family="$1" device="$2" output_scope="$3"
  local channel="${4:-${GRAPHENEOS_UPDATE_CHANNEL:-}}"
  local version="${5:-${GRAPHENEOS_VERSION:-}}"
  if [[ -z "${channel}" ]] && declare -p GRAPHENEOS >/dev/null 2>&1; then
    channel="${GRAPHENEOS[UPDATE_CHANNEL]:-}"
  fi
  if [[ "${rom_family}" == 'lineageos' && "${device}" == 'pdx235' &&
    "${output_scope}" != 'local-unpublished' ]]; then
    # This pre-source gate can only admit the explicitly supported public
    # family/device/channel. Main verifies exact API target and digest before
    # signing, and both publisher helpers independently verify their assets.
    if [[ "${output_scope}" == published &&
      "${rom_family}" == lineageos && "${device}" == pdx235 &&
      "${channel}" == nightly &&
      ( -z "${version}" || "${version}" =~ ^20[0-9]{6}$ ) ]]; then
      return 0
    fi
    echo "Error: LineageOS/pdx235 publication is limited to the supported 24.0 nightly series." >&2
    return 1
  fi
}

# Worklane 4: exact-output freshness gate. The historical ModOS patch path
# treats local ZIP existence as a success shortcut, bypassing strict verification
# of signatures, output care maps and the secondary Magisk image. Refuse that
# shortcut for pdx235 until post-sign cryptographic+semantic re-verification
# can be proved. No automatic deletion/reuse of previously signed outputs.
#
# Arguments: ROM family, device, resolved root mode, primary OTA, secondary OTA.
reject_pdx235_preexisting_outputs() {
  if [[ "$#" -ne 5 ]]; then
    echo "Error: pdx235 output-freshness gate requires five arguments." >&2
    return 1
  fi
  local family="$1" device="$2" mode="$3" primary="$4" secondary="$5"
  [[ "${family}" == 'lineageos' && "${device}" == 'pdx235' ]] || return 0
  [[ -n "${primary}" ]] || {
    echo "Error: pdx235 primary output path is missing." >&2
    return 1
  }

  local -a output_paths=("${primary}")
  case "${mode}" in
    rootless|magisk)
      [[ -z "${secondary}" ]] || {
        echo "Error: unexpected pdx235 secondary output path." >&2
        return 1
      }
      ;;
    both)
      [[ -n "${secondary}" && "${secondary}" != "${primary}" ]] || {
        echo "Error: pdx235 paired output paths are missing or identical." >&2
        return 1
      }
      output_paths+=("${secondary}")
      ;;
    *)
      echo "Error: unknown pdx235 root mode: ${mode}" >&2
      return 1
      ;;
  esac

  local output candidate
  for output in "${output_paths[@]}"; do
    for candidate in \
      "${output}" \
      "${output}.csig" \
      "${output}.selection.json" \
      "${output}.patch-report.json" \
      "${output}.microg-patch-report.json"; do
      if [[ -e "${candidate}" || -L "${candidate}" ]]; then
        echo "Error: refusing to reuse existing pdx235 OTA output/evidence: ${candidate}" >&2
        return 1
      fi
    done
  done
}
