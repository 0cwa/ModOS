#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

# Shared selection/verification boundary for acquisition, fingerprints and
# finished-image checks. Resolve the script when sourced, before callers cd.
_magisk_artifact_script="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/magisk_artifact.py"

function run_magisk_artifact() {
  python3 "${_magisk_artifact_script}" \
    --repository "${MAGISK[REPOSITORY]}" \
    --version "${VERSION[MAGISK]}" \
    --sha256 "${MAGISK_APK_SHA256:-}" \
    "$@"
}
