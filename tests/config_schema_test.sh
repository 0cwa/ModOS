#!/usr/bin/env bash

set -euo pipefail

source src/util_functions.sh

declare -A ADDITIONALS=()
declare -A GRAPHENEOS=()
declare -A MAGISK=()
declare -A DECLARATION_CALLER_PRESENT=()

fail() {
  echo "$*" >&2
  exit 1
}

assert_equals() {
  local expected="${1}"
  local actual="${2}"
  local context="${3}"
  [[ "${actual}" == "${expected}" ]] ||
    fail "${context}: expected <${expected}>, got <${actual}>"
}

assert_destination_value() {
  local canonical="${1}"
  local destination="${2}"
  local destination_kind="${3}"
  local destination_key="${4}"
  local expected="${5}"
  local actual

  if [[ "${destination_kind}" == scalar ]]; then
    actual="${!destination-}"
  else
    local -n destination_ref="${destination}"
    actual="${destination_ref[${destination_key}]-}"
  fi
  assert_equals "${expected}" "${actual}" "${canonical} shell destination"
}

test_metadata_rows() {
  local row canonical accepted section type destination destination_kind
  local destination_key caller default_value identity policy expected_value
  local -a rows=(
    'device_name|DEVICE_NAME|device|string|DEVICE_NAME|scalar||DEVICE_NAME||false|caller-or-default'
    'rom_family|ROM_FAMILY|device|string|ROM_FAMILY|scalar||ROM_FAMILY|grapheneos|true|caller-or-default'
    'interactive_mode|INTERACTIVE_MODE|device|boolean|INTERACTIVE_MODE|scalar||INTERACTIVE_MODE|true|false|caller-or-default'
    'output_scope|OUTPUT_SCOPE|device|string|OUTPUT_SCOPE|scalar||OUTPUT_SCOPE|local-unpublished|true|caller-or-default'
    'force_update|FORCE_UPDATE|build|boolean|FORCE_UPDATE|scalar||FORCE_UPDATE||false|workflow-owned'
    'root|ROOT|build|boolean|ADDITIONALS|array|ROOT|ADDITIONALS_ROOT|false|true|caller-or-default'
    'magisk_preinit|MAGISK_PREINIT|build|string|MAGISK|array|PREINIT|MAGISK_PREINIT||true|caller-or-default'
    'update_channel|GRAPHENEOS[UPDATE_CHANNEL]|device|string|GRAPHENEOS|array|UPDATE_CHANNEL|GRAPHENEOS_UPDATE_CHANNEL||true|existing-or-default'
    'magisk_repository|MAGISK[REPOSITORY]|device|string|MAGISK|array|REPOSITORY||topjohnwu/Magisk|false|caller-or-default'
    'afsr|ADDITIONALS[AFSR]|build|boolean|ADDITIONALS|array|AFSR|ADDITIONALS_AFSR|true|true|caller-or-existing-or-default'
    'alterinstaller|ADDITIONALS[ALTERINSTALLER]|build|boolean|ADDITIONALS|array|ALTERINSTALLER|ADDITIONALS_ALTERINSTALLER|true|true|caller-or-existing-or-default'
    'bcr|ADDITIONALS[BCR]|build|boolean|ADDITIONALS|array|BCR|ADDITIONALS_BCR|true|true|caller-or-existing-or-default'
    'custota|ADDITIONALS[CUSTOTA]|build|boolean|ADDITIONALS|array|CUSTOTA|ADDITIONALS_CUSTOTA|true|true|caller-or-existing-or-default'
    'msd|ADDITIONALS[MSD]|build|boolean|ADDITIONALS|array|MSD|ADDITIONALS_MSD|true|true|caller-or-existing-or-default'
    'oemunlockonboot|ADDITIONALS[OEMUNLOCKONBOOT]|build|boolean|ADDITIONALS|array|OEMUNLOCKONBOOT|ADDITIONALS_OEMUNLOCKONBOOT|true|true|caller-or-existing-or-default'
    'boot_animation|ADDITIONALS[BOOT_ANIMATION]|build|boolean|ADDITIONALS|array|BOOT_ANIMATION|ADDITIONALS_BOOT_ANIMATION|true|true|caller-or-existing-or-default'
    'fdroid_privileged_extension|ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]|build|boolean|ADDITIONALS|array|FDROID_PRIVILEGED_EXTENSION|ADDITIONALS_FDROID_PRIVILEGED_EXTENSION|false|true|caller-or-existing-or-default'
    'release_owner|PIXENEOS_RELEASE_OWNER|github|string|PIXENEOS_RELEASE_OWNER|scalar||PIXENEOS_RELEASE_OWNER||false|caller-or-default'
    'release_repository|PIXENEOS_RELEASE_REPOSITORY|github|string|PIXENEOS_RELEASE_REPOSITORY|scalar||PIXENEOS_RELEASE_REPOSITORY||false|caller-or-default'
    'release_base_url|PIXENEOS_RELEASE_BASE_URL|github|string|PIXENEOS_RELEASE_BASE_URL|scalar||PIXENEOS_RELEASE_BASE_URL||false|caller-or-default'
    'setup_source|PIXENEOS_AVBROOT_SETUP_SOURCE|github|string|PIXENEOS_AVBROOT_SETUP_SOURCE|scalar||PIXENEOS_AVBROOT_SETUP_SOURCE||false|caller-or-default'
  )

  assert_equals 21 "${#CONFIG_SCHEMA_KEYS[@]}" "schema key count"
  for row in "${rows[@]}"; do
    IFS='|' read -r canonical accepted section type destination destination_kind \
      destination_key caller default_value identity policy <<<"${row}"

    [[ ${CONFIG_SCHEMA_TYPE[${canonical}]+x} ]] || fail "missing schema row: ${canonical}"
    assert_equals "${accepted}" "${CONFIG_SCHEMA_ACCEPTED_KEY[${canonical}]}" \
      "${canonical} accepted alias"
    assert_equals "${section}" "${CONFIG_SCHEMA_SECTION[${canonical}]}" \
      "${canonical} section"
    assert_equals "${type}" "${CONFIG_SCHEMA_TYPE[${canonical}]}" \
      "${canonical} type"
    assert_equals "${destination}" "${CONFIG_SCHEMA_SHELL_DESTINATION[${canonical}]}" \
      "${canonical} destination"
    assert_equals "${destination_kind}" "${CONFIG_SCHEMA_SHELL_DESTINATION_KIND[${canonical}]}" \
      "${canonical} destination kind"
    assert_equals "${destination_key}" "${CONFIG_SCHEMA_SHELL_DESTINATION_KEY[${canonical}]}" \
      "${canonical} destination key"
    assert_equals "${caller}" "${CONFIG_SCHEMA_CALLER_DESTINATION[${canonical}]}" \
      "${canonical} caller destination"
    assert_equals "${default_value}" "${CONFIG_SCHEMA_DEFAULT[${canonical}]}" \
      "${canonical} default"
    assert_equals "${identity}" "${CONFIG_SCHEMA_IDENTITY_PARTICIPATES[${canonical}]}" \
      "${canonical} identity metadata"
    assert_equals "${policy}" "${CONFIG_SCHEMA_DECLARATION_POLICY[${canonical}]}" \
      "${canonical} declaration policy"

    config_schema_lookup_key "${section}" "${accepted}" false
    assert_equals "${canonical}" "${CONFIG_SCHEMA_CANONICAL}" \
      "${canonical} strict alias lookup"
    config_schema_lookup_key '' "${accepted}" true
    assert_equals "${canonical}" "${CONFIG_SCHEMA_CANONICAL}" \
      "${canonical} unsectioned legacy lookup"
    config_schema_lookup_key device "${accepted}" true
    assert_equals "${canonical}" "${CONFIG_SCHEMA_CANONICAL}" \
      "${canonical} device legacy lookup"

    if [[ "${type}" == boolean ]]; then
      expected_value=false
    else
      expected_value="fixture-${canonical}"
    fi
    config_schema_apply_value "${canonical}" "${expected_value}"
    assert_destination_value "${canonical}" "${destination}" "${destination_kind}" \
      "${destination_key}" "${expected_value}"
  done
}

