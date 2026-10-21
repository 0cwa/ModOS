#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

root="$(pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
mkdir -p "${tmp}/bin"
printf 'synthetic source asset\n' >"${tmp}/source.tar.gz"
cat >"${tmp}/bin/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == release && "$2" == download ]] || exit 90
tag="$3"
[[ "$tag" == test-tag ]] || exit 91
shift 3
[[ "$1" == --pattern ]] || exit 92
name="$2"
shift 2
[[ "$1" == --dir ]] || exit 93
dir="$2"
cp -- "${EXFAT_TEST_ASSET}" "${dir}/${name}"
SH
chmod +x "${tmp}/bin/gh"
export PATH="${tmp}/bin:${PATH}" EXFAT_TEST_ASSET="${tmp}/source.tar.gz"
name='exfatprogs-1.4.3-corresponding-source-test.tar.gz'
digest="$(sha256sum "${tmp}/source.tar.gz" | awk '{print $1}')"

bash src/ci/verify_release_asset.sh test-tag "${name}" "${digest}"
if bash src/ci/verify_release_asset.sh test-tag "${name}" "$(printf '%064d' 0)" >/dev/null 2>&1; then
  echo 'mismatched source asset digest unexpectedly passed' >&2
  exit 1
fi
if bash src/ci/verify_release_asset.sh test-tag other.zip "${digest}" >/dev/null 2>&1; then
  echo 'non-source asset name unexpectedly passed' >&2
  exit 1
fi
echo 'exfat source asset verification tests passed'
