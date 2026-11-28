#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Require the exact helper that contains the repaired microG native-library
# and DUMP behavior plus the reviewed additive extension/report APIs. Exact
# head qualification preserves the bounded pdx235 runtime policy.
require_pdx235_repaired_helper() {
  local family="$1" device="$2" mode="$3" revision="$4"
  [[ "${family}" == lineageos && "${device}" == pdx235 ]] || return 0
  [[ "${mode}" == magisk ]] && return 0
  [[ "${revision}" == 2e342a96f2d7dcc0e7ec805392555974797dbfc7 ]] && return 0
  echo 'Error: pdx235 rootless requires the reviewed microG native-library and DUMP helper revision.' >&2
  return 1
}