test_strict_section_rejection() {
  local canonical accepted section wrong_section

  for canonical in "${CONFIG_SCHEMA_KEYS[@]}"; do
    accepted="${CONFIG_SCHEMA_ACCEPTED_KEY[${canonical}]}"
    section="${CONFIG_SCHEMA_SECTION[${canonical}]}"
    case "${section}" in
      device) wrong_section=build ;;
      build) wrong_section=github ;;
      github) wrong_section=device ;;
      *) fail "unknown test section for ${canonical}: ${section}" ;;
    esac
    if config_schema_lookup_key "${wrong_section}" "${accepted}" false; then
      fail "${canonical} was accepted in wrong strict section"
    fi
  done
}

test_resolution_precedence() {
  TOML_CONFIG_PRESENT=([afsr]=true [device_name]=true)
  TOML_CONFIG_VALUES=([afsr]=false [device_name]=from-config)
  DECLARATION_CALLER_PRESENT=()
  unset ADDITIONALS_AFSR DEVICE_NAME

  assert_equals false "$(config_schema_resolve_value afsr fallback)" \
    "config value precedence"
  assert_equals from-config "$(config_schema_resolve_value device_name fallback)" \
    "config string precedence"

  ADDITIONALS_AFSR=false
  DEVICE_NAME=''
  DECLARATION_CALLER_PRESENT[afsr]=true
  DECLARATION_CALLER_PRESENT[device_name]=true
  assert_equals false "$(config_schema_resolve_value afsr fallback)" \
    "explicit false caller precedence"
  assert_equals '' "$(config_schema_resolve_value device_name fallback)" \
    "explicit empty caller precedence"

  DECLARATION_CALLER_PRESENT=()
  TOML_CONFIG_PRESENT=()
  TOML_CONFIG_VALUES=()
  assert_equals fallback "$(config_schema_resolve_value afsr fallback)" \
    "omitted value precedence"
  assert_equals true "$(config_schema_default afsr)" "schema default lookup"
}

