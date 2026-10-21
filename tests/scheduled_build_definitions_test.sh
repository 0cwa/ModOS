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
  local expected_fsck_sysfs_sepolicy="${7}"

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
    [[ "$(toml_resolve_value fsck_sysfs_sepolicy '')" == "${expected_fsck_sysfs_sepolicy}" ]] ||
      fail "${file}: wrong fsck sysfs-SEPolicy selection"
    local expected_exfat=false
    [[ "${expected_family}" != lineageos ]] || expected_exfat=true
    toml_config_has exfatprogs_fsck ||
      fail "${file}: exfatprogs fsck selection must be explicitly declared"
    [[ "$(toml_resolve_value exfatprogs_fsck false)" == "${expected_exfat}" ]] ||
      fail "${file}: wrong exfatprogs fsck selection"
    [[ "$(toml_resolve_value lineage_vold_exfat_timeout '')" == false ]] ||
      fail "${file}: source-built vold must remain disabled until approved"
    [[ "$(toml_resolve_value force_update '')" == false ]] ||
      fail "${file}: scheduled force update must default to false"
    [[ "$(toml_resolve_value shizuku false)" == false ]] ||
      fail "${file}: scheduled Shizuku selection must remain default-off"

    local expected_microg=false
    [[ "${expected_family}" != lineageos ]] || expected_microg=true
    [[ "$(toml_resolve_value microg false)" == "${expected_microg}" ]] ||
      fail "${file}: wrong scheduled microG selection"

    for key in afsr alterinstaller bcr custota msd oemunlockonboot disable_system_updater; do
      [[ "$(toml_resolve_value "${key}" '')" == true ]] ||
        fail "${file}: ${key} must be enabled"
    done
    [[ "$(toml_resolve_value fdroid_privileged_extension '')" == false ]] ||
      fail "${file}: F-Droid privileged extension must remain default-off"
  )
}

check_definition   .github/schedules/grapheneos-shiba.toml   shiba grapheneos stable sda10 false false

check_definition   .github/schedules/lineageos-pdx235.toml   pdx235 lineageos nightly sda47 true true

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
  local expected_exfat=false
  [[ "${expected_family}" != lineageos ]] || expected_exfat=true
  grep -Fxq "exfatprogs_fsck=${expected_exfat}" "${output_file}" ||
    fail "${file}: loader emitted wrong exfatprogs fsck selection"
  local expected_fsck_sysfs_sepolicy=false
  [[ "${expected_family}" != lineageos ]] || expected_fsck_sysfs_sepolicy=true
  grep -Fxq "fsck_sysfs_sepolicy=${expected_fsck_sysfs_sepolicy}" "${output_file}" ||
    fail "${file}: loader emitted wrong fsck sysfs-SEPolicy selection"
  grep -Fxq "ADDITIONALS_EXFATPROGS_FSCK=${expected_exfat}" "${env_file}" ||
    fail "${file}: loader exported wrong exfatprogs fsck selection"
  grep -Fxq "ADDITIONALS_FSCK_SYSFS_SEPOLICY=${expected_fsck_sysfs_sepolicy}" "${env_file}" ||
    fail "${file}: loader exported wrong fsck sysfs-SEPolicy selection"
  grep -Fxq "lineage_vold_exfat_timeout=false" "${output_file}" ||
    fail "${file}: schedule loader unexpectedly enabled unqualified vold"
  grep -Fxq "ADDITIONALS_LINEAGE_VOLD_EXFAT_TIMEOUT=false" "${env_file}" ||
    fail "${file}: schedule loader exported unexpected vold selection"
  grep -Fxq 'ADDITIONALS_DISABLE_SYSTEM_UPDATER=true' "${env_file}" ||
    fail "${file}: loader did not export stock updater removal"
  grep -Fxq 'ADDITIONALS_BOOT_ANIMATION=true' "${env_file}" ||
    fail "${file}: loader did not export boot animation to the job environment"

  local expected_microg=false
  [[ "${expected_family}" != lineageos ]] || expected_microg=true
  grep -Fxq "microg=${expected_microg}" "${output_file}" ||
    fail "${file}: loader emitted wrong microG selection"
  grep -Fxq "ADDITIONALS_MICROG=${expected_microg}" "${env_file}" ||
    fail "${file}: loader exported wrong microG selection"

  rm -f "${output_file}" "${env_file}"
}

check_loader .github/schedules/grapheneos-shiba.toml shiba grapheneos
check_loader .github/schedules/lineageos-pdx235.toml pdx235 lineageos

