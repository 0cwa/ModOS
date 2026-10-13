#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

sdk_root="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"

resolve_tool() {
  local name="${1}"
  local candidate

  if candidate="$(command -v "${name}" 2>/dev/null)"; then
    realpath -e -- "${candidate}"
    return
  fi
  [[ -n "${sdk_root}" && -d "${sdk_root}" ]] || return 1

  case "${name}" in
    apksigner)
      find "${sdk_root}/build-tools" -mindepth 2 -maxdepth 2         -type f -name apksigner -perm -u+x 2>/dev/null | sort -V | tail -n 1
      ;;
    apkanalyzer)
      find "${sdk_root}/cmdline-tools" -type f         -path '*/bin/apkanalyzer' -perm -u+x 2>/dev/null | sort -V | tail -n 1
      ;;
  esac
}

apksigner="$(resolve_tool apksigner)"
apkanalyzer="$(resolve_tool apkanalyzer)"
[[ -x "${apksigner}" && -x "${apkanalyzer}" && -n "${GITHUB_PATH:-}" ]] || {
  echo "::error::Android apksigner/apkanalyzer are required for locked APK verification." >&2
  exit 1
}

printf '%s\n' "$(dirname -- "${apksigner}")" >>"${GITHUB_PATH}"
printf '%s\n' "$(dirname -- "${apkanalyzer}")" >>"${GITHUB_PATH}"
