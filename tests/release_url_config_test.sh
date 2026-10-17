#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

set -eo pipefail

source src/util_functions.sh

set -u

fail() {
  echo "$*" >&2
  exit 1
}


reset_release_config() {
  unset GITHUB_REPOSITORY
  PROJECT_NAME=ModOS
  PIXENEOS_RELEASE_OWNER=""
  PIXENEOS_RELEASE_REPOSITORY=""
  PIXENEOS_RELEASE_BASE_URL=""
  PIXENEOS_AVBROOT_SETUP_SOURCE=""
  VERSION[GRAPHENEOS]="2026050400"
  VERSION[AVBROOT_SETUP]="9dde8207020684f2142e720823ad4fdb7c8c4cfb"
  OUTPUTS[PATCHED_OTA]="shiba-2026050400-rootless-abc1234.zip"
}

test_toml_env_allowlisted_assignments() {
  local force_update="${1:-true}"
  local tmpdir marker expected_literal status=0 module
  local -a module_toggles=(
    AFSR
    ALTERINSTALLER
    BCR
    CUSTOTA
    MSD
    OEMUNLOCKONBOOT
    BOOT_ANIMATION
    FDROID_PRIVILEGED_EXTENSION
  )
  tmpdir="$(mktemp -d)"
  marker="${tmpdir}/command-executed"
  expected_literal="literal value = \$(echo should-not-run) \`touch ${marker}\` ; &"

  (
    cd "${tmpdir}"
    printf '%s\n' \
      '[device]' \
      'PROJECT_NAME = "FixtureOS"' \
      'DEVICE_NAME = "fixture-device"' \
      'INTERACTIVE_MODE = "false"' \
      'ROM_FAMILY = "grapheneos"' \
      'OUTPUT_SCOPE = "local-published"' \
      'PIXENEOS_RELEASE_OWNER = ""' \
      'PIXENEOS_RELEASE_REPOSITORY = "fixture-repo"' \
      "PIXENEOS_RELEASE_BASE_URL = \"${expected_literal}\"" \
      'PIXENEOS_AVBROOT_SETUP_SOURCE = "https://example.com/setup.git"' \
      "'GRAPHENEOS[UPDATE_CHANNEL]' = \"alpha\"" \
      'MAGISK[REPOSITORY] = "fixture/magisk=source"' \
      'ROOT = "true"' \
      'MAGISK_PREINIT = "sda10"' \
      "FORCE_UPDATE = \"${force_update}\"" \
      >env.toml

    for module in "${module_toggles[@]}"; do
      if [[ "${module}" == "BOOT_ANIMATION" ]]; then
        printf "'ADDITIONALS[%s]' = \"true\"\n" "${module}" >>env.toml
      else
        printf "'ADDITIONALS[%s]' = \"false\"\n" "${module}" >>env.toml
      fi
    done

    DEVICE_NAME="default-device"
    INTERACTIVE_MODE="default-interactive"
    ROM_FAMILY="default-rom"
    OUTPUT_SCOPE="default-scope"
    PIXENEOS_RELEASE_OWNER="default-owner"
    PIXENEOS_RELEASE_REPOSITORY="default-repository"
    PIXENEOS_RELEASE_BASE_URL="default-base-url"
    PIXENEOS_AVBROOT_SETUP_SOURCE="default-setup-source"
    GRAPHENEOS[UPDATE_CHANNEL]="default-channel"
    for module in "${module_toggles[@]}"; do
      ADDITIONALS[${module}]="default-${module}"
    done
    ADDITIONALS[ROOT]="false"
    MAGISK[PREINIT]="default-preinit"
    MAGISK[REPOSITORY]="default-repository"
    FORCE_UPDATE="default-force-update"

    check_toml_env >/dev/null

    [[ "${PROJECT_NAME}" == FixtureOS ]]
    [[ "${DEVICE_NAME}" == "fixture-device" ]]
    [[ "${INTERACTIVE_MODE}" == "false" ]]
    [[ "${ROM_FAMILY}" == "grapheneos" ]]
    [[ "${OUTPUT_SCOPE}" == "local-published" ]]
    [[ "${PIXENEOS_RELEASE_OWNER}" == "default-owner" ]]
    [[ "${PIXENEOS_RELEASE_REPOSITORY}" == "fixture-repo" ]]
    [[ "${PIXENEOS_RELEASE_BASE_URL}" == "${expected_literal}" ]]
    [[ "${PIXENEOS_AVBROOT_SETUP_SOURCE}" == "https://example.com/setup.git" ]]
    [[ "${GRAPHENEOS[UPDATE_CHANNEL]}" == "alpha" ]]
    for module in "${module_toggles[@]}"; do
      if [[ "${module}" == "BOOT_ANIMATION" ]]; then
        [[ "${ADDITIONALS[${module}]}" == "true" ]]
      else
        [[ "${ADDITIONALS[${module}]}" == "false" ]]
      fi
    done
    [[ "${ADDITIONALS[ROOT]}" == "true" ]]
    [[ "${MAGISK[PREINIT]}" == "sda10" ]]
    [[ "${MAGISK[REPOSITORY]}" == "fixture/magisk=source" ]]
    [[ "${FORCE_UPDATE}" == "${force_update}" ]]
    [[ ! -e "${marker}" ]]
  ) || status=$?

  rm -rf "${tmpdir}"
  return "${status}"
}