# The loader must reject a tampered GrapheneOS schedule before exporting any
# build inputs. Test each forbidden switch using an otherwise-valid definition.
check_forbidden_grapheneos_module() {
  local module="${1}" fixture output_file env_file error_file
  local old="'ADDITIONALS[${module}]' = false"
  local new="'ADDITIONALS[${module}]' = true"
  fixture="$(mktemp --suffix=.toml)"
  output_file="$(mktemp)"
  env_file="$(mktemp)"
  error_file="$(mktemp)"
  cp .github/schedules/grapheneos-shiba.toml "${fixture}"
  check_loader "${fixture}" shiba grapheneos
  grep -Fxq "${old}" "${fixture}" || fail "missing GrapheneOS baseline: ${module}"
  sed -i "s/^'ADDITIONALS\\[${module}\\]' = false$/'ADDITIONALS[${module}]' = true/" "${fixture}"
  grep -Fxq "${new}" "${fixture}" || fail "could not activate ${module} in fixture"

  if GITHUB_OUTPUT="${output_file}" GITHUB_ENV="${env_file}" \
    SCHEDULE_DEFINITION="${fixture}" EXPECTED_ROM_FAMILY=grapheneos \
    bash src/ci/load_schedule_definition.sh >/dev/null 2>"${error_file}"; then
    fail "GrapheneOS schedule unexpectedly accepted ${module}"
  fi
  grep -Fq 'restricted to LineageOS pdx235' "${error_file}" ||
    fail "GrapheneOS ${module} failure did not come from the device-scope guard"
  [[ ! -s "${output_file}" && ! -s "${env_file}" ]] ||
    fail "GrapheneOS ${module} leaked forbidden selection into scheduled build outputs"
  rm -f "${fixture}" "${output_file}" "${env_file}" "${error_file}"
}

check_forbidden_grapheneos_module EXFATPROGS_FSCK
check_forbidden_grapheneos_module FSCK_SYSFS_SEPOLICY

# Both GrapheneOS entry points use this shared explicit-false callsite.
for module in exfatprogs-fsck fsck-sysfs-sepolicy; do
  grep -Eq "^[[:space:]]{6}${module}:[[:space:]]*false[[:space:]]*$" \
    .github/workflows/release.yml ||
    fail "GrapheneOS trigger must explicitly disable ${module}"
done
grep -Fq 'pdx235 fsck.exfat/SELinux modules require LineageOS pdx235' \
  .github/workflows/build-rom.yml ||
  fail "Shared workflow must reject non-pdx235 fsck/SELinux selections early"

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
grep -Fq "needs.schedule_config.outputs.exfatprogs_fsck == 'true'" .github/workflows/release-lineage.yml ||
  fail "LineageOS scheduled exfatprogs selection is not forwarded to the build"
grep -Fq "needs.schedule_config.outputs.lineage_vold_exfat_timeout == 'true'" .github/workflows/release-lineage.yml ||
  fail "LineageOS schedule does not route its opt-in vold module to the builder"
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

# A tampered GrapheneOS schedule may not select the Lineage-only vold daemon.
vold_tampered="$(mktemp --suffix=.toml)"
vold_output="$(mktemp)"
vold_env="$(mktemp)"
vold_error="$(mktemp)"
python3 - "${vold_tampered}" <<'PYVOLD'
from pathlib import Path
import sys

source = Path(".github/schedules/grapheneos-shiba.toml").read_text()
old = "'ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]' = false"
assert source.count(old) == 1
Path(sys.argv[1]).write_text(source.replace(old, old.replace("false", "true")))
PYVOLD
if SCHEDULE_DEFINITION="${vold_tampered}" GITHUB_OUTPUT="${vold_output}" \
   GITHUB_ENV="${vold_env}" EXPECTED_ROM_FAMILY=grapheneos \
   bash src/ci/load_schedule_definition.sh >/dev/null 2>"${vold_error}"; then
  fail "GrapheneOS schedule accepted the Lineage-only vold module"
fi
grep -Fq 'Source-built vold is supported only for LineageOS pdx235' "${vold_error}" || {
  cat "${vold_error}" >&2
  fail "GrapheneOS vold rejection did not come from the device-scope guard"
}
[[ ! -s "${vold_output}" && ! -s "${vold_env}" ]] ||
  fail "GrapheneOS vold schedule leaked forbidden outputs"
rm -f -- "${vold_tampered}" "${vold_output}" "${vold_env}" "${vold_error}"

echo "scheduled build definition tests passed"

grep -Fq "needs.schedule_config.outputs.microg == 'true'" .github/workflows/release-lineage.yml ||
  fail 'LineageOS schedule does not forward microG'

# Cron events have no workflow_dispatch inputs. When a scheduled boolean is
# false, its reusable-workflow binding must not fall through to the missing
# manual input (an empty string rather than a typed boolean).
python3 - <<'PYTEST'
from pathlib import Path

workflow = Path(".github/workflows/release.yml").read_text()
bindings = {}
for line in workflow.splitlines():
    if line.startswith("      ") and ": ${{ " in line:
        name, value = line.strip().split(": ", 1)
        bindings[name] = value

scheduled_boolean_outputs = {
    "root": "root",
    "alterinstaller": "alterinstaller",
    "bcr": "bcr",
    "custota": "custota",
    "msd": "msd",
    "oemunlockonboot": "oemunlockonboot",
    "disable-system-updater": "disable_system_updater",
    "fdroid-privileged-extension": "fdroid_privileged_extension",
}
for name, output in scheduled_boolean_outputs.items():
    expected = (
        "${{ github.event_name == 'schedule' && "
        f"needs.preflight.outputs.{output} == 'true' || "
        f"(github.event_name != 'schedule' && inputs.{name}) }}}}"
    )
    actual = bindings.get(name)
    assert actual == expected, (
        f"{name}: scheduled false must remain boolean false, not use an absent "
        f"manual input: {actual!r}"
    )
print("GrapheneOS scheduled boolean routing checks passed")
PYTEST
