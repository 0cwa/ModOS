#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -eo pipefail

source src/util_functions.sh

set -u

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

fail() {
  echo "$*" >&2
  exit 1
}

assert_file_equals() {
  local expected="${1}"
  local path="${2}"
  local actual=""

  if [[ -f "${path}" ]]; then
    actual="$(<"${path}")"
  fi
  [[ "${actual}" == "${expected}" ]] ||
    fail "Expected ${path} to contain '${expected}', got '${actual}'"
}

test_executables_are_bootstrapped_once_in_supported_order() (
  local root="${TEST_ROOT}/batch"
  WORKDIR="${root}/work"
  ADDITIONALS[RETRY]="false"
  ADDITIONALS[ROOT]="false"

  make_directories() { mkdir -p "${WORKDIR}"; }
  supported_tools() {
    echo "avbroot disabled-legacy afsr custota-tool enabled-legacy"
  }
  flag_check() {
    case "${1}" in
      disabled-legacy) echo false ;;
      *) echo true ;;
    esac
  }
  bootstrap_executable_tools() { printf '%s\n' "$*" >"${root}/bootstrap"; }
  download_dependencies() { printf '%s\n' "${1}" >>"${root}/downloads"; }
  verify_downloads() { printf '%s\n' "${1}" >>"${root}/verified"; }

  mkdir -p "${root}"
  check_and_download_dependencies >/dev/null

  assert_file_equals "avbroot afsr custota-tool" "${root}/bootstrap"
  assert_file_equals "enabled-legacy" "${root}/downloads"
  assert_file_equals "enabled-legacy" "${root}/verified"
)

test_disabled_executables_are_not_bootstrapped() (
  local root="${TEST_ROOT}/disabled-bootstrap"
  WORKDIR="${root}/work"
  ADDITIONALS[RETRY]="false"
  ADDITIONALS[ROOT]="false"

  make_directories() { mkdir -p "${WORKDIR}"; }
  supported_tools() { echo "avbroot afsr custota-tool"; }
  flag_check() { echo false; }
  bootstrap_executable_tools() { touch "${root}/bootstrap"; }
  download_dependencies() { touch "${root}/legacy-download"; }
  verify_downloads() { touch "${root}/legacy-verify"; }

  mkdir -p "${root}"
  check_and_download_dependencies >/dev/null

  [[ ! -e "${root}/bootstrap" ]] || fail "Disabled executable was bootstrapped"
  [[ ! -e "${root}/legacy-download" ]] ||
    fail "Disabled executable reached legacy acquisition"
  [[ ! -e "${root}/legacy-verify" ]] ||
    fail "Disabled executable reached legacy verification"
)

test_bootstrap_failure_stops_before_legacy_acquisition() (
  local root="${TEST_ROOT}/bootstrap-failure"
  local result
  WORKDIR="${root}/work"
  ADDITIONALS[RETRY]="false"
  ADDITIONALS[ROOT]="false"

  make_directories() { mkdir -p "${WORKDIR}"; }
  supported_tools() { echo "avbroot enabled-legacy"; }
  flag_check() { echo true; }
  bootstrap_executable_tools() { return 42; }
  download_dependencies() { touch "${root}/legacy-download"; }
  verify_downloads() { touch "${root}/legacy-verify"; }

  mkdir -p "${root}"
  set +e
  check_and_download_dependencies >/dev/null
  result=$?
  set -e

  [[ "${result}" -ne 0 ]] || fail "Bootstrap failure was reported as success"
  [[ ! -e "${root}/legacy-download" ]] ||
    fail "Legacy acquisition ran after bootstrap failure"
  [[ ! -e "${root}/legacy-verify" ]] ||
    fail "Legacy verification ran after bootstrap failure"
)

test_env_setup_resolves_only_enabled_executables() (
  local root="${TEST_ROOT}/enabled-env"
  local old_path="/usr/local/bin:/usr/bin"
  local avb_digest="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  local custota_digest="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  WORKDIR="${root}/work"
  PATH="${old_path}"
  ADDITIONALS[AVBROOT]="true"
  ADDITIONALS[AFSR]="false"
  ADDITIONALS[CUSTOTA_TOOL]="true"
  PIXENEOS_AFSR_BIN="stale-disabled-value"

  my_avbroot_setup() { :; }
  enable_venv() { :; }
  resolve_executable_tool() {
    printf '%s\n' "${1}" >>"${root}/resolved"
    case "${1}" in
      avbroot) echo "/opt/pixene/by-sha256/${avb_digest}/avbroot" ;;
      custota-tool) echo "/opt/pixene/by-sha256/${custota_digest}/custota-tool" ;;
      *) return 99 ;;
    esac
  }

  mkdir -p "${WORKDIR}/tools/my-avbroot-setup"
  env_setup >/dev/null

  assert_file_equals $'avbroot\ncustota-tool' "${root}/resolved"
  [[ "${PIXENEOS_AVBROOT_BIN}" == "/opt/pixene/by-sha256/${avb_digest}/avbroot" ]] ||
    fail "Enabled avbroot was not exported"
  [[ "${PIXENEOS_CUSTOTA_TOOL_BIN}" == "/opt/pixene/by-sha256/${custota_digest}/custota-tool" ]] ||
    fail "Enabled custota-tool was not exported"
  [[ -z "${PIXENEOS_AFSR_BIN+x}" ]] || fail "Disabled afsr export was retained"
  [[ "${PATH}" == "/opt/pixene/by-sha256/${avb_digest}:/opt/pixene/by-sha256/${custota_digest}:${old_path}" ]] ||
    fail "PATH does not contain only enabled digest-bound directories: ${PATH}"
)

