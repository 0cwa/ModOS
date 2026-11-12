#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

source src/util_functions.sh

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

fail() {
  echo "$*" >&2
  exit 1
}

test_helper_repository_preflight() (
  WORKDIR="${TEST_ROOT}/repository"
  local helper="${WORKDIR}/tools/my-avbroot-setup"
  mkdir -p "${helper}"
  git -C "${helper}" init -q
  printf '#!/usr/bin/env python3\n' >"${helper}/patch.py"
  git -C "${helper}" add patch.py
  git -C "${helper}" -c user.name=test -c user.email=test@example.invalid commit -qm fixture

  VERSION[AVBROOT_SETUP]="$(git -C "${helper}" rev-parse HEAD)"
  helper_repository_preflight

  VERSION[AVBROOT_SETUP]=0000000000000000000000000000000000000000
  if helper_repository_preflight 2>/dev/null; then
    fail "mismatched helper revision was accepted"
  fi
)

test_helper_contract_preflight() (
  WORKDIR="${TEST_ROOT}/contract"
  local helper="${WORKDIR}/tools/my-avbroot-setup"
  mkdir -p "${helper}"
  printf '#!/usr/bin/env python3\n' >"${helper}/patch.py"

  python() {
    [[ "${1}" == "${helper}/patch.py" && "${2}" == "--help" ]] || return 1
    printf '%s\n' 'usage: patch.py --release-url URL --adapter-extension PATH'
  }
  helper_contract_preflight

  local missing_option
  for missing_option in --release-url --adapter-extension; do
    python() { printf 'usage: patch.py %s\n' "${missing_option}"; }
    if helper_contract_preflight 2>/dev/null; then
      fail "missing native helper option was accepted: ${missing_option}"
    fi
  done

  python() { return 1; }
  if helper_contract_preflight 2>/dev/null; then
    fail "failed helper smoke check was reported as success"
  fi
)

test_repository_preflight_runs_before_ota_download() (
  WORKDIR="${TEST_ROOT}/ordering"
  mkdir -p "${WORKDIR}"

  helper_repository_preflight() { return 1; }
  download_ota() { touch "${TEST_ROOT}/ota-downloaded"; }
  create_ota() { touch "${TEST_ROOT}/ota-created"; }

  if create_and_make_release >/dev/null 2>&1; then
    fail "failed helper repository preflight was reported as success"
  fi
  [[ ! -e "${TEST_ROOT}/ota-downloaded" ]] ||
    fail "OTA download ran after helper repository preflight failed"
  [[ ! -e "${TEST_ROOT}/ota-created" ]] ||
    fail "OTA creation ran after helper repository preflight failed"
)

test_contract_smoke_runs_after_environment_setup() (
  local events="${TEST_ROOT}/events"
  CLEANUP=true

  generate_ota_info() { printf 'generate\n' >>"${events}"; }
  env_setup() { printf 'env\n' >>"${events}"; }
  helper_contract_preflight() { printf 'smoke\n' >>"${events}"; }
  patch_ota() { printf 'patch\n' >>"${events}"; }

  create_ota
  [[ "$(<"${events}")" == $'generate\nenv\nsmoke\npatch' ]] ||
    fail "unexpected create_ota ordering: $(<"${events}")"
)

test_helper_repository_preflight
test_helper_contract_preflight
test_repository_preflight_runs_before_ota_download
test_contract_smoke_runs_after_environment_setup

echo "helper preflight tests passed"
