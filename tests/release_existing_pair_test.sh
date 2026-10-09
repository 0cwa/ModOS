#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

fail() {
  echo "$*" >&2
  exit 1
}

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT

FAKE_CHECKER="${TEST_ROOT}/fake-check-existing.sh"
cat >"${FAKE_CHECKER}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "${ROOT}" >>"${CALL_LOG}"
case "${ROOT}" in
  false) result="${ROOTLESS_RESULT}" ;;
  true) result="${MAGISK_RESULT}" ;;
  *) exit 90 ;;
esac
echo "should_build=${result}" >>"${GITHUB_OUTPUT}"
EOF
chmod +x "${FAKE_CHECKER}"

hash_a="$(printf 'a%.0s' {1..64})"
hash_b="$(printf 'b%.0s' {1..64})"

run_case() {
  local name="${1}"
  local rootless_result="${2}"
  local magisk_result="${3}"
  local expected_result="${4}"
  local expected_calls="${5}"
  local out="${TEST_ROOT}/${name}.out"
  local env_file="${TEST_ROOT}/${name}.env"
  local calls="${TEST_ROOT}/${name}.calls"

  : >"${out}"
  : >"${env_file}"
  : >"${calls}"

  ROOTLESS_RESULT="${rootless_result}" \
  MAGISK_RESULT="${magisk_result}" \
  CALL_LOG="${calls}" \
  CHECK_EXISTING_BUILD_SCRIPT="${FAKE_CHECKER}" \
  EXPECTED_VARIANT_ROOTLESS="${hash_a}" \
  EXPECTED_VARIANT_MAGISK="${hash_b}" \
  GITHUB_OUTPUT="${out}" \
  GITHUB_ENV="${env_file}" \
    bash src/ci/check_existing_pair.sh >/dev/null

  grep -Fxq "should_build=${expected_result}" "${out}" ||
    fail "${name}: wrong paired should_build result"

  actual_calls="$(wc -l <"${calls}" | tr -d ' ')"
  [[ "${actual_calls}" == "${expected_calls}" ]] ||
    fail "${name}: expected ${expected_calls} checker calls, got ${actual_calls}"
}

run_case both-present false false false 2
run_case rootless-missing true false true 1
run_case magisk-missing false true true 2

out="${TEST_ROOT}/unresolved.out"
calls="${TEST_ROOT}/unresolved.calls"
: >"${out}"
: >"${calls}"
ROOTLESS_RESULT=false \
MAGISK_RESULT=false \
CALL_LOG="${calls}" \
CHECK_EXISTING_BUILD_SCRIPT="${FAKE_CHECKER}" \
EXPECTED_VARIANT_ROOTLESS='' \
EXPECTED_VARIANT_MAGISK='' \
GITHUB_OUTPUT="${out}" \
GITHUB_ENV="${TEST_ROOT}/unresolved.env" \
  bash src/ci/check_existing_pair.sh >/dev/null

grep -Fxq 'should_build=true' "${out}" ||
  fail "unresolved identities did not force a build"
[[ ! -s "${calls}" ]] ||
  fail "unresolved identities unexpectedly queried release assets"

echo "paired existing-build preflight tests passed"
