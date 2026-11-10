#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Build the microG Companion (com.android.vending) from the reviewed release tag
# with ModOS's patches applied, then sign it with the owner's key.
#
# The output is NOT the official microG APK: it carries a different signer, so it
# only keeps signature spoofing on a ROM built with MICROG_SPOOF_PATCH and the
# matching certificate. Shipping a new build also requires a reviewed helper lock.
#
# Usage:
#   tools/build-microg-companion.sh \
#     --keystore PATH --alias NAME --pass-file PATH [--output DIR] [--release TAG]
#
# --output defaults to <git dir>/tmp/microg-companion (never committed).
#
# The keystore password is only ever read by apksigner from --pass-file; it is
# never accepted on the command line and never printed.
#
# Requirements: git, Python 3, JDK 17+, and an Android SDK ($ANDROID_HOME) with platform 35
# and build-tools 35.0.0 (licenses accepted by you). Other SDK layouts can be
# supported with MICROG_GRADLE_ARGS (for example "-I my-init.gradle").
# Run it through `agent-heavy --` on shared machines; a release build is large.

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
patch_dir="${repo_root}/patches/microg"
reviewed_release='v0.3.17.252432'
reviewed_revision='32bc8954ff872d0e1a05ddfae81561b6dbecef69'

usage() {
  sed -n '/^# Build the microG/,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//' >&2
  exit "${1:-2}"
}

die() {
  echo "Error: $*" >&2
  exit 1
}

keystore=''
alias_name=''
pass_file=''
output="$(git -C "${repo_root}" rev-parse --absolute-git-dir)/tmp/microg-companion"
release=''

while (($#)); do
  case "$1" in
    --keystore|--alias|--pass-file|--output|--release)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "missing value for $1"
      ;;
  esac
  case "$1" in
    --keystore) keystore="${2:-}"; shift 2 ;;
    --alias) alias_name="${2:-}"; shift 2 ;;
    --pass-file) pass_file="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --release) release="${2:-}"; shift 2 ;;
    --password|--ks-pass|--key-pass|--storepass|--keypass)
      die "passwords are not accepted on the command line; use --pass-file"
      ;;
    -h|--help) usage 0 ;;
    *) echo "Error: unknown argument: $1" >&2; usage ;;
  esac
done

[[ -n "${keystore}" && -n "${alias_name}" && -n "${pass_file}" ]] ||
  { echo "Error: --keystore, --alias and --pass-file are required." >&2; usage; }
[[ -f "${keystore}" && ! -L "${keystore}" ]] || die "keystore is not a regular file: ${keystore}"
[[ -f "${pass_file}" && ! -L "${pass_file}" ]] || die "password file is not a regular file: ${pass_file}"
if [[ -n "$(find "${pass_file}" -maxdepth 0 -perm /g+rwx,o+rwx -print)" ]]; then
  die "password file must not be accessible to other users: ${pass_file}"
fi
keystore="$(realpath -e -- "${keystore}")"
pass_file="$(realpath -e -- "${pass_file}")"
output="$(realpath -m -- "${output}")"
git_dir="$(git -C "${repo_root}" rev-parse --absolute-git-dir)"
for credential in "${keystore}" "${pass_file}"; do
  case "${credential}" in
    "${repo_root}/.keys/"*) ;;
    "${repo_root}/"*) die "keep the keystore out of the repository (.keys/ is the only allowed place); the same applies to passwords" ;;
  esac
  [[ "${credential}" != "${output}" && "${credential}" != "${output}/"* ]] ||
    die "keep signing credentials outside the output directory"
done
case "${output}/" in
  "${git_dir}/tmp/"*) ;;
  "${repo_root}/"*) die "output must be outside the repository or under its Git tmp directory" ;;
esac

if [[ -z "${release}" ]]; then
  release="$(sed -n 's/^VERSION\[MICROG\]="\(v[0-9.]*\)".*/\1/p' "${repo_root}/src/declarations.sh")"
fi
[[ "${release}" =~ ^v[0-9]+(\.[0-9]+)+$ ]] || die "unrecognized microG release tag: ${release:-<empty>}"
[[ "${release}" == "${reviewed_release}" ]] || die "microG release has no reviewed source pin: ${release}"
[[ "${GRADLE_MICROG_VERSION_WITHOUT_GIT:-0}" != 1 ]] ||
  die "GRADLE_MICROG_VERSION_WITHOUT_GIT would change the reviewed version identity"

