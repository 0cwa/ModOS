#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="${ROOT_DIR}/src/ci/publish_ota.sh"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT

TOKEN='0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
OTHER_TOKEN='abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'
REAL_GIT="$(command -v git)"

fail() {
  echo "$*" >&2
  exit 1
}

setup_case() {
  local name="${1}"
  local case_root="${TEST_ROOT}/${name}"
  local repo="${case_root}/repo"
  local remote="${case_root}/remote.git"
  local bin="${case_root}/bin"

  mkdir -p -- "${bin}"
  git init -q --bare "${remote}"
  git init -q "${repo}"
  git -C "${repo}" checkout -q -b main
  git -C "${repo}" config user.email fixture@example.invalid
  git -C "${repo}" config user.name fixture
  printf 'main\n' >"${repo}/tracked"
  git -C "${repo}" add tracked
  git -C "${repo}" commit -q -m main
  git -C "${repo}" checkout -q -b gh-pages
  mkdir -p -- "${repo}/rootless" "${repo}/variants/grapheneos/rootless"
  printf 'old\n' >"${repo}/rootless/bramble.json"
  printf 'old\n' >"${repo}/variants/grapheneos/rootless/bramble-${TOKEN}.json"
  git -C "${repo}" add rootless variants
  git -C "${repo}" commit -q -m gh-pages
  git -C "${repo}" remote add origin "${remote}"
  git -C "${repo}" push -q origin gh-pages
  git -C "${repo}" checkout -q main

  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    '[[ "${1-}" == release && "${2-}" == view ]] || exit 2' \
    'echo release-assets-verified >>"${PUBLISH_LOG}"' \
    'printf "%s\\n" "${PUBLISH_ASSETS}"' >"${bin}/gh"
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    'if [[ "${1-}" == checkout && "${2-}" == gh-pages ]]; then echo checkout-gh-pages >>"${PUBLISH_LOG}"; fi' \
    'exec "${REAL_GIT}" "$@"' >"${bin}/git"
  chmod 0700 -- "${bin}/gh" "${bin}/git"

  printf '%s\n' "${case_root}"
}

write_inputs() {
  local case_root="${1}"
  local fingerprint="${2:-${TOKEN}}"
  local artifact="bramble-20260911-rootless-${TOKEN}-deadbeef.zip"
  local repo="${case_root}/repo"

  printf '{"ota":"fixture"}\n' >"${repo}/bramble.json"
  printf 'ota\n' >"${repo}/${artifact}"
  printf 'signature\n' >"${repo}/${artifact}.csig"
  printf '{\n  "artifact_name": "%s",\n  "device": "bramble",\n  "grapheneos_version": "20260911",\n  "module_selection_fingerprint": "%s",\n  "output_scope": "published",\n  "rom_family": "grapheneos",\n  "schema_version": 2\n}\n' \
    "${artifact}" "${fingerprint}" >"${repo}/${artifact}.selection.json"
}

assets_for_fixture() {
  local artifact="bramble-20260911-rootless-${TOKEN}-deadbeef.zip"
  printf '%s\n' "${artifact}" "${artifact}.csig" "${artifact}.selection.json"
}

run_helper() {
  local case_root="${1}"
  local assets="${2}"
  local expected_status="${3}"
  local identity_mode="${4:-configured}"
  local repo="${case_root}/repo"
  local bin="${case_root}/bin"
  local log="${case_root}/operations.log"
  local status=0
  local -a env_args=(
    "PATH=${bin}:${PATH}"
    "REAL_GIT=${REAL_GIT}"
    "PUBLISH_LOG=${log}"
    "PUBLISH_ASSETS=${assets}"
    "ROM_FAMILY=grapheneos"
    "ROM_PROFILE_PROVIDER=grapheneos"
    "ROM_FLAVOR=rootless"
    "DEVICE_NAME=bramble"
    "MODULE_SELECTION_FINGERPRINT=${TOKEN}"
    "GRAPHENEOS_VERSION=20260911"
    "RELEASE_TYPE=default"
    "OUTPUT_SCOPE=published"
    "OTA_ARTIFACT_PATH=bramble-20260911-rootless-${TOKEN}-deadbeef.zip"
    "SELECTION_METADATA_PATH=bramble-20260911-rootless-${TOKEN}-deadbeef.zip.selection.json"
    "OTA_METADATA_PATH=bramble.json"
  )

  case "${identity_mode}" in
    configured)
      env_args+=(
        'GIT_COMMIT_EMAIL=fixture@example.invalid'
        'GIT_COMMIT_NAME=fixture'
      )
      ;;
    unset)
      ;;
    *)
      fail "unsupported identity mode: ${identity_mode}"
      ;;
  esac

  : >"${log}"
  set +e
  (
    cd "${repo}"
    if [[ "${identity_mode}" == unset ]]; then
      env -u GIT_COMMIT_EMAIL -u GIT_COMMIT_NAME \
        "${env_args[@]}" bash "${HELPER}" >"${case_root}/stdout" 2>"${case_root}/stderr"
    else
      env "${env_args[@]}" bash "${HELPER}" >"${case_root}/stdout" 2>"${case_root}/stderr"
    fi
  ) || status=$?
  set -e

  if [[ "${expected_status}" == nonzero ]]; then
    [[ "${status}" -ne 0 ]] || fail "expected a publication failure"
    return 0
  fi
  [[ "${status}" -eq "${expected_status}" ]] || {
    cat "${case_root}/stdout" "${case_root}/stderr" >&2
    fail "expected status ${expected_status}, got ${status}"
  }
}