assert_toml_env_rejected() {
  local assignment="${1}" tmpdir status=0
  tmpdir="$(mktemp -d)"

  (
    cd "${tmpdir}"
    printf '%s\n' "${assignment}" >env.toml
    check_toml_env >/dev/null 2>&1
  ) || status=$?

  rm -rf "${tmpdir}"
  if [[ "${status}" -eq 0 ]]; then
    echo "Expected check_toml_env to reject: ${assignment}" >&2
    return 1
  fi
}

test_toml_env_rejects_unknown_and_malformed_keys() {
  assert_toml_env_rejected 'UNKNOWN_KEY = "value"'
  assert_toml_env_rejected 'GITHUB_USER = "attacker"'
  assert_toml_env_rejected 'GRAPHENEOS[UNKNOWN] = "value"'
  assert_toml_env_rejected 'ADDITIONALS[UNKNOWN] = "value"'
  assert_toml_env_rejected 'FORCE_UPDATE[VALUE] = "true"'
  assert_toml_env_rejected 'GRAPHENEOS[UPDATE_CHANNEL = "value"'
  assert_toml_env_rejected 'DEVICE-NAME = "value"'
}

capture_setup_url() {
  local expected_url="${1}" expected_source="${PIXENEOS_AVBROOT_SETUP_SOURCE:-${DOMAIN}/0cwa/my-avbroot-setup}" tmpdir status=0
  local -a invocation=()
  tmpdir="$(mktemp -d)"
  WORKDIR="${tmpdir}/work"

  python3() {
    invocation=("$@")
  }
  my_avbroot_setup >/dev/null || status=$?
  unset -f python3

  [[ "${status}" -eq 0 ]] || fail "Compatibility helper invocation failed"
  [[ "${#invocation[@]}" -eq 6 ]] || fail "Unexpected compatibility helper arguments"
  [[ "${invocation[0]}" == "tools/compat/avbroot_setup_compat.py" ]] ||
    fail "Unexpected compatibility helper path"
  [[ "${invocation[1]}" == "--source" ]] ||
    fail "Missing compatibility helper source option"
  [[ "${invocation[2]}" == "${expected_source}" ]] ||
    fail "Unexpected compatibility helper source: ${invocation[2]}"
  [[ "${invocation[3]}" == "${WORKDIR}/tools/my-avbroot-setup" ]] ||
    fail "Unexpected materialized helper root"
  [[ "${invocation[4]}" == "${expected_url}" ]] ||
    fail "Unexpected release URL: ${invocation[4]}"
  [[ "${invocation[5]}" == "${VERSION[AVBROOT_SETUP]}" ]] ||
    fail "Unexpected compatibility revision"

  rm -rf "${tmpdir}"
}

test_default_release_url() {
  reset_release_config
  capture_setup_url \
    "https://github.com/0cwa/ModOS/releases/download/2026050400/shiba-2026050400-rootless-abc1234.zip"
}

test_github_repository_release_url() {
  reset_release_config
  GITHUB_REPOSITORY="myorg/myrepo"
  capture_setup_url \
    "https://github.com/myorg/myrepo/releases/download/2026050400/shiba-2026050400-rootless-abc1234.zip"
}

