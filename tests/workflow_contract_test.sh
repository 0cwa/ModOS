#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

set -euo pipefail

WORKFLOW_DIR=".github/workflows"
REUSABLE="${WORKFLOW_DIR}/build-rom.yml"
RELEASE="${WORKFLOW_DIR}/release.yml"

fail() {
  echo "$*" >&2
  exit 1
}

assert_contains() {
  local file="${1}"
  local pattern="${2}"
  local context="${3}"

  grep -Eq -- "${pattern}" "${file}" ||
    fail "${context}: ${file} does not match ${pattern}"
}

assert_not_contains() {
  local file="${1}"
  local pattern="${2}"
  local context="${3}"

  if grep -Eq -- "${pattern}" "${file}"; then
    fail "${context}: ${file} unexpectedly matches ${pattern}"
  fi
}

assert_thin_trigger() {
  local file="${1}"
  local family="${2}"

  [[ -f "${file}" ]] || fail "missing ${family} release trigger: ${file}"
  assert_contains \
    "${file}" \
    'uses:[[:space:]]*\./\.github/workflows/build-rom\.yml' \
    "${family} trigger must call the shared workflow"
  assert_contains \
    "${file}" \
    "rom-family:[[:space:]]*['\"]?${family}['\"]?" \
    "${family} trigger must select its ROM family"
  assert_not_contains \
    "${file}" \
    'uses:[[:space:]]*actions/checkout@' \
    "thin triggers must not duplicate checkout/build steps"
  assert_not_contains \
    "${file}" \
    '(^|[[:space:]])ref:[[:space:]]*['\"]?lineage['\"]?([[:space:]#]|$)' \
    "release triggers must not check out a divergent lineage branch"
}

