#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# This file is the source of truth for the typed env.toml contract. The
# accepted spellings are historical env.toml keys, not shell variable names.
# Keep compatibility aliases until repository/user migration evidence shows
# that the old spelling is no longer in use.
if [[ ${PIXENEOS_CONFIG_SCHEMA_LOADED+x} ]]; then
  return 0
fi
PIXENEOS_CONFIG_SCHEMA_LOADED=true

declare -ag CONFIG_SCHEMA_KEYS=()
declare -Ag CONFIG_SCHEMA_CANONICAL_BY_ACCEPTED_KEY=()
declare -Ag CONFIG_SCHEMA_ACCEPTED_KEY=()
declare -Ag CONFIG_SCHEMA_SECTION=()
declare -Ag CONFIG_SCHEMA_TYPE=()
declare -Ag CONFIG_SCHEMA_SHELL_DESTINATION=()
declare -Ag CONFIG_SCHEMA_SHELL_DESTINATION_KIND=()
declare -Ag CONFIG_SCHEMA_SHELL_DESTINATION_KEY=()
declare -Ag CONFIG_SCHEMA_CALLER_DESTINATION=()
declare -Ag CONFIG_SCHEMA_DEFAULT=()
declare -Ag CONFIG_SCHEMA_IDENTITY_PARTICIPATES=()
declare -Ag CONFIG_SCHEMA_DECLARATION_POLICY=()
declare -Ag TOML_CONFIG_PRESENT=()
declare -Ag TOML_CONFIG_VALUES=()

# The declaration policy preserves the old distinction between scalar caller
# variables, associative-array fallbacks, and values that are intentionally
# not initialized by declarations.sh (FORCE_UPDATE is workflow-owned).
function _config_schema_define() {
  local canonical="${1}"
  local accepted_key="${2}"
  local section="${3}"
  local type="${4}"
  local shell_destination="${5}"
  local shell_kind="${6}"
  local shell_key="${7}"
  local caller_destination="${8}"
  local default_value="${9}"
  local identity_participates="${10}"
  local declaration_policy="${11}"

  CONFIG_SCHEMA_KEYS+=("${canonical}")
  CONFIG_SCHEMA_CANONICAL_BY_ACCEPTED_KEY["${accepted_key}"]="${canonical}"
  CONFIG_SCHEMA_ACCEPTED_KEY["${canonical}"]="${accepted_key}"
  CONFIG_SCHEMA_SECTION["${canonical}"]="${section}"
  CONFIG_SCHEMA_TYPE["${canonical}"]="${type}"
  CONFIG_SCHEMA_SHELL_DESTINATION["${canonical}"]="${shell_destination}"
  CONFIG_SCHEMA_SHELL_DESTINATION_KIND["${canonical}"]="${shell_kind}"
  CONFIG_SCHEMA_SHELL_DESTINATION_KEY["${canonical}"]="${shell_key}"
  CONFIG_SCHEMA_CALLER_DESTINATION["${canonical}"]="${caller_destination}"
  CONFIG_SCHEMA_DEFAULT["${canonical}"]="${default_value}"
  CONFIG_SCHEMA_IDENTITY_PARTICIPATES["${canonical}"]="${identity_participates}"
  CONFIG_SCHEMA_DECLARATION_POLICY["${canonical}"]="${declaration_policy}"
}

_config_schema_define device_name DEVICE_NAME device string \
  DEVICE_NAME scalar '' DEVICE_NAME '' false caller-or-default
_config_schema_define devices DEVICES device string \
  DEVICES scalar '' DEVICES '' false caller-or-default
_config_schema_define rom_family ROM_FAMILY device string \
  ROM_FAMILY scalar '' ROM_FAMILY grapheneos true caller-or-default
_config_schema_define interactive_mode INTERACTIVE_MODE device boolean \
  INTERACTIVE_MODE scalar '' INTERACTIVE_MODE true false caller-or-default
_config_schema_define output_scope OUTPUT_SCOPE device string \
  OUTPUT_SCOPE scalar '' OUTPUT_SCOPE local-unpublished true caller-or-default
