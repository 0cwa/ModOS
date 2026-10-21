#!/usr/bin/env bash
# Download an exact release asset and verify its expected digest.
set -o nounset -o pipefail -o errexit
tag="${1-}"
name="${2-}"
expected="${3-}"
[[ "${tag}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo 'invalid release tag' >&2; exit 1; }
[[ "${name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*[.]tar[.]gz$ ]] || { echo 'invalid release asset name' >&2; exit 1; }
[[ "${expected}" =~ ^[0-9a-f]{64}$ ]] || { echo 'invalid expected asset digest' >&2; exit 1; }
dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/release-asset-verify.XXXXXX")"
trap 'rm -rf -- "${dir}"' EXIT
gh release download "${tag}" --pattern "${name}" --dir "${dir}"
file="${dir}/${name}"
[[ -f "${file}" && ! -L "${file}" ]] || { echo 'expected source asset was not downloaded as a regular file' >&2; exit 1; }
actual="$(sha256sum -- "${file}" | awk '{print $1}')"
[[ "${actual}" == "${expected}" ]] || { echo 'source release asset SHA-256 mismatch' >&2; exit 1; }
