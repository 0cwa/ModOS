#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

set -euo pipefail

REPO_ROOT="$(pwd -P)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT

source src/util_functions.sh

fail() {
  echo "$*" >&2
  exit 1
}

assert_equals() {
  local expected="${1}"
  local actual="${2}"
  local context="${3}"
  [[ "${actual}" == "${expected}" ]] ||
    fail "${context}: expected <${expected}>, got <${actual}>"
}

assert_rejected() {
  local config="${1}"
  local case_name="${2}"
  local tmpdir="${TEST_ROOT}/${case_name}"
  local status=0
  mkdir -p "${tmpdir}"
  printf '%s\n' "${config}" >"${tmpdir}/env.toml"
  (cd "${tmpdir}" && check_toml_env >/dev/null 2>&1) || status=$?
  [[ "${status}" -ne 0 ]] || fail "${case_name}: malformed config was accepted"
}

test_current_legacy_env() (
  local tmpdir="${TEST_ROOT}/legacy"
  mkdir -p "${tmpdir}"
  printf '%s\n' \
    '[device]' \
    'DEVICE_NAME = "legacy-device"' \
    "'GRAPHENEOS[UPDATE_CHANNEL]' = \"stable\"" \
    'ROOT = "true"' \
    "'ADDITIONALS[AFSR]' = \"false\"" \
    'PIXENEOS_RELEASE_BASE_URL = "https://example.test/releases"' \
    >"${tmpdir}/env.toml"

  (
    cd "${tmpdir}"
    check_toml_env >/dev/null
    assert_equals legacy-device "${DEVICE_NAME}" "legacy device"
    assert_equals stable "${GRAPHENEOS[UPDATE_CHANNEL]}" "legacy update channel"
    assert_equals true "${ADDITIONALS[ROOT]}" "legacy root"
    assert_equals false "${ADDITIONALS[AFSR]}" "legacy AFSR"
    assert_equals https://example.test/releases "${PIXENEOS_RELEASE_BASE_URL}" \
      "legacy release URL"
  )
)

test_section_semantics() (
  local tmpdir="${TEST_ROOT}/sections"
  mkdir -p "${tmpdir}"
  printf '%s\n' \
    '[device]' \
    'DEVICE_NAME = "section-device"' \
    'ROM_FAMILY = "grapheneos"' \
    "'GRAPHENEOS[UPDATE_CHANNEL]' = \"beta\"" \
    '[build]' \
    'ROOT = false' \
    'ROOT_MODE = "both"' \
    "'ADDITIONALS[MICROG]' = true" \
    "'ADDITIONALS[AFSR]' = false" \
    'MAGISK_PREINIT = "sda10"' \
    'FORCE_UPDATE = true' \
    '[github]' \
    'PIXENEOS_RELEASE_OWNER = ""' \
    'PIXENEOS_RELEASE_REPOSITORY = "fixture"' \
    >"${tmpdir}/env.toml"

  (
    cd "${tmpdir}"
    check_toml_env >/dev/null
    assert_equals section-device "${DEVICE_NAME}" "device section"
    assert_equals grapheneos "${ROM_FAMILY}" "device ROM family"
    assert_equals beta "${GRAPHENEOS[UPDATE_CHANNEL]}" "device channel"
    assert_equals false "${ADDITIONALS[ROOT]}" "build root"
    assert_equals both "${ROOT_MODE}" "build root mode"
    assert_equals true "${ADDITIONALS[MICROG]}" "build microG"
    assert_equals false "${ADDITIONALS[AFSR]}" "build AFSR"
    assert_equals true "${FORCE_UPDATE}" "build force update"
    assert_equals '' "${PIXENEOS_RELEASE_OWNER}" "explicit empty GitHub value"
    assert_equals true "${TOML_CONFIG_PRESENT[release_owner]}" \
      "explicit empty presence"
  )
)

test_precedence_and_omission() (
  local tmpdir="${TEST_ROOT}/precedence"
  mkdir -p "${tmpdir}"
  printf '%s\n' '[build]' "'ADDITIONALS[AFSR]' = false" >"${tmpdir}/env.toml"

  (
    cd "${tmpdir}"
    check_toml_env >/dev/null
    assert_equals false "$(toml_resolve_value afsr declaration-default)" \
      "validated config over declaration default"
  )

  rm -f "${tmpdir}/env.toml"
  (
    cd "${tmpdir}"
    check_toml_env >/dev/null
    assert_equals declaration-default "$(toml_resolve_value afsr declaration-default)" \
      "omitted input preserves declaration default"
    [[ -z "${TOML_CONFIG_PRESENT[afsr]+x}" ]] ||
      fail "omitted AFSR was reported as present"
  )

  printf '%s\n' '[build]' "'ADDITIONALS[AFSR]' = true" >"${tmpdir}/env.toml"
  env REPO_ROOT="${REPO_ROOT}" CONFIG_ROOT="${tmpdir}" \
    ADDITIONALS_AFSR=false bash -c '
      source "${REPO_ROOT}/src/util_functions.sh"
      cd "${CONFIG_ROOT}"
      check_toml_env >/dev/null
      [[ "$(toml_resolve_value afsr declaration-default)" == false ]]
    '
)

test_rejections() {
  assert_rejected '[unknown]'$'\n''ROOT = false' unknown-section
  assert_rejected '[build]'$'\n''UNKNOWN = false' unknown-key
  assert_rejected '[build]'$'\n''ROOT = "yes"' malformed-boolean
  assert_rejected '[build]'$'\n''ROOT = false'$'\n''ROOT = true' duplicate-key
  assert_rejected '[device]'$'\n''DEVICE_NAME = true' unquoted-string
  assert_rejected '[device]'$'\n''DEVICE_NAME = "unterminated' malformed-string
}

test_current_legacy_env
test_section_semantics
test_precedence_and_omission
test_rejections

echo "typed config resolver tests passed"