test_workflow_contract() {
  local workflow="${ROOT_DIR}/.github/workflows/build-rom.yml"
  local release_line helper_line

  release_line="$(awk '/uses: softprops\/action-gh-release@/ { line = NR } END { print line }' "${workflow}")"
  helper_line="$(grep -n 'bash src/ci/publish_ota.sh' "${workflow}" | cut -d: -f1)"
  (( release_line < helper_line )) || fail "release assets must precede OTA publication"
  grep -Fq 'group: gh-pages' "${workflow}" || fail "gh-pages serialization changed"
  grep -Fq 'cancel-in-progress: false' "${workflow}" || fail "gh-pages cancellation changed"
  for field in ROM_PROFILE_PROVIDER ROM_FLAVOR OTA_ARTIFACT_PATH \
    OTA_METADATA_PATH SELECTION_METADATA_PATH; do
    grep -Fq "${field}:" "${workflow}" || fail "workflow does not pass ${field}"
  done
}

test_success_order_and_paths() {
  local case_root log verified checkout
  case_root="$(setup_case success)"
  write_inputs "${case_root}"
  run_helper "${case_root}" "$(assets_for_fixture)" 0
  log="${case_root}/operations.log"
  verified="$(grep -n '^release-assets-verified$' "${log}" | cut -d: -f1)"
  checkout="$(grep -n '^checkout-gh-pages$' "${log}" | cut -d: -f1)"
  (( verified < checkout )) || fail "gh-pages checkout preceded asset verification"
  diff -u "${case_root}/repo/bramble.json" "${case_root}/repo/rootless/bramble.json"
  diff -u "${case_root}/repo/bramble.json" \
    "${case_root}/repo/variants/grapheneos/rootless/bramble-${TOKEN}.json"
  grep -Fq 'Published OTA metadata' "${case_root}/stdout" || fail "success was not reported"
}

test_missing_asset() {
  local case_root artifact
  case_root="$(setup_case missing-asset)"
  write_inputs "${case_root}"
  artifact="bramble-20260911-rootless-${TOKEN}-deadbeef.zip"
  run_helper "${case_root}" "${artifact}"$'\n'"${artifact}.selection.json" 1
  ! grep -Fq 'checkout-gh-pages' "${case_root}/operations.log" ||
    fail "missing asset reached gh-pages checkout"
}

test_wrong_variant() {
  local case_root
  case_root="$(setup_case wrong-variant)"
  write_inputs "${case_root}" "${OTHER_TOKEN}"
  run_helper "${case_root}" "$(assets_for_fixture)" 1
  [[ ! -s "${case_root}/operations.log" ]] || fail "wrong variant reached asset verification"
}

test_publication_failure() {
  local case_root repo remote
  case_root="$(setup_case publication-failure)"
  write_inputs "${case_root}"
  repo="${case_root}/repo"
  remote="${case_root}/remote.git"
  git -C "${repo}" remote set-url origin "${case_root}/missing-remote.git"
  run_helper "${case_root}" "$(assets_for_fixture)" nonzero
  [[ "$(git --git-dir "${remote}" show refs/heads/gh-pages:rootless/bramble.json)" == old ]] ||
    fail "failed publication changed the remote OTA pointer"
  ! grep -Fq 'Published OTA metadata' "${case_root}/stdout" || fail "failure reported success"
}

test_missing_git_identity() {
  local case_root remote before_tree after_tree
  case_root="$(setup_case missing-git-identity)"
  write_inputs "${case_root}"
  remote="${case_root}/remote.git"
  before_tree="$(git --git-dir "${remote}" rev-parse refs/heads/gh-pages^{tree})"

  run_helper "${case_root}" "$(assets_for_fixture)" nonzero unset

  after_tree="$(git --git-dir "${remote}" rev-parse refs/heads/gh-pages^{tree})"
  [[ "${after_tree}" == "${before_tree}" ]] ||
    fail "missing Git identity advanced the remote OTA tree"
  [[ "$(git --git-dir "${remote}" show refs/heads/gh-pages:rootless/bramble.json)" == old ]] ||
    fail "missing Git identity changed the remote OTA pointer"
  [[ "$(git --git-dir "${remote}" show refs/heads/gh-pages:variants/grapheneos/rootless/bramble-${TOKEN}.json)" == old ]] ||
    fail "missing Git identity changed the remote variant metadata"
  ! grep -Fq 'Published OTA metadata' "${case_root}/stdout" ||
    fail "missing Git identity reported success"
}

test_local_unpublished_noop() {
  local case_root repo
  case_root="$(setup_case local-unpublished)"
  write_inputs "${case_root}"
  repo="${case_root}/repo"
  : >"${case_root}/operations.log"
  (
    cd "${repo}"
    PATH="${case_root}/bin:${PATH}" PUBLISH_LOG="${case_root}/operations.log" \
      ROM_FAMILY='grapheneos' ROM_PROFILE_PROVIDER='grapheneos' ROM_FLAVOR='rootless' \
      DEVICE_NAME='bramble' MODULE_SELECTION_FINGERPRINT="${TOKEN}" \
      GRAPHENEOS_VERSION='20260911' RELEASE_TYPE='build-only' OUTPUT_SCOPE='local-unpublished' \
      bash "${HELPER}" >"${case_root}/stdout" 2>"${case_root}/stderr"
  )
  [[ "$(git -C "${repo}" branch --show-current)" == main ]] || fail "no-op changed branch"
  [[ ! -s "${case_root}/operations.log" ]] || fail "no-op contacted publication tooling"
  [[ ! -e "${repo}/rootless/bramble.json" ||
    "$(cat "${repo}/rootless/bramble.json")" != '{"ota":"fixture"}' ]] ||
    fail "no-op advanced OTA metadata"
}

test_workflow_contract
test_success_order_and_paths
test_missing_asset
test_wrong_variant
test_publication_failure
test_missing_git_identity
test_local_unpublished_noop

echo "Publication helper tests passed"
