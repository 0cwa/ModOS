#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly HELPER="${REPO_ROOT}/tools/compat/avbroot_setup_compat.py"
readonly PRODUCTION_MANIFEST="${REPO_ROOT}/tools/compat/avbroot_setup_compat.json"
readonly DEFAULT_SOURCE="https://github.com/0cwa/my-avbroot-setup"
readonly PINNED_REVISION="$(sed -n 's/^VERSION\[AVBROOT_SETUP\]="\([0-9a-f]*\)".*/\1/p' "${REPO_ROOT}/src/declarations.sh")"
readonly RAW_SOURCE_BASE="https://raw.githubusercontent.com/0cwa/my-avbroot-setup/${PINNED_REVISION}"
readonly RELEASE_URL="https://releases.example.com/PixeneOS/ota'quoted/build.zip"
readonly TARGETS=(
  patch.py
)
readonly TEST_CACHE="$(mktemp -d)"
trap 'rm -rf -- "${TEST_CACHE}"' EXIT

fail() {
  echo "$*" >&2
  exit 1
}

strict_mode=false
if [[ "${1:-}" == '--strict' ]]; then
  strict_mode=true
  shift
fi
[[ "$#" -eq 0 ]] || fail 'Usage: tests/avbroot_setup_compat_test.sh [--strict]'

network_preflight_unavailable() {
  if [[ "${strict_mode}" == true ]]; then
    fail 'avbroot compatibility strict mode requires the immutable pinned-source preflight'
  fi
  echo 'avbroot compatibility checkout tests skipped: pinned source is unavailable' >&2
  return 77
}

manifest_hashes_match() {
  local raw_root="${1}"
  python3 -B - "${PRODUCTION_MANIFEST}" "${raw_root}" "${TARGETS[@]}" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest_path, raw_root, *targets = sys.argv[1:]
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding='utf-8'))
for relative in targets:
    path = pathlib.Path(raw_root) / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = manifest['targets'][relative]['sha256']
    if digest != expected:
        raise SystemExit(f'{relative}: raw source hash does not match production manifest')
PY
}

fetch_pinned_sources() {
  local raw_root="${TEST_CACHE}/raw" relative
  [[ -e "${TEST_CACHE}/raw-ready" ]] && return 0
  mkdir -p "${raw_root}"

  for relative in "${TARGETS[@]}"; do
    mkdir -p "${raw_root}/$(dirname -- "${relative}")"
    if ! curl --fail --silent --show-error --location --max-time 30 \
      --output "${raw_root}/${relative}" "${RAW_SOURCE_BASE}/${relative}" \
      2>"${TEST_CACHE}/network-error"; then
      network_preflight_unavailable
      return $?
    fi
  done
  manifest_hashes_match "${raw_root}" || return 1

  if ! git clone --quiet --bare --no-tags "${DEFAULT_SOURCE}.git" "${TEST_CACHE}/repo.git" \
    2>"${TEST_CACHE}/git-error"; then
    network_preflight_unavailable
    return $?
  fi
  git --git-dir="${TEST_CACHE}/repo.git" cat-file -e "${PINNED_REVISION}^{commit}" ||
    fail 'Pinned revision was not present in the source repository'
  touch "${TEST_CACHE}/raw-ready"
}

prepare_checkout() {
  local root="${1}" relative
  fetch_pinned_sources
  git clone --quiet --local --no-tags --no-checkout \
    "${TEST_CACHE}/repo.git" "${root}"
  git -C "${root}" sparse-checkout init --no-cone
  git -C "${root}" sparse-checkout set --no-cone -- "${TARGETS[@]}"
  git -C "${root}" checkout --quiet --detach "${PINNED_REVISION}"
  git -C "${root}" remote set-url origin "${DEFAULT_SOURCE}"
  [[ "$(git -C "${root}" rev-parse HEAD)" == "${PINNED_REVISION}" ]] ||
    fail 'Pinned-source checkout has the wrong revision'
  for relative in "${TARGETS[@]}"; do
    cmp -s "${TEST_CACHE}/raw/${relative}" "${root}/${relative}" ||
      fail "Pinned raw source differs from the Git checkout for ${relative}"
  done
}

run_helper() {
  local root="${1}" source="${2:-${DEFAULT_SOURCE}}" manifest="${3:-${PRODUCTION_MANIFEST}}"
  local revision="${4:-${PINNED_REVISION}}"
  python3 -B "${HELPER}" --manifest "${manifest}" --source "${source}" \
    "${root}" "${RELEASE_URL}" "${revision}"
}

snapshot_checkout() {
  local root="${1}" snapshot="${2}" relative
  for relative in "${TARGETS[@]}"; do
    mkdir -p "${snapshot}/$(dirname -- "${relative}")"
    cp -p "${root}/${relative}" "${snapshot}/${relative}"
  done
}

assert_snapshot() {
  local root="${1}" snapshot="${2}" relative
  for relative in "${TARGETS[@]}"; do
    cmp -s "${snapshot}/${relative}" "${root}/${relative}" ||
      fail "Unexpected mutation of ${relative}"
    [[ "$(stat -c '%a' "${snapshot}/${relative}")" == "$(stat -c '%a' "${root}/${relative}")" ]] ||
      fail "Unexpected mode change for ${relative}"
  done
}

