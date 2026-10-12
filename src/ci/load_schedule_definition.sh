#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

: "${SCHEDULE_DEFINITION:?SCHEDULE_DEFINITION must point to a checked-in schedule TOML file}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"

[[ -f "${SCHEDULE_DEFINITION}" && ! -L "${SCHEDULE_DEFINITION}" ]] || {
  echo "::error::Schedule definition must be a regular checked-in file: ${SCHEDULE_DEFINITION}"
  exit 1
}

source src/util_functions.sh
check_toml_env "${SCHEDULE_DEFINITION}"

required=(
  device_name
  rom_family
  update_channel
  root
  root_mode
  magisk_preinit
  afsr
  alterinstaller
  bcr
  custota
  msd
  oemunlockonboot
  disable_system_updater
  fdroid_privileged_extension
  boot_animation
  compatible_sepolicy_patching
  force_update
)

for key in "${required[@]}"; do
  toml_config_has "${key}" || {
    echo "::error::Schedule definition is missing required key: ${key}"
    exit 1
  }
done

device_id="$(toml_resolve_value device_name '')"
rom_family="$(toml_resolve_value rom_family '')"
update_channel="$(toml_resolve_value update_channel '')"
root="$(toml_resolve_value root '')"
root_mode="$(toml_resolve_value root_mode '')"
magisk_preinit="$(toml_resolve_value magisk_preinit '')"
afsr="$(toml_resolve_value afsr '')"
alterinstaller="$(toml_resolve_value alterinstaller '')"
bcr="$(toml_resolve_value bcr '')"
custota="$(toml_resolve_value custota '')"
msd="$(toml_resolve_value msd '')"
oemunlockonboot="$(toml_resolve_value oemunlockonboot '')"
disable_system_updater="$(toml_resolve_value disable_system_updater '')"
fdroid_privileged_extension="$(toml_resolve_value fdroid_privileged_extension '')"
boot_animation="$(toml_resolve_value boot_animation '')"
compatible_sepolicy_patching="$(toml_resolve_value compatible_sepolicy_patching '')"
force_update="$(toml_resolve_value force_update '')"

[[ -n "${device_id}" && -n "${rom_family}" && -n "${update_channel}" && -n "${root_mode}" && -n "${magisk_preinit}" ]] || {
  echo "::error::Schedule definition contains an empty required string."
  exit 1
}

if [[ -n "${EXPECTED_ROM_FAMILY:-}" && "${rom_family}" != "${EXPECTED_ROM_FAMILY}" ]]; then
  echo "::error::Schedule definition ROM family ${rom_family} does not match expected ${EXPECTED_ROM_FAMILY}."
  exit 1
fi

case "${root_mode}" in
  rootless|magisk|both) ;;
  *)
    echo "::error::Schedule ROOT_MODE must be rootless, magisk, or both."
    exit 1
    ;;
esac

{
  echo "device_id=${device_id}"
  echo "rom_family=${rom_family}"
  echo "update_channel=${update_channel}"
  echo "root=${root}"
  echo "root_mode=${root_mode}"
  echo "magisk_preinit_device=${magisk_preinit}"
  echo "afsr=${afsr}"
  echo "alterinstaller=${alterinstaller}"
  echo "bcr=${bcr}"
  echo "custota=${custota}"
  echo "msd=${msd}"
  echo "oemunlockonboot=${oemunlockonboot}"
  echo "disable_system_updater=${disable_system_updater}"
  echo "fdroid_privileged_extension=${fdroid_privileged_extension}"
  echo "boot_animation=${boot_animation}"
  echo "compatible_sepolicy_patching=${compatible_sepolicy_patching}"
  echo "force_update=${force_update}"
} >>"${GITHUB_OUTPUT}"

{
  echo "DEVICE_NAME=${device_id}"
  echo "ROM_FAMILY=${rom_family}"
  echo "GRAPHENEOS_UPDATE_CHANNEL=${update_channel}"
  echo "ADDITIONALS_ROOT=${root}"
  echo "ROOT_MODE=${root_mode}"
  echo "MAGISK_PREINIT=${magisk_preinit}"
  echo "ADDITIONALS_AFSR=${afsr}"
  echo "ADDITIONALS_ALTERINSTALLER=${alterinstaller}"
  echo "ADDITIONALS_BCR=${bcr}"
  echo "ADDITIONALS_CUSTOTA=${custota}"
  echo "ADDITIONALS_MSD=${msd}"
  echo "ADDITIONALS_OEMUNLOCKONBOOT=${oemunlockonboot}"
  echo "ADDITIONALS_DISABLE_SYSTEM_UPDATER=${disable_system_updater}"
  echo "ADDITIONALS_FDROID_PRIVILEGED_EXTENSION=${fdroid_privileged_extension}"
  echo "ADDITIONALS_BOOT_ANIMATION=${boot_animation}"
  echo "ADDITIONALS_MAS_COMPATIBLE_SEPOLICY=${compatible_sepolicy_patching}"
  echo "FORCE_UPDATE=${force_update}"
} >>"${GITHUB_ENV}"
