#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

[[ -n "${MODOS_HELPER_SOURCE_ROOT:-}" && -d "${MODOS_HELPER_SOURCE_ROOT}" ]] || {
  echo 'MODOS_HELPER_SOURCE_ROOT must name the local pinned helper checkout.' >&2
  exit 2
}
[[ -n "${MODOS_HELPER_RUNTIME_PYTHON:-}" && -x "${MODOS_HELPER_RUNTIME_PYTHON}" ]] || {
  echo 'MODOS_HELPER_RUNTIME_PYTHON must name the installed helper Python.' >&2
  exit 2
}

source src/util_functions.sh
: "${TMPDIR:?set TMPDIR to a task scratch directory}"
TEST_ROOT="$(mktemp -d "${TMPDIR}/modos-helper-reuse.XXXXXX")"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT
WORKDIR="${TEST_ROOT}/work"
PIXENEOS_AVBROOT_SETUP_SOURCE='https://github.com/0cwa/my-avbroot-setup'
OUTPUTS[PATCHED_OTA]='fixture-output.zip'
helper="${WORKDIR}/tools/my-avbroot-setup"
mkdir -p -- "$(dirname -- "${helper}")"
git clone --quiet --shared --no-checkout \
  "${MODOS_HELPER_SOURCE_ROOT}" "${helper}"
git -C "${helper}" checkout --quiet --detach "${VERSION[AVBROOT_SETUP]}"
git -C "${helper}" remote set-url origin "${PIXENEOS_AVBROOT_SETUP_SOURCE}"

for run in 1 2; do
  my_avbroot_setup >/dev/null
  enable_venv "${helper}"
  [[ "$(realpath -- "${VIRTUAL_ENV}")" == "$(realpath -- "${WORKDIR}/venvs/my-avbroot-setup")" ]] || {
    echo 'Helper Python environment was not kept outside its source checkout.' >&2
    exit 1
  }
  PYTHONDONTWRITEBYTECODE=1 "${MODOS_HELPER_RUNTIME_PYTHON}" \
    "${helper}/patch.py" --help >/dev/null
  [[ -z "$(git -C "${helper}" status --porcelain=v1 -z \
    --untracked-files=all --ignored=matching)" ]] || {
    echo "Helper checkout changed during repeated setup ${run}." >&2
    git -C "${helper}" status --short --ignored >&2
    exit 1
  }
done

echo 'Repeated native validation, external venv setup, and patch help left helper source pristine.'
