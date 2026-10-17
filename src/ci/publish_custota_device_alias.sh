#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Custota chooses the metadata filename from the OTA's pre-device property.
# LineageOS uses pdx235 internally, but the signed Sony OTA's pre-device is
# XQ-DC72. Keep the *same* verified OTA and csig pointers under both names.
#
# Call after source/OTA verification, release asset checks, checkout of
# gh-pages, and writing the canonical <flavor>/pdx235.json.
publish_custota_device_alias() {
  [[ "$#" -eq 4 ]] || {
    echo 'Error: Custota alias requires ROM, device, flavor and canonical path.' >&2
    return 1
  }

  local family="$1" device="$2" flavor="$3" canonical="$4" alias
  [[ "${family}" == lineageos && "${device}" == pdx235 ]] || return 0

  case "${flavor}" in
    rootless|magisk) ;;
    *) echo 'Error: unknown Custota alias flavor.' >&2; return 1 ;;
  esac
  [[ "${canonical}" == "${flavor}/${device}.json" ]] || {
    echo 'Error: Custota alias source must be the canonical published device metadata.' >&2
    return 1
  }
  [[ -d "${flavor}" && ! -L "${flavor}" &&
     -f "${canonical}" && ! -L "${canonical}" ]] || {
    echo 'Error: canonical published Custota metadata is missing or unsafe.' >&2
    return 1
  }

  alias="${flavor}/XQ-DC72.json"
  [[ ! -L "${alias}" && ! -d "${alias}" ]] || {
    echo 'Error: refusing unsafe Custota device alias destination.' >&2
    return 1
  }

  cp -- "${canonical}" "${alias}" || return 1
  cmp -s -- "${canonical}" "${alias}" || {
    echo 'Error: Custota alias differs from the verified canonical metadata.' >&2
    return 1
  }
  git add -- "${alias}"
}
