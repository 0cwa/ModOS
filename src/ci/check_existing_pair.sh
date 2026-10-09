#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Run the existing single-variant preflight for rootless first, then Magisk
# only when needed. Build the pair when either exact asset triplet is missing.
# This deliberately reuses check_existing_build.sh rather than duplicating its
# release/selection validation rules.

set -o nounset -o pipefail -o errexit

die() {
  echo "::error::$*" >&2
  return 1
}

read_should_build() {
  local output_file="${1}"
  local value
  value="$(awk -F= '$1 == "should_build" { value=$2 } END { print value }' "${output_file}")"
  case "${value}" in
    true|false) printf '%s' "${value}" ;;
    *) die "Variant preflight did not emit a valid should_build result" ;;
  esac
}

run_variant() {
  local root="${1}"
  local expected_variant="${2}"
  local output_file env_file result

  [[ "${expected_variant}" =~ ^[0-9a-f]{64}$ ]] ||
    die "Paired preflight received an invalid selection fingerprint"

  output_file="$(mktemp)"
  env_file="$(mktemp)"
  ROOT="${root}" \
  EXPECTED_VARIANT="${expected_variant}" \
  GITHUB_OUTPUT="${output_file}" \
  GITHUB_ENV="${env_file}" \
    bash "${CHECK_EXISTING_BUILD_SCRIPT:-src/ci/check_existing_build.sh}"

  result="$(read_should_build "${output_file}")"
  if [[ -n "${GITHUB_ENV:-}" ]] && grep -Fxq 'FORCE_REBUILD=true' "${env_file}"; then
    grep -Fxq 'FORCE_REBUILD=true' "${GITHUB_ENV}" 2>/dev/null ||
      echo 'FORCE_REBUILD=true' >>"${GITHUB_ENV}"
  fi
  rm -f -- "${output_file}" "${env_file}"
  printf '%s' "${result}"
}

expected_rootless="${EXPECTED_VARIANT_ROOTLESS:-}"
expected_magisk="${EXPECTED_VARIANT_MAGISK:-}"

if [[ ! "${expected_rootless}" =~ ^[0-9a-f]{64}$ ||
  ! "${expected_magisk}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Paired selection identity is unavailable; continuing with a build."
  [[ -n "${GITHUB_OUTPUT:-}" ]] && echo 'should_build=true' >>"${GITHUB_OUTPUT}"
  exit 0
fi

rootless_result="$(run_variant false "${expected_rootless}")"
if [[ "${rootless_result}" == true ]]; then
  echo "Rootless variant requires a build; paired build will produce both variants."
  [[ -n "${GITHUB_OUTPUT:-}" ]] && echo 'should_build=true' >>"${GITHUB_OUTPUT}"
  exit 0
fi

magisk_result="$(run_variant true "${expected_magisk}")"
if [[ "${magisk_result}" == true ]]; then
  echo "Magisk variant requires a build; paired build will produce both variants."
  [[ -n "${GITHUB_OUTPUT:-}" ]] && echo 'should_build=true' >>"${GITHUB_OUTPUT}"
  exit 0
fi

echo "Both exact rootless and Magisk asset triplets already exist. Skipping paired build."
[[ -n "${GITHUB_OUTPUT:-}" ]] && echo 'should_build=false' >>"${GITHUB_OUTPUT}"
