#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

set -euo pipefail

default_value="$(env -u ADDITIONALS_BOOT_ANIMATION bash -c '
  source src/declarations.sh
  printf "%s\n" "${ADDITIONALS[BOOT_ANIMATION]}"
')"
[[ "${default_value}" == "true" ]] || {
  echo "boot animation is not default-on: ${default_value}" >&2
  exit 1
}

disabled_value="$(ADDITIONALS_BOOT_ANIMATION=false bash -c '
  source src/declarations.sh
  printf "%s\n" "${ADDITIONALS[BOOT_ANIMATION]}"
')"
[[ "${disabled_value}" == "false" ]] || {
  echo "boot animation explicit opt-out was not applied" >&2
  exit 1
}

echo "boot animation configuration tests passed"
