#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Temporary, non-configurable release quarantine for 0cwa/ModOS#114.
# It is deliberately independent of the October 9 source digest and the
# selected root mode. Removing it requires a separate reviewed security change
# after care-map runtime conformance and physical-device acceptance.
#
# Source this file from the ROM profile gate and *both* publication helpers.
# The approved public scope is the LineageOS 24.0 nightly series on pdx235.
# Every ZIP requires the official builds API SHA-256, independent publisher
# signature, and strict output cryptographic and runtime care-map postflight.
# Future series and other devices/flavors remain outside this release policy.
pdx235_pilot_source_matches() {
  [[ "${PIXENEOS_PDX235_PILOT_RELEASE:-false}" == true &&
    "${ROM_FAMILY:-}" == lineageos && "${DEVICE_NAME:-}" == pdx235 &&
    "${GRAPHENEOS[UPDATE_CHANNEL]:-}" == nightly &&
    "${GRAPHENEOS[OTA_TARGET]:-}" =~ ^lineage-24[.]0-([0-9]{8})-nightly-pdx235-signed$ &&
    "${BASH_REMATCH[1]}" > 20261011 &&
    "${ROM_OTA_SHA256:-}" =~ ^[0-9a-f]{64}$ &&
    "${VERSION[GRAPHENEOS]:-}" == "${BASH_REMATCH[1]}" ]]
}

enforce_pdx235_pilot_source() {
  pdx235_pilot_source_matches && return 0
  echo 'Error: pdx235 publishing requires an official, signed LineageOS 24.0 pdx235 nightly with valid API SHA-256.' >&2
  return 1
}
enforce_pdx235_care_map_quarantine() {
  if [[ "$#" -ne 3 ]]; then
    echo "Error: pdx235 quarantine requires ROM family, device, and output scope." >&2
    return 1
  fi

  local rom_family="$1" device="$2" output_scope="$3"
  if [[ "${rom_family}" == 'lineageos' && "${device}" == 'pdx235' &&
    "${output_scope}" != 'local-unpublished' ]]; then
    # Early policy runs before source selection. Only the explicitly selected
    # test-release workflow may proceed to fetching source metadata; that
    # exact filename AND digest are verified before key use or signing.
    if [[ "${output_scope}" == published &&
      "${PIXENEOS_PDX235_PILOT_RELEASE:-false}" == true &&
      ( -z "${GRAPHENEOS_VERSION:-}" || "${GRAPHENEOS_VERSION}" == 20261012 ) ]]; then
      return 0
    fi
    echo "Error: ModOS#114 blocks LineageOS/pdx235 publication outside the reviewed 24.0 series." >&2
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
