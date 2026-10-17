#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Require the exact helper used by the successful October 17 rootless repair.
# A successor revision needs explicit review and updated regression coverage.
require_pdx235_repaired_helper() {
  local family="$1" device="$2" mode="$3" revision="$4"
  [[ "${family}" == lineageos && "${device}" == pdx235 ]] || return 0
  [[ "${mode}" == magisk ]] && return 0
  [[ "${revision}" == abdede21c1b048e11700ec2789d0fb96ace0b0e4 ]] && return 0
  echo 'Error: pdx235 rootless requires the reviewed microG DUMP repair helper revision.' >&2
  return 1
}