assert_python_files_compile() {
  local root="${1}"
  python3 -B - "${root}" <<'PY'
import pathlib
import py_compile
import sys
import tempfile

root = pathlib.Path(sys.argv[1])
with tempfile.TemporaryDirectory() as compiled:
    for index, relative in enumerate((
        'patch.py',
    )):
        py_compile.compile(
            str(root / relative),
            cfile=str(pathlib.Path(compiled) / f'{index}.pyc'),
            doraise=True,
        )
PY
}

assert_patch_url() {
  local root="${1}"
  python3 -B - "${root}/patch.py" "${RELEASE_URL}" <<'PY'
import ast
import pathlib
import sys

path, expected_url = pathlib.Path(sys.argv[1]), sys.argv[2]
tree = ast.parse(path.read_text(encoding='utf-8'))
calls = [
    node for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id == 'external'
    and node.func.attr == 'generate_update_info'
]
assert len(calls) == 1
assert len(calls[0].args) == 2 and not calls[0].keywords
assert isinstance(calls[0].args[1], ast.Constant)
assert calls[0].args[1].value == expected_url
PY
}

test_production_manifest_matches_pin() {
  python3 -B - "${HELPER}" "${PRODUCTION_MANIFEST}" "${PINNED_REVISION}" <<'PY'
import importlib.util
import pathlib
import sys

helper_path, manifest_path, declared_revision = sys.argv[1:]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('compatibility_helper', helper_path)
assert spec is not None and spec.loader is not None
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)
manifest = compat.load_manifest(pathlib.Path(manifest_path))
assert manifest['revision'] == declared_revision
assert manifest['repository'] == 'https://github.com/0cwa/my-avbroot-setup'
assert set(manifest['targets']) == set(compat.TARGET_PATHS)
PY
}

test_repository_normalization() {
  python3 -B - "${HELPER}" <<'PY'
import importlib.util
import sys

helper_path = sys.argv[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('compatibility_helper', helper_path)
assert spec is not None and spec.loader is not None
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)

equivalent = {
    compat.normalized_repository('https://example.com/alternate-helper.git/'),
    compat.normalized_repository('ssh://git@example.com:22/alternate-helper'),
    compat.normalized_repository('git@example.com:alternate-helper.git'),
}
assert len(equivalent) == 1
assert compat.normalized_repository('https://github.com/0CWA/my-avbroot-setup/') == \
    compat.normalized_repository('git@github.com:0cwa/my-avbroot-setup.git')
for value in (
    'https://user:secret@example.com/helper',
    'https://user@example.com/helper',
    'ssh://root@example.com/helper',
):
    try:
        compat.normalized_repository(value)
    except compat.CompatibilityError as error:
        assert 'user' not in str(error)
        assert 'secret' not in str(error)
    else:
        raise SystemExit('authenticated URL was accepted')
PY
}

test_helper_rejects_authenticated_source_without_disclosure() {
  local output status=0
  output="$(python3 -B "${HELPER}" --source 'https://user:secret@example.com/helper' \
    /nonexistent/helper "${RELEASE_URL}" "${PINNED_REVISION}" 2>&1)" || status=$?
  [[ "${status}" -ne 0 ]] || fail 'Authenticated source was accepted'
  [[ "${output}" == *'Authenticated helper repository URLs are not allowed'* ]] ||
    fail 'Authenticated source did not produce the generic rejection'
  [[ "${output}" != *'user'* && "${output}" != *'secret'* ]] ||
    fail 'Authenticated source details were disclosed'
}

test_success_and_idempotence() {
  local tmpdir root snapshot mode_before
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; snapshot="${tmpdir}/after-first"
  prepare_checkout "${root}"
  mode_before="$(stat -c '%a' "${root}/patch.py")"
  run_helper "${root}"
  assert_python_files_compile "${root}"
  assert_patch_url "${root}"
  [[ "$(stat -c '%a' "${root}/patch.py")" == "${mode_before}" ]] ||
    fail 'The staged replacement did not preserve file mode'
  snapshot_checkout "${root}" "${snapshot}"
  run_helper "${root}" || fail 'Second compatibility-helper invocation failed'
  assert_snapshot "${root}" "${snapshot}"
  rm -rf -- "${tmpdir}"
}

test_rejects_revision_mismatch() {
  local tmpdir root snapshot
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; snapshot="${tmpdir}/before"
  prepare_checkout "${root}"; snapshot_checkout "${root}" "${snapshot}"
  if run_helper "${root}" "${DEFAULT_SOURCE}" "${PRODUCTION_MANIFEST}" \
    0000000000000000000000000000000000000000; then
    fail 'Revision mismatch was accepted'
  fi
  assert_snapshot "${root}" "${snapshot}"
  rm -rf -- "${tmpdir}"
}

