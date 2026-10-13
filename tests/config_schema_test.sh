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

# Exercise lookup and literal assignment for every configured field without
# duplicating the schema inventory, defaults or policies in a second table.
test_metadata_rows() {
  local canonical accepted section value
  for canonical in "${CONFIG_SCHEMA_KEYS[@]}"; do
    accepted="${CONFIG_SCHEMA_ACCEPTED_KEY[${canonical}]}"
    section="${CONFIG_SCHEMA_SECTION[${canonical}]}"
    config_schema_lookup_key "${section}" "${accepted}" false
    assert_equals "${canonical}" "${CONFIG_SCHEMA_CANONICAL}" "strict alias lookup"
    config_schema_lookup_key '' "${accepted}" true
    assert_equals "${canonical}" "${CONFIG_SCHEMA_CANONICAL}" "legacy alias lookup"
    if [[ "${CONFIG_SCHEMA_TYPE[${canonical}]}" == boolean ]]; then
      value=false
    else
      value="fixture-${canonical}"
    fi
    config_schema_apply_value "${canonical}" "${value}"
    assert_destination_value "${canonical}" \
      "${CONFIG_SCHEMA_SHELL_DESTINATION[${canonical}]}" \
      "${CONFIG_SCHEMA_SHELL_DESTINATION_KIND[${canonical}]}" \
      "${CONFIG_SCHEMA_SHELL_DESTINATION_KEY[${canonical}]}" "${value}"
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

test_no_eval_assignment() {
  local marker value
  marker="$(mktemp)"
  rm -f -- "${marker}"
  value="literal \$(touch ${marker}) \`touch ${marker}\`"
  config_schema_apply_value device_name "${value}"
  assert_equals "${value}" "${DEVICE_NAME}" "literal string assignment"
  [[ ! -e "${marker}" ]] || fail "schema assignment evaluated input"
}


test_metadata_rows
test_strict_section_rejection
test_resolution_precedence
test_public_adapter_unknown_key_fallback
test_declaration_magisk_repository_compatibility
test_no_eval_assignment

echo "config schema tests passed"
