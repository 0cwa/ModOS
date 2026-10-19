#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
source src/declarations.sh
source src/rom_profiles.sh
reject() { if "$@"; then echo "Unexpected acceptance: $*" >&2; exit 1; fi; }
repair=d6eccab076ba48889c2279ce7c88e43261cbd1f0
previous_repair=abdede21c1b048e11700ec2789d0fb96ace0b0e4
for mode in rootless both; do
  require_pdx235_repaired_helper lineageos pdx235 "${mode}" "${repair}"
  reject require_pdx235_repaired_helper lineageos pdx235 "${mode}" "${previous_repair}"
  reject require_pdx235_repaired_helper lineageos pdx235 "${mode}" c9d091225bdbed9f62d6401a040e3cdbae834078
  reject require_pdx235_repaired_helper lineageos pdx235 "${mode}" ed6c24fdcc34d048f1fd51580c0c08569c3c0cc5
  reject require_pdx235_repaired_helper lineageos pdx235 "${mode}" ''
done
require_pdx235_repaired_helper lineageos pdx235 magisk old-revision
require_pdx235_repaired_helper grapheneos shiba both old-revision
require_pdx235_repaired_helper lineageos other rootless old-revision
ROM_FAMILY=lineageos DEVICE_NAME=pdx235 ROOT_MODE=both RESOLVED_ROOT_MODE=''
PIXENEOS_PDX235_PILOT_RELEASE=true
ADDITIONALS[DEBUG]=false ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]=false
VERSION[GRAPHENEOS]=20261016
VERSION[AVBROOT_SETUP]="${repair}"
enforce_output_policy published
enforce_publication_evidence published
VERSION[AVBROOT_SETUP]=c9d091225bdbed9f62d6401a040e3cdbae834078
reject enforce_output_policy published
reject enforce_publication_evidence published
reject enforce_output_policy local-unpublished
echo 'PASS: repaired helper publication accepted; published pre-repair helper rejected'