test_env_setup_resolve_failure_is_transactional() (
  local root="${TEST_ROOT}/resolve-failure"
  local old_path="/caller/bin:/usr/bin"
  local old_prefix="/old/pixene/avbroot:/old/pixene/afsr"
  local result
  WORKDIR="${root}/work"
  PATH="${old_prefix}:${old_path}"
  ADDITIONALS[AVBROOT]="true"
  ADDITIONALS[AFSR]="true"
  ADDITIONALS[CUSTOTA_TOOL]="false"
  PIXENEOS_AVBROOT_BIN="/old/pixene/avbroot/avbroot"
  PIXENEOS_AFSR_BIN="/old/pixene/afsr/afsr"
  PIXENEOS_CUSTOTA_TOOL_BIN="stale-disabled-value"
  PIXENEOS_EXECUTABLE_PATH_PREFIX="${old_prefix}"
  PIXENEOS_EXECUTABLE_BASE_PATH="${old_path}"

  my_avbroot_setup() { touch "${root}/helper-mutated"; }
  enable_venv() { touch "${root}/venv-enabled"; }
  resolve_executable_tool() {
    case "${1}" in
      avbroot) echo "/opt/pixene/by-sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/avbroot" ;;
      *) return 55 ;;
    esac
  }

  mkdir -p "${WORKDIR}/tools/my-avbroot-setup"
  set +e
  env_setup >/dev/null
  result=$?
  set -e

  [[ "${result}" -ne 0 ]] || fail "Resolve failure was reported as success"
  [[ "${PATH}" == "${old_path}" ]] || fail "Stale injected PATH survived resolve failure"
  [[ -z "${PIXENEOS_AVBROOT_BIN+x}" ]] ||
    fail "Stale avbroot export survived resolve failure"
  [[ -z "${PIXENEOS_AFSR_BIN+x}" ]] || fail "Stale afsr export survived resolve failure"
  [[ -z "${PIXENEOS_CUSTOTA_TOOL_BIN+x}" ]] ||
    fail "Stale custota-tool export survived resolve failure"
  [[ -z "${PIXENEOS_EXECUTABLE_PATH_PREFIX+x}" ]] ||
    fail "Stale injected PATH tracking survived resolve failure"
  [[ ! -e "${root}/helper-mutated" ]] ||
    fail "Helper source was mutated before all executable resolution succeeded"
  [[ ! -e "${root}/venv-enabled" ]] || fail "Environment setup continued after resolve failure"
)

test_repeated_env_setup_removes_disabled_tool_bindings() (
  local root="${TEST_ROOT}/repeated-env"
  local old_path="/caller/bin:/usr/bin"
  local avb_digest="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  local afsr_digest="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  WORKDIR="${root}/work"
  PATH="${old_path}"
  ADDITIONALS[AVBROOT]="true"
  ADDITIONALS[AFSR]="true"
  ADDITIONALS[CUSTOTA_TOOL]="false"

  my_avbroot_setup() { :; }
  enable_venv() { :; }
  resolve_executable_tool() {
    case "${1}" in
      avbroot) echo "/opt/pixene/by-sha256/${avb_digest}/avbroot" ;;
      afsr) echo "/opt/pixene/by-sha256/${afsr_digest}/afsr" ;;
      *) return 99 ;;
    esac
  }

  mkdir -p "${WORKDIR}/tools/my-avbroot-setup"
  env_setup >/dev/null
  [[ "${PATH}" == "/opt/pixene/by-sha256/${avb_digest}:/opt/pixene/by-sha256/${afsr_digest}:${old_path}" ]] ||
    fail "Initial executable PATH was not constructed as expected"

  PATH="${PATH}:/caller/added-after-setup"
  ADDITIONALS[AFSR]="false"
  env_setup >/dev/null

  [[ -z "${PIXENEOS_AFSR_BIN+x}" ]] || fail "Disabled afsr export survived repeated setup"
  [[ "${PATH}" == "/opt/pixene/by-sha256/${avb_digest}:${old_path}:/caller/added-after-setup" ]] ||
    fail "Repeated setup retained or duplicated an injected tool directory: ${PATH}"
  [[ "${PIXENEOS_EXECUTABLE_BASE_PATH}" == "${old_path}:/caller/added-after-setup" ]] ||
    fail "Caller base PATH was not preserved"
)

