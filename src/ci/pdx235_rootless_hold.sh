#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# A verified private repair is not a production release receipt. No environment
# override: lifting this hold requires a reviewed publication-policy change.
enforce_pdx235_rootless_hold() {
  local family="$1" device="$2" mode="$3" scope="$4"
  [[ "${family}" == lineageos && "${device}" == pdx235 ]] || return 0
  case "${mode}" in
    magisk) return 0 ;;
    rootless|both) ;;
    *) echo 'Error: missing/unknown pdx235 root mode; refusing publication.' >&2; return 1 ;;
  esac
  [[ "${scope}" == local-unpublished ]] && return 0
  echo 'Error: pdx235 rootless OTA publication held after the October 17 microG bootloop; reviewed release verification is required.' >&2
  return 1
}

# Keep this independent of the temporary hold, so a stale helper cannot become
# publishable merely by lifting the hold. A successor needs explicit review.
require_pdx235_repaired_helper() {
  local family="$1" device="$2" mode="$3" revision="$4"
  [[ "${family}" == lineageos && "${device}" == pdx235 ]] || return 0
  [[ "${mode}" == magisk ]] && return 0
  [[ "${revision}" == abdede21c1b048e11700ec2789d0fb96ace0b0e4 ]] && return 0
  echo 'Error: pdx235 rootless requires the reviewed microG DUMP repair helper revision.' >&2
  return 1
}
