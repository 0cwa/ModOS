#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

repo_root="$(pwd -P)"
source "${repo_root}/src/ci/publish_custota_device_alias.sh"

die() { echo "FAIL: $*" >&2; exit 1; }
scratch="$(mktemp -d)"
trap 'rm -rf -- "${scratch}"' EXIT
cd "${scratch}"
git init -q
mkdir -p rootless magisk

# Each alias must be EXACTLY the canonical metadata, including the same
# repository OTA and csig URLs. Do not invent or transform a signing pointer.
for flavor in rootless magisk; do
  canonical="${flavor}/pdx235.json"
  alias="${flavor}/XQ-DC72.json"
  printf '{"version":2,"full":{"location_ota":"https://github.com/0cwa/ModOS/releases/download/20261016/pdx235-20261016-%s.zip","location_csig":"https://github.com/0cwa/ModOS/releases/download/20261016/pdx235-20261016-%s.zip.csig"}}\n' "${flavor}" "${flavor}" >"${canonical}"
  printf '{"stale":"PixeneOS"}\n' >"${alias}"
  publish_custota_device_alias lineageos pdx235 "${flavor}" "${canonical}" ||
    die "${flavor} alias publication rejected"
  cmp -s -- "${canonical}" "${alias}" ||
    die "${flavor} published alias differs from canonical"
  git ls-files --error-unmatch "${alias}" >/dev/null ||
    die "${flavor} alias was not staged"
done

# Repeated scheduled releases must refresh pre-existing aliases, not simply
# create missing files. The pair of aliases must never cross flavors.
printf '{"version":2,"full":{"location_ota":"https://github.com/0cwa/ModOS/releases/download/20261017/rootless.zip","location_csig":"https://github.com/0cwa/ModOS/releases/download/20261017/rootless.zip.csig"}}\n' >rootless/pdx235.json
publish_custota_device_alias lineageos pdx235 rootless rootless/pdx235.json
cmp -s rootless/pdx235.json rootless/XQ-DC72.json ||
  die 'later rootless nightly did not update the alias'
cmp -s rootless/pdx235.json magisk/XQ-DC72.json &&
  die 'rootless nightly overwrote Magisk alias'

# Other ROMs/devices are unaffected, and a request cannot direct this
# privileged publisher to a caller-named alias, arbitrary flavor, or symlink.
printf 'sentinel\n' >rootless/XQ-DC72.json
publish_custota_device_alias grapheneos shiba rootless rootless/pdx235.json
grep -Fxq sentinel rootless/XQ-DC72.json ||
  die 'other ROM changed the pdx235 alias'
publish_custota_device_alias lineageos other rootless rootless/pdx235.json
grep -Fxq sentinel rootless/XQ-DC72.json ||
  die 'other device changed the pdx235 alias'
if publish_custota_device_alias lineageos pdx235 unknown rootless/pdx235.json 2>/dev/null; then
  die 'invalid flavor accepted'
fi
if publish_custota_device_alias lineageos pdx235 rootless magisk/pdx235.json 2>/dev/null; then
  die 'cross-flavor source accepted'
fi
mv rootless/pdx235.json rootless/saved.json
ln -s saved.json rootless/pdx235.json
if publish_custota_device_alias lineageos pdx235 rootless rootless/pdx235.json 2>/dev/null; then
  die 'symlink canonical source accepted'
fi
rm rootless/pdx235.json
mv rootless/saved.json rootless/pdx235.json
rm rootless/XQ-DC72.json
ln -s nonexistent.json rootless/XQ-DC72.json
if publish_custota_device_alias lineageos pdx235 rootless rootless/pdx235.json 2>/dev/null; then
  die 'symlink alias destination accepted'
fi
rm rootless/XQ-DC72.json

# Exercise both real publisher wiring contracts: the alias is generated only
# after the canonical file and selection proof are prepared for publication.
for publisher in publish_ota.sh publish_ota_pair.sh; do
  path="${repo_root}/src/ci/${publisher}"
  grep -Fq 'source "${_publish_ci_dir}/publish_custota_device_alias.sh"' "${path}" ||
    die "${publisher} does not load the shared alias function"
  grep -Fq 'publish_custota_device_alias "${ROM_FAMILY}" "${DEVICE_NAME}"' "${path}" ||
    die "${publisher} does not publish the Sony device alias"
done

echo 'Custota pdx235/XQ-DC72 device-alias publication tests passed'