test_public_adapter_unknown_key_fallback() {
  assert_equals fallback "$(toml_resolve_value unknown_key fallback)" \
    "public adapter unknown-key fallback"
  if config_schema_resolve_value unknown_key fallback; then
    fail "strict schema resolver accepted an unknown key"
  fi
}

test_declaration_magisk_repository_compatibility() {
  local actual

  actual="$(bash -c '
    declare -A MAGISK=()
    MAGISK[REPOSITORY]=preexisting/repository
    source src/declarations.sh
    printf "%s|%s" "${MAGISK[REPOSITORY]}" "${MAGISK[URL]}"
  ')"
  assert_equals \
    "topjohnwu/Magisk|https://github.com/topjohnwu/Magisk" \
    "${actual}" "declaration Magisk repository compatibility"
}

test_selection_identity_metadata() {
  local manifest

  assert_equals true "${CONFIG_SCHEMA_IDENTITY_PARTICIPATES[rom_family]}" \
    "rom family identity metadata"
  assert_equals true "${CONFIG_SCHEMA_IDENTITY_PARTICIPATES[output_scope]}" \
    "output scope identity metadata"

  source src/ci/selection_variant.sh
  SELECTION_ROM_FAMILY=grapheneos
  SELECTION_UPDATE_CHANNEL=stable
  SELECTION_UPDATE_TYPE=ota
  SELECTION_OUTPUT_SCOPE=local-unpublished
  SELECTION_ROOT=false
  SELECTION_MAGISK_PREINIT=none
  SELECTION_DEBUG=false
  SELECTION_COMPATIBLE_SEPOLICY=false
  SELECTION_CLEAR_VBMETA_FLAGS=false
  SELECTION_HELPER_COMMIT=fixture-helper
  SELECTION_LOCK_SHA256=disabled
  SELECTION_PROFILE_SHA256=disabled
  SELECTION_MODULE_AFSR=false
  SELECTION_MODULE_ALTERINSTALLER=false
  SELECTION_MODULE_BCR=false
  SELECTION_MODULE_CUSTOTA=false
  SELECTION_MODULE_FDROID_PRIVILEGED_EXTENSION=false
  SELECTION_MODULE_MSD=false
  SELECTION_MODULE_OEMUNLOCKONBOOT=false
  SELECTION_BOOT_ANIMATION=false
  manifest="$(selection_variant_manifest)"
  [[ "${manifest}" == *$'rom_family=grapheneos\n'* ]] ||
    fail "selection identity omits rom family"
  [[ "${manifest}" == *$'output_scope=local-unpublished\n'* ]] ||
    fail "selection identity omits output scope"
}

test_no_eval_assignment() {
  local marker value
  marker="$(mktemp)"
  rm -f -- "${marker}"
  value="literal \$(touch ${marker}) \`touch ${marker}\`"
  config_schema_apply_value device_name "${value}"
  assert_equals "${value}" "${DEVICE_NAME}" "literal string assignment"
  [[ ! -e "${marker}" ]] || fail "schema assignment evaluated input"
}

test_no_duplicate_parser_mapping() {
  ! grep -Eq 'case[[:space:]]+"?\$\{canonical\}' src/util_functions.sh ||
    fail "util parser still has canonical-key case mapping"
  ! grep -Eq 'case[[:space:]]+"?\$\{canonical\}' src/declarations.sh ||
    fail "declarations still has canonical-key case mapping"
}

test_metadata_rows
test_strict_section_rejection
test_resolution_precedence
test_public_adapter_unknown_key_fallback
test_declaration_magisk_repository_compatibility
test_selection_identity_metadata
test_no_eval_assignment
test_no_duplicate_parser_mapping

echo "config schema tests passed"