_config_schema_define force_update FORCE_UPDATE build boolean \
  FORCE_UPDATE scalar '' FORCE_UPDATE '' false workflow-owned
_config_schema_define root ROOT build boolean \
  ADDITIONALS array ROOT ADDITIONALS_ROOT false true caller-or-default
_config_schema_define root_mode ROOT_MODE build string \
  ROOT_MODE scalar '' ROOT_MODE '' false caller-or-default
_config_schema_define magisk_preinit MAGISK_PREINIT build string \
  MAGISK array PREINIT MAGISK_PREINIT '' true caller-or-default
_config_schema_define update_channel 'GRAPHENEOS[UPDATE_CHANNEL]' device string \
  GRAPHENEOS array UPDATE_CHANNEL GRAPHENEOS_UPDATE_CHANNEL '' true existing-or-default
_config_schema_define magisk_repository 'MAGISK[REPOSITORY]' device string \
  MAGISK array REPOSITORY '' topjohnwu/Magisk false caller-or-default
_config_schema_define magisk_version MAGISK_VERSION build string \
  VERSION array MAGISK MAGISK_VERSION v30.7 true caller-or-existing-or-default
_config_schema_define afsr 'ADDITIONALS[AFSR]' build boolean \
  ADDITIONALS array AFSR ADDITIONALS_AFSR true true caller-or-existing-or-default
_config_schema_define alterinstaller 'ADDITIONALS[ALTERINSTALLER]' build boolean \
  ADDITIONALS array ALTERINSTALLER ADDITIONALS_ALTERINSTALLER true true caller-or-existing-or-default
_config_schema_define bcr 'ADDITIONALS[BCR]' build boolean \
  ADDITIONALS array BCR ADDITIONALS_BCR true true caller-or-existing-or-default
_config_schema_define custota 'ADDITIONALS[CUSTOTA]' build boolean \
  ADDITIONALS array CUSTOTA ADDITIONALS_CUSTOTA true true caller-or-existing-or-default
_config_schema_define msd 'ADDITIONALS[MSD]' build boolean \
  ADDITIONALS array MSD ADDITIONALS_MSD true true caller-or-existing-or-default
_config_schema_define oemunlockonboot 'ADDITIONALS[OEMUNLOCKONBOOT]' build boolean \
  ADDITIONALS array OEMUNLOCKONBOOT ADDITIONALS_OEMUNLOCKONBOOT true true caller-or-existing-or-default
_config_schema_define boot_animation 'ADDITIONALS[BOOT_ANIMATION]' build boolean \
  ADDITIONALS array BOOT_ANIMATION ADDITIONALS_BOOT_ANIMATION true true caller-or-existing-or-default
_config_schema_define compatible_sepolicy_patching 'ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]' build boolean \
  ADDITIONALS array MAS_COMPATIBLE_SEPOLICY ADDITIONALS_MAS_COMPATIBLE_SEPOLICY false true caller-or-existing-or-default
_config_schema_define fdroid_privileged_extension \
  'ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]' build boolean \
  ADDITIONALS array FDROID_PRIVILEGED_EXTENSION \
  ADDITIONALS_FDROID_PRIVILEGED_EXTENSION false true caller-or-existing-or-default
_config_schema_define release_owner PIXENEOS_RELEASE_OWNER github string \
  PIXENEOS_RELEASE_OWNER scalar '' PIXENEOS_RELEASE_OWNER '' false caller-or-default
_config_schema_define release_repository PIXENEOS_RELEASE_REPOSITORY github string \
  PIXENEOS_RELEASE_REPOSITORY scalar '' PIXENEOS_RELEASE_REPOSITORY '' false caller-or-default
_config_schema_define release_base_url PIXENEOS_RELEASE_BASE_URL github string \
  PIXENEOS_RELEASE_BASE_URL scalar '' PIXENEOS_RELEASE_BASE_URL '' false caller-or-default
_config_schema_define setup_source PIXENEOS_AVBROOT_SETUP_SOURCE github string \
  PIXENEOS_AVBROOT_SETUP_SOURCE scalar '' PIXENEOS_AVBROOT_SETUP_SOURCE '' false caller-or-default

