#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "$*" >&2
  exit 1
}

check_definition() {
  local file="${1}"
  local expected_device="${2}"
  local expected_family="${3}"
  local expected_channel="${4}"
  local expected_preinit="${5}"
  local expected_sepolicy="${6}"

  (
    source src/util_functions.sh
    check_toml_env "${file}"

    [[ "$(toml_resolve_value device_name '')" == "${expected_device}" ]] ||
      fail "${file}: wrong device"
    [[ "$(toml_resolve_value rom_family '')" == "${expected_family}" ]] ||
      fail "${file}: wrong ROM family"
    [[ "$(toml_resolve_value update_channel '')" == "${expected_channel}" ]] ||
      fail "${file}: wrong update channel"
    [[ "$(toml_resolve_value root '')" == false ]] ||
      fail "${file}: legacy scheduled ROOT fallback must remain false"
    [[ "$(toml_resolve_value root_mode '')" == both ]] ||
      fail "${file}: scheduled build must request both root variants"
    [[ "$(toml_resolve_value magisk_preinit '')" == "${expected_preinit}" ]] ||
      fail "${file}: wrong Magisk preinit definition"
    [[ "$(toml_resolve_value boot_animation '')" == true ]] ||
      fail "${file}: scheduled custom boot animation must be enabled"
    [[ "$(toml_resolve_value compatible_sepolicy_patching '')" == "${expected_sepolicy}" ]] ||
      fail "${file}: wrong compatible-SEPolicy selection"
    [[ "$(toml_resolve_value force_update '')" == false ]] ||
      fail "${file}: scheduled force update must default to false"

    for key in afsr alterinstaller bcr custota msd oemunlockonboot disable_system_updater; do
      [[ "$(toml_resolve_value "${key}" '')" == true ]] ||
        fail "${file}: ${key} must be enabled"
    done
    [[ "$(toml_resolve_value fdroid_privileged_extension '')" == false ]] ||
      fail "${file}: F-Droid privileged extension must remain default-off"
  )
}

check_definition   .github/schedules/grapheneos-shiba.toml   shiba grapheneos stable sda10 false

check_definition   .github/schedules/lineageos-pdx235.toml   pdx235 lineageos nightly sda47 true

check_loader() {
  local file="${1}"
  local expected_device="${2}"
  local expected_family="${3}"
  local output_file env_file

  output_file="$(mktemp)"
  env_file="$(mktemp)"
  GITHUB_OUTPUT="${output_file}" \
  GITHUB_ENV="${env_file}" \
  SCHEDULE_DEFINITION="${file}" \
  EXPECTED_ROM_FAMILY="${expected_family}" \
    bash src/ci/load_schedule_definition.sh >/dev/null

  grep -Fxq "device_id=${expected_device}" "${output_file}" ||
    fail "${file}: loader emitted wrong device"
  grep -Fxq "rom_family=${expected_family}" "${output_file}" ||
    fail "${file}: loader emitted wrong ROM family"
  grep -Fxq 'root_mode=both' "${output_file}" ||
    fail "${file}: loader did not emit paired root mode"
  grep -Fxq 'ROOT_MODE=both' "${env_file}" ||
    fail "${file}: loader did not export paired root mode"
  grep -Fxq 'boot_animation=true' "${output_file}" ||
    fail "${file}: loader did not enable scheduled boot animation"
  grep -Fxq 'ADDITIONALS_DISABLE_SYSTEM_UPDATER=true' "${env_file}" ||
    fail "${file}: loader did not export stock updater removal"
  grep -Fxq 'ADDITIONALS_BOOT_ANIMATION=true' "${env_file}" ||
    fail "${file}: loader did not export boot animation to the job environment"

  rm -f "${output_file}" "${env_file}"
}

check_loader .github/schedules/grapheneos-shiba.toml shiba grapheneos
check_loader .github/schedules/lineageos-pdx235.toml pdx235 lineageos

grep -Fq 'SCHEDULE_DEFINITION: .github/schedules/grapheneos-shiba.toml' .github/workflows/release.yml ||
  fail "GrapheneOS cron does not reference the shiba schedule definition"
grep -Fq 'SCHEDULE_DEFINITION: .github/schedules/lineageos-pdx235.toml' .github/workflows/release-lineage.yml ||
  fail "LineageOS cron does not reference the pdx235 schedule definition"

grep -Fq 'needs.preflight.outputs.boot_animation' .github/workflows/release.yml ||
  fail "GrapheneOS scheduled boot-animation selection is not forwarded from the definition"
grep -Fq 'needs.schedule_config.outputs.boot_animation' .github/workflows/release-lineage.yml ||
  fail "LineageOS scheduled boot-animation selection is not forwarded from the definition"

grep -Fq 'needs.preflight.outputs.root_mode' .github/workflows/release.yml ||
  fail "GrapheneOS scheduled root mode is not forwarded from the definition"
grep -Fq 'needs.schedule_config.outputs.root_mode' .github/workflows/release-lineage.yml ||
  fail "LineageOS scheduled root mode is not forwarded from the definition"

grep -Fq 'needs.preflight.outputs.disable_system_updater' .github/workflows/release.yml ||
  fail "GrapheneOS scheduled updater removal is not forwarded from the definition"
grep -Fq 'needs.schedule_config.outputs.disable_system_updater' .github/workflows/release-lineage.yml ||
  fail "LineageOS scheduled updater removal is not forwarded from the definition"
grep -Fq 'check_existing_pair.sh' .github/workflows/release.yml ||
  fail "GrapheneOS paired schedule is not protected by paired existing-build preflight"

if grep -Eq 'boot-animation:[[:space:]]*true' .github/workflows/release.yml .github/workflows/release-lineage.yml; then
  fail "Scheduled boot animation must come from the schedule definition, not a hardcoded workflow literal"
fi

if grep -Eq 'scheduled_build:|schedule_config:' .github/workflows/release-lineage.yml; then
  :
else
  fail "LineageOS schedule jobs are missing"
fi

echo "scheduled build definition tests passed"
