#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
source src/declarations.sh
source src/rom_profiles.sh
reject() { if "$@"; then echo "Unexpected acceptance: $*" >&2; exit 1; fi; }
repair=abdede21c1b048e11700ec2789d0fb96ace0b0e4
for mode in rootless both; do
  for scope in published private shared; do
    reject enforce_pdx235_rootless_hold lineageos pdx235 "${mode}" "${scope}"
  done
  enforce_pdx235_rootless_hold lineageos pdx235 "${mode}" local-unpublished
  require_pdx235_repaired_helper lineageos pdx235 "${mode}" "${repair}"
  reject require_pdx235_repaired_helper lineageos pdx235 "${mode}" ed6c24fdcc34d048f1fd51580c0c08569c3c0cc5
  reject require_pdx235_repaired_helper lineageos pdx235 "${mode}" ''
done
enforce_pdx235_rootless_hold lineageos pdx235 magisk published
enforce_pdx235_rootless_hold grapheneos shiba both published
enforce_pdx235_rootless_hold lineageos other rootless published
# Actual entry points must reject even the pilot flag and force-publish before
# looking for assets or invoking gh/git. Missing metadata cannot bypass a hold.
for helper in publish_ota.sh publish_ota_pair.sh; do
  reject env ROM_FAMILY=lineageos DEVICE_NAME=pdx235 ROM_FLAVOR=rootless \
    ROM_PROFILE_PROVIDER=lineageos GRAPHENEOS_VERSION=20261016 \
    OUTPUT_SCOPE=published RELEASE_TYPE=force-publish \
    PIXENEOS_PDX235_PILOT_RELEASE=true bash "src/ci/${helper}"
done
ROM_FAMILY=lineageos DEVICE_NAME=pdx235 ROOT_MODE=both RESOLVED_ROOT_MODE=''
PIXENEOS_PDX235_PILOT_RELEASE=true
ADDITIONALS[DEBUG]=false ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]=false
reject enforce_output_policy published
VERSION[AVBROOT_SETUP]=ed6c24fdcc34d048f1fd51580c0c08569c3c0cc5
reject enforce_output_policy local-unpublished
VERSION[AVBROOT_SETUP]="${repair}"
enforce_output_policy local-unpublished

# Exercise the real withdrawal script against a disposable bare remote, proving
# atomic removal, preservation of unrelated metadata, and idempotence.
script="${PWD}/src/ci/hold_pdx235_rootless_feed.sh"
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
git init -q --bare "${tmp}/remote.git"
git init -q -b gh-pages "${tmp}/feed"
cd "${tmp}/feed"
git config user.name fixture
git config user.email fixture@example.invalid
git remote add origin "${tmp}/remote.git"
mkdir -p rootless magisk variants/lineageos/rootless
for file in rootless/pdx235.json rootless/XQ-DC72.json \
  variants/lineageos/rootless/pdx235-fingerprint.json \
  magisk/pdx235.json magisk/XQ-DC72.json rootless/shiba.json; do
  printf 'fixture\n' >"${file}"
done
git add .
git commit -qm fixture
git push -q origin gh-pages
before="$(git rev-parse HEAD)"
bash "${script}"
[[ "$(git diff --name-only "${before}" | wc -l)" -eq 3 ]]
for file in magisk/pdx235.json magisk/XQ-DC72.json rootless/shiba.json; do
  [[ "$(cat "${file}")" == fixture ]]
done
after="$(git rev-parse HEAD)"
[[ "${after}" == "$(git ls-remote origin refs/heads/gh-pages | cut -f1)" ]]
bash "${script}"
[[ "${after}" == "$(git rev-parse HEAD)" ]]
echo 'PASS: rootless hold, stale helper, direct publishers, exact withdrawal and idempotence'
