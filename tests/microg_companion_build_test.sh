#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Offline checks for the patched microG Companion build: the patches are
# well-formed and scoped, and the build script handles credentials safely. The
# real build needs the Android SDK and network; CI qualifies this PR branch
# with a disposable signer and retains only non-installable evidence.
set -euo pipefail

script='tools/build-microg-companion.sh'
patch_dir='patches/microg'
fixture_root="$(mktemp -d)"
trap 'rm -rf "${fixture_root}"' EXIT

fail() {
  echo "Error: $*" >&2
  exit 1
}

mapfile -t patches < <(find "${patch_dir}" -maxdepth 1 -type f -name '*.patch' | sort)
((${#patches[@]})) || fail "no microG patches are checked in"

# Each patch must be scoped to the Companion module and be a git mail patch.
for patch in "${patches[@]}"; do
  head -n 1 "${patch}" | grep -Eq '^From [0-9a-f]{40} ' || fail "${patch} is not a git mail patch"
  grep -q '^Subject: ' "${patch}" || fail "${patch} has no subject"
  while IFS=$'\t' read -r _ _ path; do
    [[ "${path}" == vending-app/* ]] || fail "${patch} touches ${path}, outside vending-app/"
  done < <(git apply --numstat "${patch}")
done

# Rebuild each patch's pre-image from its own context and prove it applies.
for patch in "${patches[@]}"; do
  work="${fixture_root}/apply-$(basename "${patch}")"
  mkdir -p "${work}"
  python3 - "${patch}" "${work}" <<'PY'
import re, sys
from pathlib import Path

patch, work = Path(sys.argv[1]), Path(sys.argv[2])
current = None
files: dict[str, list[str]] = {}
start = 0
for line in patch.read_text().splitlines():
    if line.startswith("+++ b/"):
        current = line[6:]
        files[current] = []
        start = 0
    elif line.startswith("@@") and current:
        start = int(re.match(r"@@ -(\d+)", line).group(1))
        files[current] += [""] * (start - 1 - len(files[current]))
    elif current and start and line[:1] in (" ", "-"):
        files[current].append(line[1:])
for name, lines in files.items():
    target = work / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")
PY
  git -C "${work}" init -q
  git -C "${work}" apply --check "${PWD}/${patch}" || fail "${patch} does not apply to its own context"
done

# The build script refuses unsafe input before cloning or building anything.
expect_failure() {
  local message="$1"
  shift
  local output status=0
  output="$("$@" 2>&1)" || status=$?
  [[ "${status}" -ne 0 ]] || fail "expected failure: ${message}"
  grep -Fq -- "${message}" <<<"${output}" || fail "wrong error for '${message}': ${output}"
}

# Exercise the actual source checker with real Git index flags and a newline
# in the tracked filename. Git status alone can hide both modifications.
bash -n "${script}"
source <(sed -n '/^check_tracked_microg_source() {$/,/^}$/p' "${script}")
source_work="${fixture_root}/source-check"
mkdir -p "${source_work}"
git -C "${source_work}" init -q
tracked_name=$'tracked\nsource.txt'
printf 'reviewed source\n' >"${source_work}/${tracked_name}"
git -C "${source_work}" add -- "${tracked_name}"
git -C "${source_work}" -c user.name='Source check test' \
  -c user.email='source-check@modos.invalid' commit -qm 'reviewed fixture'
source_commit="$(git -C "${source_work}" rev-parse HEAD)"
source_tree="$(git -C "${source_work}" rev-parse 'HEAD^{tree}')"
check_tracked_microg_source "${source_work}" "${source_commit}" "${source_tree}"
for flag in assume-unchanged skip-worktree; do
  git -C "${source_work}" update-index "--${flag}" -- "${tracked_name}"
  printf 'unreviewed source\n' >"${source_work}/${tracked_name}"
  [[ -z "$(git -C "${source_work}" status --porcelain --untracked-files=no)" ]] ||
    fail "${flag} fixture did not hide the tracked modification"
  expect_failure 'hidden index flag on tracked microG source' \
    check_tracked_microg_source "${source_work}" "${source_commit}" "${source_tree}"
  git -C "${source_work}" update-index "--no-${flag}" -- "${tracked_name}"
  expect_failure 'tracked microG source changed during the build' \
    check_tracked_microg_source "${source_work}" "${source_commit}" "${source_tree}"
  git -C "${source_work}" checkout -- "${tracked_name}"
done
check_tracked_microg_source "${source_work}" "${source_commit}" "${source_tree}"

keystore="${fixture_root}/keystore.p12"
pass_file="${fixture_root}/keystore.pass"
: >"${keystore}"
printf 'fixture\n' >"${pass_file}"
chmod 600 "${pass_file}"

expect_failure 'passwords are not accepted on the command line' \
  "${script}" --password secret
expect_failure 'passwords are not accepted on the command line' \
  "${script}" --ks-pass pass:secret
expect_failure '--keystore, --alias and --pass-file are required' "${script}"
expect_failure 'unknown argument' "${script}" --bogus
expect_failure 'missing value for --output' "${script}" --output

expect_failure 'keystore is not a regular file' \
  "${script}" --keystore "${fixture_root}/missing.p12" --alias a --pass-file "${pass_file}"

ln -s "${keystore}" "${fixture_root}/link.p12"
expect_failure 'keystore is not a regular file' \
  "${script}" --keystore "${fixture_root}/link.p12" --alias a --pass-file "${pass_file}"

ln -s "${pass_file}" "${fixture_root}/link.pass"
expect_failure 'password file is not a regular file' \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${fixture_root}/link.pass"

chmod 644 "${pass_file}"
expect_failure 'must not be accessible to other users' \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${pass_file}"
chmod 600 "${pass_file}"
chmod 640 "${pass_file}"
expect_failure 'must not be accessible to other users' \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${pass_file}"
chmod 600 "${pass_file}"

# A keystore inside the repository (outside .keys/) is refused.
repo_keystore="$(mktemp -p "${PWD}" keystore-test-XXXXXX.p12)"
trap 'rm -f "${repo_keystore}"; rm -rf "${fixture_root}"' EXIT
expect_failure 'keep the keystore out of the repository' \
  "${script}" --keystore "${repo_keystore}" --alias a --pass-file "${pass_file}"

expect_failure 'unrecognized microG release tag' \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${pass_file}" --release 'v1;rm'

expect_failure 'no reviewed source pin' \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${pass_file}" --release v0.0.1
expect_failure 'keep signing credentials outside the output directory' \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${pass_file}" --output "${fixture_root}"
expect_failure 'output must be outside the repository' \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${pass_file}" --output "${PWD}"

# A moved release tag must stop before Gradle/signing. Preserve unrelated
# output/src contents and a previously successful result on that failure.
mkdir -p "${fixture_root}/bin" "${fixture_root}/sdk/build-tools/35.0.0" \
  "${fixture_root}/output/src"
printf 'keep\n' >"${fixture_root}/output/src/sentinel"
printf 'previous verified APK\n' >"${fixture_root}/output/com.android.vending.apk"
export MICROG_TEST_REAL_GIT="$(command -v git)"
cat >"${fixture_root}/bin/git" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == clone ]]; then
  mkdir -p -- "${@: -1}"
elif [[ "${3:-}" == rev-parse && "${4:-}" == HEAD ]]; then
  printf '%040d\n' 0
else
  exec "${MICROG_TEST_REAL_GIT}" "$@"
fi
SH
for tool in apksigner zipalign; do
  printf '#!/usr/bin/env bash\nexit 97\n' >"${fixture_root}/sdk/build-tools/35.0.0/${tool}"
  chmod +x "${fixture_root}/sdk/build-tools/35.0.0/${tool}"
done
chmod +x "${fixture_root}/bin/git"
expect_failure 'release tag no longer resolves to the reviewed source commit' \
  env "PATH=${fixture_root}/bin:${PATH}" "ANDROID_HOME=${fixture_root}/sdk" \
  "${script}" --keystore "${keystore}" --alias a --pass-file "${pass_file}" \
  --output "${fixture_root}/output"
[[ "$(cat "${fixture_root}/output/src/sentinel")" == keep ]] || fail 'removed unrelated source work'
[[ "$(cat "${fixture_root}/output/com.android.vending.apk")" == 'previous verified APK' ]] || fail 'damaged prior output'
[[ -z "$(find "${fixture_root}/output" -name '.build.*' -print)" ]] || fail 'left a failed temporary build'

# The password only ever reaches apksigner as a file reference.
grep -Fq -- '--ks-pass "file:${pass_file}"' "${script}" || fail "apksigner must read the password from a file"
if grep -nE '(^|[[:space:]])set -[a-z]*x|cat[[:space:]]+"?\$\{?pass_file|echo[^#]*\$\{?pass_file' "${script}"; then
  fail "the build script must not trace or print the password file"
fi

echo "microG Companion build checks passed"
