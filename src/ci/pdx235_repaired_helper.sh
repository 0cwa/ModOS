#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Require the helper with reviewed microG native-library extraction and DUMP access.
# A successor revision needs explicit review and updated regression coverage.
require_pdx235_repaired_helper() {
  local family="$1" device="$2" mode="$3" revision="$4"
  [[ "${family}" == lineageos && "${device}" == pdx235 ]] || return 0
  [[ "${mode}" == magisk ]] && return 0
  [[ "${revision}" == d6eccab076ba48889c2279ce7c88e43261cbd1f0 ]] && return 0
  echo 'Error: pdx235 rootless requires the reviewed microG native-library and DUMP helper revision.' >&2
  return 1
}
