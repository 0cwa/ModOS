#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Require the exact helper that contains the repaired microG native-library
# and DUMP behavior plus the reviewed additive extension/report APIs. Exact
# head qualification preserves the bounded pdx235 runtime policy.
require_pdx235_repaired_helper() {
  local family="$1" device="$2" mode="$3" revision="$4"
  [[ "${family}" == lineageos && "${device}" == pdx235 ]] || return 0
  [[ "${mode}" == magisk ]] && return 0
  [[ "${revision}" == 21b8318ab43908dbc6d9c797cffabf99a86f9d5b ]] && return 0
  echo 'Error: pdx235 rootless requires the exactly reviewed repaired helper revision.' >&2
  return 1
}
