#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

_companion_ci_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

companion_publication_scope() {
  case "${DEVICE_NAME:-}" in
    XQ-DC72)
      echo 'Error: publish pdx235 and its XQ-DC72 alias together; direct alias publication is forbidden.' >&2
      return 1 ;;
    pdx235)
      [[ "${ROM_FAMILY:-}" == lineageos ]] || {
        echo 'Error: the protected pdx235 feed requires the LineageOS publication contract.' >&2
        return 1
      }
      echo protected ;;
    *) echo unrelated ;;
  esac
}

check_companion_publication() {
  local scope
  scope="$(companion_publication_scope)" || return 1
  [[ "${scope}" == protected ]] || return 0
  python3 "${_companion_ci_dir}/companion_continuity.py" \
    --repository "${GITHUB_REPOSITORY:?repository is required}" "$@"
}

check_companion_release_inputs() {
  local scope
  scope="$(companion_publication_scope)" || return 1
  [[ "${scope}" == protected ]] || return 0
  local -a args=(--release-tag "${1:?release tag is required}")
  case "${ROOT_MODE_RESOLVED:-${ROM_FLAVOR:-}}" in
    both)
      args+=(--rootless "${SELECTION_METADATA_ROOTLESS:?}" "${OTA_METADATA_ROOTLESS:?}" "${OUTPUTS_PATCHED_OTA_ROOTLESS:?}"
             --magisk "${SELECTION_METADATA_MAGISK:?}" "${OTA_METADATA_MAGISK:?}" "${OUTPUTS_PATCHED_OTA_MAGISK:?}") ;;
    rootless|magisk)
      args+=("--${ROOT_MODE_RESOLVED:-${ROM_FLAVOR}}" "${SELECTION_METADATA:?}" "${DEVICE_NAME}.json" "${OUTPUTS_PATCHED_OTA:?}") ;;
    *) echo 'Error: publication has no resolved root flavor.' >&2; return 1 ;;
  esac
  check_companion_publication "${args[@]}"
}

prepare_companion_publication_snapshot() {
  local scope
  scope="$(companion_publication_scope)" || return 1
  [[ "${scope}" == protected ]] || return 0
  COMPANION_FEED_SNAPSHOT="$(check_companion_publication --uploaded "$@")" || return 1
  # Never reset a stale local branch or force-push; another writer requires
  # a fresh review/retry before either canonical pointer is touched.
  git fetch origin gh-pages:refs/remotes/origin/gh-pages || return 1
  [[ "$(git rev-parse refs/remotes/origin/gh-pages)" == "${COMPANION_FEED_SNAPSHOT}" ]] || {
    echo 'Error: published feeds changed during continuity validation.' >&2; return 1;
  }
  if git show-ref --verify --quiet refs/heads/gh-pages; then
    [[ "$(git rev-parse refs/heads/gh-pages)" == "${COMPANION_FEED_SNAPSHOT}" ]] || {
      echo 'Error: local gh-pages is stale; refusing to replace published feeds.' >&2; return 1;
    }
  fi
}