: "${ANDROID_HOME:?ANDROID_HOME must point at an Android SDK}"
[[ -d "${ANDROID_HOME}/build-tools" ]] || die "no build-tools in ANDROID_HOME=${ANDROID_HOME}"
ANDROID_HOME="$(realpath -e -- "${ANDROID_HOME}")"
build_tools="${ANDROID_HOME}/build-tools/35.0.0"
[[ -x "${build_tools}/apksigner" && -x "${build_tools}/zipalign" ]] ||
  die "apksigner/zipalign not found in ${build_tools}"

mapfile -t patches < <(find "${patch_dir}" -maxdepth 1 -type f -name '*.patch' | sort)
((${#patches[@]})) || die "no patches found in ${patch_dir}"

mkdir -p -- "${output}"
# Only remove a directory created by this invocation. A caller's output/src
# may contain unrelated work, and must never be recursively deleted.
build_dir="$(mktemp -d "${output}/.build.XXXXXXXX")"
trap 'rm -rf -- "${build_dir}"' EXIT
work="${build_dir}/src"

echo "Cloning microG GmsCore ${release}..."
git clone -q --depth 1 --branch "${release}" https://github.com/microg/GmsCore.git "${work}"
[[ "$(git -C "${work}" rev-parse HEAD)" == "${reviewed_revision}" ]] ||
  die "release tag no longer resolves to the reviewed source commit"

echo "Applying ${#patches[@]} patch(es)..."
for patch in "${patches[@]}"; do
  git -C "${work}" -c user.name='ModOS build' -c user.email='build@modos.invalid' \
    am --quiet --committer-date-is-author-date "${patch}" || die "patch does not apply to ${release}: ${patch##*/}"
done
patched_commit="$(git -C "${work}" rev-parse HEAD)"
patched_tree="$(git -C "${work}" rev-parse 'HEAD^{tree}')"

echo "Building the Companion (release)..."
declare -a gradle_args=()
if [[ -n "${MICROG_GRADLE_ARGS:-}" ]]; then
  read -r -a gradle_args <<<"${MICROG_GRADLE_ARGS}"
fi
printf 'sdk.dir=%s\n' "${ANDROID_HOME}" >"${work}/local.properties"
(cd -- "${work}" && ./gradlew --no-daemon --console=plain "${gradle_args[@]}" \
  :vending-app:assembleDefaultRelease)

mapfile -t unsigned_apks < <(find "${work}/vending-app/build/outputs/apk/default/release" -maxdepth 1 -type f -name '*.apk')
[[ "${#unsigned_apks[@]}" == 1 ]] || die "expected exactly one release APK"
unsigned="${unsigned_apks[0]}"
[[ "$(git -C "${work}" rev-parse HEAD)" == "${patched_commit}" &&
   -z "$(git -C "${work}" status --porcelain --untracked-files=no)" ]] ||
  die "tracked microG source changed during the build"

aligned="${build_dir}/companion-aligned.apk"
signed="${build_dir}/com.android.vending.apk"
"${build_tools}/zipalign" -f -p 4 "${unsigned}" "${aligned}"
"${build_tools}/apksigner" sign \
  --ks "${keystore}" --ks-key-alias "${alias_name}" --ks-pass "file:${pass_file}" \
  --out "${signed}" "${aligned}"
rm -f -- "${aligned}" "${signed}.idsig"
"${build_tools}/apksigner" verify --print-certs "${signed}" >"${build_dir}/signer.txt"

{
  echo "release=${release}"
  echo "source_commit=${reviewed_revision}"
  echo "gmscore_commit=${patched_commit}"
  echo "gmscore_tree=${patched_tree}"
  for patch in "${patches[@]}"; do
    echo "patch=${patch##*/} sha256=$(sha256sum "${patch}" | cut -d' ' -f1)"
  done
  echo "apk_sha256=$(sha256sum "${signed}" | cut -d' ' -f1)"
  grep -E 'certificate SHA-256 digest' "${build_dir}/signer.txt" | head -n 1
} >"${build_dir}/build-record.txt"

# Replace prior outputs only after the new APK verifies. Failed builds leave
# the previous successful output and its matching receipt unchanged.
mv -f -- "${signed}" "${output}/com.android.vending.apk"
mv -f -- "${build_dir}/signer.txt" "${output}/signer.txt"
mv -f -- "${build_dir}/build-record.txt" "${output}/build-record.txt"

echo "Built ${output}/com.android.vending.apk"
cat "${output}/build-record.txt"