assert_dispatch_default() {
  local file="${1}"
  local input="${2}"
  local expected="${3}"
  local actual
  local trigger='workflow_call'

  if grep -Eq '^[[:space:]]{2}workflow_dispatch:[[:space:]]*$' "${file}"; then
    trigger='workflow_dispatch'
  fi

  actual="$(awk -v input="${input}" -v trigger="${trigger}" '
    $0 ~ "^[[:space:]]{2}" trigger ":[[:space:]]*$" { in_trigger = 1; next }
    in_trigger && $0 ~ "^[[:space:]]{2}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_trigger && $0 ~ "^[[:space:]]{6}" input ":[[:space:]]*$" { in_input = 1; next }
    in_input && $0 ~ "^[[:space:]]{6}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_input && $0 ~ "^[[:space:]]+default:[[:space:]]*" {
      sub(/^.*default:[[:space:]]*/, "")
      gsub(/[[:space:]\047"]/, "")
      print
      exit
    }
  ' "${file}")"

  [[ "${actual}" == "${expected}" ]] ||
    fail "${file}: ${input} default expected ${expected}, got ${actual:-missing}"
}

assert_workflow_input_contract() {
  local file="${1}"
  local input="${2}"
  local expected_type="${3}"
  local expected_default="${4}"
  local actual
  local trigger='workflow_call'

  if grep -Eq '^[[:space:]]{2}workflow_dispatch:[[:space:]]*$' "${file}"; then
    trigger='workflow_dispatch'
  fi

  actual="$(awk -v input="${input}" -v trigger="${trigger}" '
    $0 ~ "^[[:space:]]{2}" trigger ":[[:space:]]*$" { in_trigger = 1; next }
    in_trigger && $0 ~ "^[[:space:]]{2}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_trigger && $0 ~ "^[[:space:]]{6}" input ":[[:space:]]*$" { in_input = 1; next }
    in_input && $0 ~ "^[[:space:]]{6}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_input && $0 ~ "^[[:space:]]+type:[[:space:]]*" {
      sub(/^.*type:[[:space:]]*/, "")
      gsub(/[[:space:]\047"]/, "")
      type = $0
    }
    in_input && $0 ~ "^[[:space:]]+default:[[:space:]]*" {
      sub(/^.*default:[[:space:]]*/, "")
      gsub(/[[:space:]\047"]/, "")
      default_value = $0
    }
    END {
      if (type != "" && default_value != "") print type "|" default_value
    }
  ' "${file}")"

  [[ "${actual}" == "${expected_type}|${expected_default}" ]] ||
    fail "${file}: ${input} input expected ${expected_type}|${expected_default}, got ${actual:-missing}"
}

test_reusable_workflow() {
  [[ -f "${REUSABLE}" ]] || fail "missing reusable ROM workflow: ${REUSABLE}"
  assert_contains \
    "${REUSABLE}" \
    'workflow_call:' \
    "shared ROM workflow must be reusable"
  assert_contains \
    "${REUSABLE}" \
    'rom-family:' \
    "shared ROM workflow must accept a ROM family"
  assert_dispatch_default "${REUSABLE}" boot-animation false
  assert_dispatch_default "${REUSABLE}" afsr true
  assert_contains \
    "${REUSABLE}" \
    'ADDITIONALS_BOOT_ANIMATION' \
    "shared workflow must pass the boot-animation selection"
  assert_contains \
    "${REUSABLE}" \
    'ADDITIONALS_AFSR' \
    "shared workflow must pass the AFSR selection"
  local contract input expected_type expected_default
  for contract in \
    'afsr:boolean:true' \
    'alterinstaller:boolean:true' \
    'bcr:boolean:true' \
    'custota:boolean:true' \
    'msd:boolean:true' \
    'oemunlockonboot:boolean:true' \
    'disable-system-updater:boolean:false' \
    'fdroid-privileged-extension:boolean:false'; do
    IFS=: read -r input expected_type expected_default <<<"${contract}"
    assert_workflow_input_contract \
      "${REUSABLE}" "${input}" "${expected_type}" "${expected_default}"
  done
  assert_contains \
    "${REUSABLE}" \
    'uses:[[:space:]]*actions/checkout@' \
    "shared ROM workflow must own checkout"
  assert_contains \
    "${REUSABLE}" \
    'MODULE_SELECTION_FINGERPRINT' \
    "shared workflow must retain the full selection fingerprint as metadata"
  assert_contains \
    "${REUSABLE}" \
    '"artifact_name": artifact_name' \
    "selection metadata must bind the exact output filename"
  assert_contains \
    "${REUSABLE}" \
    '"grapheneos_version": version' \
    "selection metadata must bind the GrapheneOS version"
  assert_contains \
    "${REUSABLE}" \
    '"schema_version": 2' \
    "selection metadata must use the variant-aware schema"
  assert_contains \
    "${REUSABLE}" \
    'OUTPUT_SCOPE' \
    "shared workflow must set an explicit output scope"
  assert_contains \
    "${REUSABLE}" \
    'enforce_output_policy' \
    "shared workflow must enforce policy before release or upload"
  assert_contains \
    "${REUSABLE}" \
    'enforce_publication_evidence' \
    "shared workflow must enforce publication evidence"
  assert_contains \
    "${REUSABLE}" \
    'MODULE_SELECTION_FINGERPRINT="\$\{MODULE_SELECTION_FINGERPRINT_ROOTLESS\}"' \
    "paired rootless cleanup must bind the rootless fingerprint"
  assert_contains \
    "${REUSABLE}" \
    'MODULE_SELECTION_FINGERPRINT="\$\{MODULE_SELECTION_FINGERPRINT_MAGISK\}"' \
    "paired Magisk cleanup must bind the Magisk fingerprint"
  assert_not_contains \
    "${REUSABLE}" \
    'actions/upload-artifact@' \
    "local-unpublished outputs must not be uploaded"
  assert_not_contains \
    "${REUSABLE}" \
    '(^|[[:space:]])ref:[[:space:]]*['\"]?lineage['\"]?([[:space:]#]|$)' \
    "shared ROM workflow must not check out a divergent lineage branch"
}

test_release_triggers() {
  local lineage="${WORKFLOW_DIR}/release-lineage.yml"

  assert_thin_trigger "${RELEASE}" grapheneos

  [[ -f "${lineage}" ]] || fail "missing LineageOS release trigger: ${lineage}"
  assert_contains "${lineage}" 'schedule:' "LineageOS release must remain scheduled"
  assert_contains "${lineage}" 'uses:[[:space:]]*\./\.github/workflows/build-rom\.yml'     "LineageOS release must call the shared workflow"
  assert_contains "${lineage}" 'rom-family:[[:space:]]*lineageos'     "LineageOS manual build must select the LineageOS profile"
  assert_contains "${lineage}" 'SCHEDULE_DEFINITION:[[:space:]]*\.github/schedules/lineageos-pdx235\.toml'     "LineageOS schedule must use the pdx235 definition"
  assert_contains "${RELEASE}" '^[[:space:]]+- build-only[[:space:]]*$'     "GrapheneOS manual release must expose build-only mode"
  assert_contains "${lineage}" '^[[:space:]]+- build-only[[:space:]]*$'     "LineageOS manual release must expose build-only mode"

  assert_dispatch_default "${RELEASE}" device-id shiba
  assert_dispatch_default "${RELEASE}" root true
  assert_dispatch_default "${RELEASE}" alterinstaller true
  assert_dispatch_default "${RELEASE}" bcr true
  assert_dispatch_default "${RELEASE}" custota true
  assert_dispatch_default "${RELEASE}" msd true
  assert_dispatch_default "${RELEASE}" oemunlockonboot true
  assert_dispatch_default "${RELEASE}" disable-system-updater false
  assert_dispatch_default "${RELEASE}" fdroid-privileged-extension false
  assert_dispatch_default "${RELEASE}" boot-animation false
  assert_dispatch_default "${RELEASE}" compatible-sepolicy-patching false
  assert_dispatch_default "${lineage}" device-id pdx235
  assert_dispatch_default "${lineage}" root true
  assert_dispatch_default "${lineage}" compatible-sepolicy-patching true
  assert_dispatch_default "${lineage}" disable-system-updater false
  assert_dispatch_default "${lineage}" boot-animation false
}

test_config_loading_isolated_to_scheduled_releases() {
  local lineage="${WORKFLOW_DIR}/release-lineage.yml"

  assert_contains "${RELEASE}"     'SCHEDULE_DEFINITION:[[:space:]]*\.github/schedules/grapheneos-shiba\.toml'     "GrapheneOS schedule must use the shiba definition"
  assert_contains "${lineage}"     'SCHEDULE_DEFINITION:[[:space:]]*\.github/schedules/lineageos-pdx235\.toml'     "LineageOS schedule must use the pdx235 definition"
  assert_not_contains "${lineage}" 'env\.toml'     "LineageOS schedule must not inherit repository-wide local env.toml"
}

test_release_configuration_forwarding() {
  local loader="src/ci/load_schedule_definition.sh"

  assert_contains "${RELEASE}"     'name:[[:space:]]*Load scheduled build definition'     "scheduled GrapheneOS release must load a checked-in definition"
  assert_contains "${RELEASE}"     'if:[[:space:]]*github\.event_name == .schedule.'     "scheduled configuration must be schedule-only"
  assert_contains "${RELEASE}"     'run:[[:space:]]*bash src/ci/load_schedule_definition\.sh'     "GrapheneOS schedule must use the shared definition loader"
  assert_contains "${loader}"     'check_toml_env "\$\{SCHEDULE_DEFINITION\}"'     "schedule loader must use the typed TOML parser"
  assert_contains "${loader}"     'toml_config_has "\$\{key\}"'     "schedule loader must require every declared schedule key"
  assert_contains "${loader}"     'disable_system_updater="\$\(toml_resolve_value disable_system_updater'     "schedule loader must resolve updater removal"
  assert_contains "${loader}"     'boot_animation="\$\(toml_resolve_value boot_animation'     "schedule loader must resolve the boot-animation selection"
  assert_contains "${loader}"     'compatible_sepolicy_patching="\$\(toml_resolve_value compatible_sepolicy_patching'     "schedule loader must resolve compatible-SEPolicy selection"
  assert_contains "${loader}"     'force_update="\$\(toml_resolve_value force_update'     "schedule loader must resolve FORCE_UPDATE"
  assert_contains "${RELEASE}"     'name:[[:space:]]*Resolve expected selection identity'     "release preflight must resolve the canonical selection identity"
  assert_contains "${RELEASE}"     'module_selection_fingerprint'     "release preflight must use the shared selection fingerprint"
  assert_contains "${RELEASE}"     'expected_variant:[[:space:]]*\$\{\{ steps\.selection_identity\.outputs\.expected_variant \}\}'     "release preflight must expose the resolved identity"
  assert_contains "${RELEASE}"     'EXPECTED_VARIANT:[[:space:]]*\$\{\{ steps\.selection_identity\.outputs\.expected_variant \}\}'     "existing-build preflight must receive the resolved identity"
  assert_contains "${RELEASE}"     'force_update:[[:space:]]*\$\{\{ steps\.scheduled_config\.outputs\.force_update \}\}'     "preflight must expose scheduled FORCE_UPDATE as a job output"
  assert_contains "${RELEASE}"     'FORCE_UPDATE:[[:space:]]*\$\{\{ github\.event_name == .schedule. && steps\.scheduled_config\.outputs\.force_update \|\| false \}\}'     "existing-build preflight must receive scheduled FORCE_UPDATE"
  assert_contains "${RELEASE}"     'device-id:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.device_id \|\| inputs\.device-id \}\}'     "manual device input must remain authoritative"
  assert_contains "${RELEASE}"     'disable-system-updater:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.disable_system_updater == .true. \|\| inputs\.disable-system-updater \}\}'     "updater removal must be forwarded from scheduled definition or manual input"
  assert_contains "${RELEASE}"     'boot-animation:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.boot_animation == .true. \|\| inputs\.boot-animation \|\| false \}\}'     "boot animation must be forwarded from scheduled definition or manual input"
  assert_contains "${RELEASE}"     'compatible-sepolicy-patching:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.compatible_sepolicy_patching == .true.'     "compatible SEPolicy must be forwarded from the scheduled definition"
  assert_contains "${RELEASE}"     'root:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.root == .true. \|\| inputs\.root \}\}'     "root must explicitly coerce the scheduled string output to boolean"
  assert_contains "${RELEASE}"     'magisk-preinit-device:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.magisk_preinit_device'     "scheduled preinit configuration must be forwarded"
  assert_contains "${RELEASE}"     'update-channel:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.update_channel'     "scheduled update channel must be forwarded"
}

test_module_forwarding_contract() {
  local contract input config output
  local loader="src/ci/load_schedule_definition.sh"

  for contract in \
    'afsr:AFSR:afsr' \
    'alterinstaller:ALTERINSTALLER:alterinstaller' \
    'bcr:BCR:bcr' \
    'custota:CUSTOTA:custota' \
    'msd:MSD:msd' \
    'oemunlockonboot:OEMUNLOCKONBOOT:oemunlockonboot' \
    'disable-system-updater:DISABLE_SYSTEM_UPDATER:disable_system_updater' \
    'fdroid-privileged-extension:FDROID_PRIVILEGED_EXTENSION:fdroid_privileged_extension'; do
    IFS=: read -r input config output <<<"${contract}"

    assert_contains \
      "${loader}" \
      "toml_resolve_value ${output}" \
      "schedule loader must resolve ${config} through the typed contract"
    assert_contains \
      "${loader}" \
      "echo \"${output}=" \
      "schedule loader must expose ${output}"
    assert_contains \
      "${RELEASE}" \
      "steps\\.scheduled_config\\.outputs\\.${output}" \
      "preflight must expose ${output}"
    assert_contains \
      "${RELEASE}" \
      "ADDITIONALS_${config}:[[:space:]].*steps\\.scheduled_config\\.outputs\\.${output}.*inputs\\.${input}" \
      "expected identity must use scheduled/manual ${input}"
    assert_contains \
      "${RELEASE}" \
      "needs\\.preflight\\.outputs\\.${output}[[:space:]]*==[[:space:]]*['\"]true['\"].*inputs\\.${input}" \
      "build invocation must use scheduled/manual ${input}"
    assert_contains \
      "${REUSABLE}" \
      "ADDITIONALS_${config}:[[:space:]].*inputs\\.${input}" \
      "reusable build must map ${config} from input ${input}"
  done
}

test_publication_identity_is_step_scoped() {
  assert_contains \
    "${WORKFLOW_DIR}/build-rom.yml" \
    'GIT_COMMIT_EMAIL:[[:space:]]*\$\{\{ secrets\.EMAIL \}\}' \
    "publication identity email must be step-scoped"
  assert_contains \
    "${WORKFLOW_DIR}/build-rom.yml" \
    'GIT_COMMIT_NAME:[[:space:]]*\$\{\{ github\.repository_owner \}\}' \
    "publication identity name must be step-scoped"
  assert_contains \
    "src/ci/publish_ota.sh" \
    'git config user\.email "\$\{GIT_COMMIT_EMAIL-\}"' \
    "publication helper must configure local email from the step environment"
  assert_contains \
    "src/ci/publish_ota.sh" \
    'git config user\.name "\$\{GIT_COMMIT_NAME-\}"' \
    "publication helper must configure local name from the step environment"
  assert_not_contains \
    "${WORKFLOW_DIR}/build-rom.yml" \
    'git config user\.email .*secrets\.EMAIL' \
    "publication must not interpolate the email secret in shell source"
}

test_workflow_inventory_is_intentional() (
  local expected actual

  expected="$(printf '%s\n' \
    build-rom.yml \
    ci.yml \
    multi-release.yml \
    release-lineage.yml \
    release.yml \
    renovate.yml)"

  actual="$(find "${WORKFLOW_DIR}" -maxdepth 1 -type f \
    \( -name '*.yml' -o -name '*.yaml' \) -exec basename {} \; | sort)"

  [[ "${actual}" == "${expected}" ]] || {
    printf 'unexpected workflow inventory\nexpected:\n%s\nactual:\n%s\n' \
      "${expected}" "${actual}" >&2
    exit 1
  }
)

test_no_lineage_checkout_anywhere() {
  local file

  while IFS= read -r -d '' file; do
    assert_not_contains \
      "${file}" \
      '(^|[[:space:]])ref:[[:space:]]*['\"]?lineage['\"]?([[:space:]#]|$)' \
      "workflows must use the main branch implementation"
  done < <(find "${WORKFLOW_DIR}" -maxdepth 1 -type f \
    \( -name '*.yml' -o -name '*.yaml' \) -print0)
}

test_reusable_workflow
test_release_triggers
test_config_loading_isolated_to_scheduled_releases
test_release_configuration_forwarding
test_module_forwarding_contract
test_publication_identity_is_step_scoped
test_workflow_inventory_is_intentional
test_no_lineage_checkout_anywhere

echo "Workflow contract tests passed"