test_runner_constructs_exact_fd_bound_command() (
  local root="${TEST_ROOT}/runner-command"
  WORKDIR="${root}/work"
  local repository_root="$(pwd -P)"

  mkdir -p "${root}"
  python3() { printf '%s\n' "$*" >"${root}/python-args"; }
  run_executable_tool avbroot key generate-key -o "/output/key"

  assert_file_equals \
    "${repository_root}/src/bootstrap_executable_tools.py --workdir ${WORKDIR} run avbroot -- key generate-key -o /output/key" \
    "${root}/python-args"
)

test_runner_failure_prevents_generate_keys_fallback_execution() (
  local root="${TEST_ROOT}/generate-keys"
  local result
  WORKDIR="${root}/work"
  PATH="${root}/bin:/usr/bin"

  mkdir -p "${root}/bin"
  printf '#!/usr/bin/env bash\ntouch %q\n' "${root}/executed" >"${root}/bin/avbroot"
  chmod 0755 "${root}/bin/avbroot"
  run_executable_tool() { return 66; }
  base64_encode() { touch "${root}/base64-ran"; }

  set +e
  generate_keys >/dev/null 2>&1
  result=$?
  set -e

  [[ "${result}" -ne 0 ]] || fail "Runner failure was reported as success"
  [[ ! -e "${root}/executed" ]] || fail "PATH fallback ran after runner failure"
  [[ ! -e "${root}/base64-ran" ]] || fail "Key processing continued after runner failure"
)

test_pixene_avbroot_calls_preserve_exact_arguments() (
  local root="${TEST_ROOT}/runner-arguments"
  WORKDIR="${root}/work"
  GRAPHENEOS[OTA_TARGET]="official"
  KEYS[AVB]="${root}/keys/avb.key"
  KEYS[OTA]="${root}/keys/ota.key"
  KEYS[PKMD]="${root}/keys/avb_pkmd.bin"
  KEYS[CERT_OTA]="${root}/keys/ota.crt"

  run_executable_tool() {
    printf '%s\n' "$*" >>"${root}/runner-args"
    if [[ "${1} ${2} ${3}" == "avbroot ota extract" ]]; then
      # The current structured extractor writes the root vbmeta key directly.
      printf 'fixture-root-vbmeta-public-key' >"${WORKDIR}/extracted/avb_pkmd.bin"
    fi
  }
  base64_encode() { :; }
  unzip() { :; }

  mkdir -p "${WORKDIR}/extracted/extracts"
  generate_keys >/dev/null
  extract_official_keys >/dev/null

  assert_file_equals \
    "$(printf '%s\n' \
      "avbroot key generate-key -o ${KEYS[AVB]}" \
      "avbroot key generate-key -o ${KEYS[OTA]}" \
      "avbroot key extract-avb -k ${KEYS[AVB]} -o ${KEYS[PKMD]}" \
      "avbroot key generate-cert -k ${KEYS[OTA]} -o ${KEYS[CERT_OTA]}" \
      "avbroot ota extract --input ${WORKDIR}/official.zip --directory ${WORKDIR}/extracted/extracts --all --public-key-avb ${WORKDIR}/extracted/avb_pkmd.bin")" \
    "${root}/runner-args"
)

test_legacy_acquisition_rejects_locked_executables() (
  local root="${TEST_ROOT}/legacy-guard"
  WORKDIR="${root}/work"
  INTERACTIVE_MODE="false"
  get() { touch "${root}/legacy-get"; }

  mkdir -p "${root}"
  local tool
  for tool in avbroot afsr custota-tool; do
    if url_constructor "${tool}" false >/dev/null 2>&1; then
      fail "Legacy URL construction accepted ${tool}"
    fi
  done
  [[ ! -e "${root}/legacy-get" ]] || fail "Legacy get ran for locked executable"
)

test_direct_legacy_get_and_verify_reject_locked_executables() (
  local root="${TEST_ROOT}/direct-legacy-guards"
  WORKDIR="${root}/work"

  mkdir -p "${WORKDIR}/modules" "${WORKDIR}/signatures" "${WORKDIR}/tools"
  local tool
  for tool in avbroot afsr custota-tool; do
    if get "${tool}" "https://fixtures.invalid/${tool}.zip" \
      "https://fixtures.invalid/${tool}.zip.sig" >/dev/null 2>&1; then
      fail "Direct legacy get accepted ${tool}"
    fi
    if verify_downloads "${tool}" >/dev/null 2>&1; then
      fail "Legacy verification accepted ${tool}"
    fi
  done
  [[ -z "$(find "${WORKDIR}" -type f -print -quit)" ]] ||
    fail "Direct legacy guards created an executable artifact"
)

test_executables_are_bootstrapped_once_in_supported_order
test_disabled_executables_are_not_bootstrapped
test_bootstrap_failure_stops_before_legacy_acquisition
test_env_setup_resolves_only_enabled_executables
test_env_setup_resolve_failure_is_transactional
test_repeated_env_setup_removes_disabled_tool_bindings
test_runner_constructs_exact_fd_bound_command
test_runner_failure_prevents_generate_keys_fallback_execution
test_pixene_avbroot_calls_preserve_exact_arguments
test_legacy_acquisition_rejects_locked_executables
test_direct_legacy_get_and_verify_reject_locked_executables

echo "executable tool routing tests passed"