test_rejects_repository_mismatch() {
  local tmpdir root snapshot
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; snapshot="${tmpdir}/before"
  prepare_checkout "${root}"; git -C "${root}" remote set-url origin https://github.com/example/wrong-helper
  snapshot_checkout "${root}" "${snapshot}"
  if run_helper "${root}"; then fail 'Repository mismatch was accepted'; fi
  assert_snapshot "${root}" "${snapshot}"
  rm -rf -- "${tmpdir}"
}

test_accepts_equivalent_sources() {
  local tmpdir root
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; prepare_checkout "${root}"
  git -C "${root}" remote set-url origin git@github.com:0cwa/my-avbroot-setup.git
  run_helper "${root}" "ssh://git@github.com/0CWA/my-avbroot-setup/"
  assert_patch_url "${root}" || fail 'Equivalent GitHub source was not accepted'
  rm -rf -- "${tmpdir}"
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; prepare_checkout "${root}"
  git -C "${root}" remote set-url origin https://example.com/alternate-helper.git
  run_helper "${root}" ssh://git@example.com:22/alternate-helper
  assert_patch_url "${root}" || fail 'Equivalent custom-host source was not accepted'
  rm -rf -- "${tmpdir}"
}

test_rejects_dirty_and_untracked_files() {
  local tmpdir root snapshot
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; snapshot="${tmpdir}/before"
  prepare_checkout "${root}"; printf '\n# dirty\n' >>"${root}/patch.py"
  snapshot_checkout "${root}" "${snapshot}"
  if run_helper "${root}"; then fail 'Same-HEAD dirty file was accepted'; fi
  assert_snapshot "${root}" "${snapshot}"
  rm -rf -- "${tmpdir}"
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; snapshot="${tmpdir}/before"
  prepare_checkout "${root}"; printf 'unrelated\n' >"${root}/unexpected.txt"
  snapshot_checkout "${root}" "${snapshot}"
  if run_helper "${root}"; then fail 'Untracked file was accepted'; fi
  assert_snapshot "${root}" "${snapshot}"
  rm -rf -- "${tmpdir}"
}

test_rejects_manifest_hash_mismatch() {
  local tmpdir root snapshot manifest
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; snapshot="${tmpdir}/before"; manifest="${tmpdir}/manifest.json"
  prepare_checkout "${root}"; snapshot_checkout "${root}" "${snapshot}"
  python3 -B - "${PRODUCTION_MANIFEST}" "${manifest}" <<'PY'
import json
import pathlib
import sys

source, destination = map(pathlib.Path, sys.argv[1:])
manifest = json.loads(source.read_text(encoding='utf-8'))
manifest['targets']['patch.py']['sha256'] = '0' * 64
destination.write_text(json.dumps(manifest), encoding='utf-8')
PY
  if run_helper "${root}" "${DEFAULT_SOURCE}" "${manifest}"; then
    fail 'Manifest hash mismatch was accepted'
  fi
  assert_snapshot "${root}" "${snapshot}"
  rm -rf -- "${tmpdir}"
}

test_atomic_failure_preserves_state() {
  local tmpdir root snapshot
  tmpdir="$(mktemp -d)"; root="${tmpdir}/helper"; snapshot="${tmpdir}/before"
  prepare_checkout "${root}"; snapshot_checkout "${root}" "${snapshot}"
  python3 -B - "${HELPER}" "${root}" "${PRODUCTION_MANIFEST}" "${RELEASE_URL}" "${PINNED_REVISION}" <<'PY'
import importlib.util
import pathlib
import sys

helper_path, root, manifest, release_url, revision = sys.argv[1:]
spec = importlib.util.spec_from_file_location('compatibility_helper', helper_path)
assert spec is not None and spec.loader is not None
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)
real_replace = compat.os.replace
replace_calls = 0

def fail_on_first_replace(source, destination):
    global replace_calls
    replace_calls += 1
    if replace_calls == 1:
        raise OSError('injected replacement failure')
    return real_replace(source, destination)

compat.os.replace = fail_on_first_replace
try:
    compat.run(
        pathlib.Path(root),
        release_url,
        revision,
        pathlib.Path(manifest),
        'https://github.com/0cwa/my-avbroot-setup',
    )
except compat.CompatibilityError as error:
    assert 'rolled back' in str(error)
else:
    raise SystemExit('injected replacement failure was not observed')
PY
  assert_snapshot "${root}" "${snapshot}"
  rm -rf -- "${tmpdir}"
}

test_production_manifest_matches_pin
test_repository_normalization
test_helper_rejects_authenticated_source_without_disclosure

if fetch_pinned_sources; then
  test_success_and_idempotence
  test_rejects_revision_mismatch
  test_rejects_repository_mismatch
  test_accepts_equivalent_sources
  test_rejects_dirty_and_untracked_files
  test_rejects_manifest_hash_mismatch
  test_atomic_failure_preserves_state
else
  status=$?
  [[ "${status}" -eq 77 ]] || exit "${status}"
  echo 'avbroot compatibility checkout tests skipped because the network-backed pinned checkout is unavailable' >&2
fi

echo 'avbroot setup compatibility tests passed'
