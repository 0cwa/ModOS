#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Temporary, non-configurable release quarantine for 0cwa/ModOS#114.
# It is deliberately independent of the October 9 source digest and the
# selected root mode. Removing it requires a separate reviewed security change
# after care-map runtime conformance and physical-device acceptance.
#
# Source this file from the ROM profile gate and *both* publication helpers.
# The only permitted pdx235/LineageOS scope is local-unpublished.
enforce_pdx235_care_map_quarantine() {
  if [[ "$#" -ne 3 ]]; then
    echo "Error: pdx235 quarantine requires ROM family, device, and output scope." >&2
    return 1
  fi

  local rom_family="$1" device="$2" output_scope="$3"
  if [[ "${rom_family}" == 'lineageos' && "${device}" == 'pdx235' &&
    "${output_scope}" != 'local-unpublished' ]]; then
    echo "Error: ModOS#114 blocks LineageOS/pdx235 publication and shared outputs; only local-unpublished build-only acceptance is permitted." >&2
    return 1
  fi
}