function config_schema_key_exists() {
  [[ ${CONFIG_SCHEMA_TYPE[${1}]+x} ]]
}

function config_schema_lookup_key() {
  local section="${1}"
  local accepted_key="${2}"
  local legacy_mode="${3}"
  local canonical

  CONFIG_SCHEMA_CANONICAL=''
  [[ ${CONFIG_SCHEMA_CANONICAL_BY_ACCEPTED_KEY["${accepted_key}"]+x} ]] || return 1
  canonical="${CONFIG_SCHEMA_CANONICAL_BY_ACCEPTED_KEY["${accepted_key}"]}"

  if [[ "${legacy_mode}" != true && "${CONFIG_SCHEMA_SECTION[${canonical}]}" != "${section}" ]]; then
    return 1
  fi

  CONFIG_SCHEMA_CANONICAL="${canonical}"
  return 0
}

function config_schema_validate_value() {
  local canonical="${1}"
  local value="${2}"

  config_schema_key_exists "${canonical}" || return 1
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || return 1
  if [[ "${CONFIG_SCHEMA_TYPE[${canonical}]}" == boolean ]]; then
    [[ "${value}" == true || "${value}" == false ]] || return 1
  fi
}

function config_schema_default() {
  local canonical="${1}"
  config_schema_key_exists "${canonical}" || return 1
  printf '%s' "${CONFIG_SCHEMA_DEFAULT[${canonical}]}"
}

function config_schema_caller_supplied() {
  local canonical="${1}"
  local caller_destination

  config_schema_key_exists "${canonical}" || return 1
  caller_destination="${CONFIG_SCHEMA_CALLER_DESTINATION[${canonical}]}"
  [[ -n "${caller_destination}" ]] || return 1
  [[ -v "${caller_destination}" ]]
}

function config_schema_caller_present() {
  local canonical="${1}"

  config_schema_key_exists "${canonical}" || return 1
  declare -p DECLARATION_CALLER_PRESENT >/dev/null 2>&1 || return 1
  [[ ${DECLARATION_CALLER_PRESENT[${canonical}]+x} ]]
}

function config_schema_caller_value() {
  local canonical="${1}"
  local caller_destination

  config_schema_key_exists "${canonical}" || return 1
  caller_destination="${CONFIG_SCHEMA_CALLER_DESTINATION[${canonical}]}"
  [[ -n "${caller_destination}" ]] || return 1
  printf '%s' "${!caller_destination-}"
}

# Apply only values that have already passed the TOML syntax/type checks. The
# destination names are schema data, and printf -v/nameref avoid shell eval.
function config_schema_apply_value() {
  local canonical="${1}"
  local value="${2}"
  local destination destination_kind destination_key

  config_schema_validate_value "${canonical}" "${value}" || return 1
  destination="${CONFIG_SCHEMA_SHELL_DESTINATION[${canonical}]}"
  destination_kind="${CONFIG_SCHEMA_SHELL_DESTINATION_KIND[${canonical}]}"
  destination_key="${CONFIG_SCHEMA_SHELL_DESTINATION_KEY[${canonical}]}"

  case "${destination_kind}" in
    scalar)
      printf -v "${destination}" '%s' "${value}"
      ;;
    array)
      local -n destination_ref="${destination}"
      destination_ref[${destination_key}]="${value}"
      ;;
    *)
      return 1
      ;;
  esac
}

function config_schema_resolve_value() {
  local canonical="${1}"
  local fallback

  config_schema_key_exists "${canonical}" || return 1
  if [[ $# -ge 2 ]]; then
    fallback="${2}"
  else
    fallback="$(config_schema_default "${canonical}")"
  fi

  if config_schema_caller_present "${canonical}"; then
    config_schema_caller_value "${canonical}"
  elif [[ ${TOML_CONFIG_PRESENT[${canonical}]+x} ]]; then
    printf '%s' "${TOML_CONFIG_VALUES[${canonical}]}"
  else
    printf '%s' "${fallback}"
  fi
}