test_release_base_url_override() {
  reset_release_config
  PIXENEOS_RELEASE_BASE_URL="https://releases.example.com/PixeneOS/shiba/2026050400"
  capture_setup_url \
    "https://releases.example.com/PixeneOS/shiba/2026050400/shiba-2026050400-rootless-abc1234.zip"
}

test_release_base_url_trailing_slash() {
  reset_release_config
  PIXENEOS_RELEASE_BASE_URL="https://releases.example.com/PixeneOS/shiba/2026050400/"
  capture_setup_url \
    "https://releases.example.com/PixeneOS/shiba/2026050400/shiba-2026050400-rootless-abc1234.zip"
}

test_release_base_url_sed_metacharacters() {
  reset_release_config
  PIXENEOS_RELEASE_BASE_URL="https://releases.example.com/PixeneOS/amp&pipe|segment"
  capture_setup_url \
    "https://releases.example.com/PixeneOS/amp&pipe|segment/shiba-2026050400-rootless-abc1234.zip"
}

test_my_avbroot_setup_source_override() {
  reset_release_config
  PIXENEOS_AVBROOT_SETUP_SOURCE="https://example.com/my-avbroot-setup.git"
  capture_setup_url \
    "https://github.com/0cwa/ModOS/releases/download/2026050400/shiba-2026050400-rootless-abc1234.zip"
}

test_fetcher_source_override() {
  reset_release_config
  PIXENEOS_AVBROOT_SETUP_SOURCE="https://example.com/tools/my-avbroot-setup.git"
  unset SIGNATURE_URL URL

  get() { :; }

  url_constructor "my-avbroot-setup" "false" >/dev/null

  if [[ "${URL}" != "${PIXENEOS_AVBROOT_SETUP_SOURCE}" ]]; then
    echo "Expected my-avbroot-setup URL override ${PIXENEOS_AVBROOT_SETUP_SOURCE}, got ${URL}" >&2
    exit 1
  fi
}

test_fetcher_source_rejects_authenticated_url() {
  local output status=0
  reset_release_config
  PIXENEOS_AVBROOT_SETUP_SOURCE="https://user:secret@example.com/my-avbroot-setup.git"
  output="$(url_constructor "my-avbroot-setup" "false" 2>&1)" || status=$?
  [[ "${status}" -ne 0 ]] || fail 'Authenticated helper source was accepted by the fetch boundary'
  [[ "${output}" == *'authenticated helper repository URLs are not allowed'* ]] ||
    fail 'Authenticated helper source did not produce the generic rejection'
  [[ "${output}" != *'user'* && "${output}" != *'secret'* ]] ||
    fail 'Authenticated helper source was disclosed by the fetch boundary'
}

test_my_avbroot_setup_source_fallback() {
  reset_release_config
  unset SIGNATURE_URL URL

  get() { :; }

  url_constructor "my-avbroot-setup" "false" >/dev/null

  if [[ "${URL}" != "https://github.com/0cwa/my-avbroot-setup" ]]; then
    echo "Expected my-avbroot-setup fallback URL, got ${URL}" >&2
    exit 1
  fi
}

test_project_name_release_fallback() {
  reset_release_config
  PROJECT_NAME=FixtureOS
  capture_setup_url \
    "https://github.com/0cwa/FixtureOS/releases/download/2026050400/shiba-2026050400-rootless-abc1234.zip"
  reset_release_config
  PROJECT_NAME=FixtureOS
  GITHUB_REPOSITORY=fixture/ActualRepository
  capture_setup_url \
    "https://github.com/fixture/ActualRepository/releases/download/2026050400/shiba-2026050400-rootless-abc1234.zip"
  reset_release_config
}

test_project_name_release_fallback
test_default_release_url
test_github_repository_release_url
test_release_base_url_override
test_release_base_url_trailing_slash
test_release_base_url_sed_metacharacters
test_my_avbroot_setup_source_override
test_fetcher_source_override
test_fetcher_source_rejects_authenticated_url
test_my_avbroot_setup_source_fallback
test_toml_env_allowlisted_assignments true
test_toml_env_allowlisted_assignments false
test_toml_env_rejects_unknown_and_malformed_keys

echo "release URL configuration tests passed"
