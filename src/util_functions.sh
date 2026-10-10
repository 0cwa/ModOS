#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

# This script is a part of the main script and is responsible for the utility functions used in the main script.

source src/declarations.sh
_util_functions_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "${_util_functions_source_dir}/config_schema.sh"
source "${_util_functions_source_dir}/source_care_map_policy.sh"
unset _util_functions_source_dir
source src/exchange.sh
source src/fetcher.sh
source src/verifier.sh
source src/debug_module_setup.sh
source src/rom_profiles.sh

declare -a LOCKED_EXECUTABLE_TOOLS=(avbroot afsr custota-tool)

function is_locked_executable_tool() {
  local candidate="${1}"
  local locked_tool

  for locked_tool in "${LOCKED_EXECUTABLE_TOOLS[@]}"; do
    [[ "${candidate}" == "${locked_tool}" ]] && return 0
  done
  return 1
}

function resolve_root_mode() {
  local requested="${ROOT_MODE:-}"

  if [[ -z "${requested}" ]]; then
    case "${ADDITIONALS[ROOT]}" in
      true) requested='magisk' ;;
      false) requested='rootless' ;;
      *)
        echo "Error: legacy ROOT selection must be true or false." >&2
        return 1
        ;;
    esac
  fi

  case "${requested}" in
    rootless|magisk|both) ;;
    *)
      echo "Error: ROOT_MODE must be rootless, magisk, or both." >&2
      return 1
      ;;
  esac

  if [[ "${requested}" == 'magisk' || "${requested}" == 'both' ]]; then
    if [[ -z "${MAGISK[PREINIT]}" ]]; then
      echo "Error: Magisk root modes require MAGISK_PREINIT." >&2
      return 1
    fi
  fi

  RESOLVED_ROOT_MODE="${requested}"
  export RESOLVED_ROOT_MODE
}

function root_mode_includes_magisk() {
  resolve_root_mode >/dev/null || return 1
  [[ "${RESOLVED_ROOT_MODE}" == 'magisk' || "${RESOLVED_ROOT_MODE}" == 'both' ]]
}

# Function to check and download the dependencies
# This function checks for the required tools and downloads them if not found depending on the configuration done in the declarations file
function check_and_download_dependencies() {
  make_directories

  # Check for Python requirements
  if ! command -v python3 &>/dev/null; then
    echo -e "Python 3 is required to run this script.\nExiting..."
    exit 1
  fi

  # Check if retry config is enabled
  if [[ "${ADDITIONALS[RETRY]}" == "true" ]]; then
    RETRY="true"
  else
    RETRY="false"
  fi

  # Check for required tools
  # If they're present, continue with the script
  # Else, download them by checking version from declarations
  tools=$(supported_tools "cdd") # Call the function and capture its output

  # Convert the space-separated string back into an array
  IFS=' ' read -r -a tools_array <<<"${tools}"

  local -a executable_tools=()
  local tool flag
  for tool in "${tools_array[@]}"; do
    flag="$(flag_check "${tool}")"

    if is_locked_executable_tool "${tool}"; then
      if [[ "${flag}" == "true" ]]; then
        executable_tools+=("${tool}")
      fi
      continue
    fi

  done

  # Authenticate the complete executable set before extracting any executable.
  if ((${#executable_tools[@]})); then
    bootstrap_executable_tools "${executable_tools[@]}" || return 1
  fi

  for tool in "${tools_array[@]}"; do
    flag="$(flag_check "${tool}")"

    if is_locked_executable_tool "${tool}"; then
      continue
    fi

    if [[ "${flag}" == 'false' ]]; then
      echo -e "\`${tool}\` is **NOT** enabled in the configuration.\nSkipping...\n"
      continue
    fi

    if [ -f "${WORKDIR}/modules/${tool}.zip" ]; then
      echo -e "\`${tool}.zip\` file already exists in \`${WORKDIR}/modules\`."
      continue
    fi

    if [ -d "${WORKDIR}/tools/${tool}" ]; then
      echo -e "\`${tool}\` file already exists in \`${WORKDIR}/tools\`."
      continue
    fi

    RETRY_COUNT=0 # Reset retry count for each tool
    while true; do
      # Download the tool and verify the download
      download_dependencies "${tool}"
      verify_downloads "${tool}"
      [[ "${ADDITIONALS[RETRY]}" == "true" ]] && [[ "${RETRY}" == "true" ]] || break
    done
  done

  # Retry logic for magisk. ROOT_MODE=both downloads it once for the
  # secondary output while preserving the legacy ROOT boolean path.
  if root_mode_includes_magisk; then
    RETRY_COUNT=0 # Reset retry count for magisk
    while true; do
      # Magisk is an exception as it is an APK and hence we do the get call directly and verify
      URL="${MAGISK[URL]}/releases/download/${VERSION[MAGISK]}/Magisk-${VERSION[MAGISK]}.apk"
      echo "URL for \`magisk\`: ${URL}"
      get "magisk" "${URL}"
      verify_downloads "magisk"

      [[ "${ADDITIONALS[RETRY]}" == "true" ]] && [[ "${RETRY}" == "true" ]] || break
    done
  fi
}

function bootstrap_executable_tools() {
  local -a selected=("$@")
  local report="${WORKDIR}/reports/executable-tools.json"

  python3 "$(git rev-parse --show-toplevel)/src/bootstrap_executable_tools.py" \
    --workdir "${WORKDIR}" \
    install \
    --report "${report}" \
    "${selected[@]}"
}

function resolve_executable_tool() {
  local tool="${1}"

  python3 "$(git rev-parse --show-toplevel)/src/bootstrap_executable_tools.py" \
    --workdir "${WORKDIR}" \
    resolve "${tool}"
}

function run_executable_tool() {
  local tool="${1}"
  shift

  python3 "$(git rev-parse --show-toplevel)/src/bootstrap_executable_tools.py" \
    --workdir "${WORKDIR}" \
    run "${tool}" -- "$@"
}

# Function to check the flag status
# If flag for a tool is disabled, it is not downloaded
function flag_check() {
  local tool="${1}"
  local tool_upper_case=$(echo "${tool}" | tr '[:lower:]' '[:upper:]')

  if [[ "${tool}" == "my-avbroot-setup" ]]; then
    FLAG="${ADDITIONALS[MY_AVBROOT_SETUP]}"
  elif [[ "${tool}" == "custota-tool" ]]; then
    FLAG="${ADDITIONALS[CUSTOTA_TOOL]}"
  else
    FLAG="${ADDITIONALS[$tool_upper_case]}"
  fi

  if [[ "${FLAG}" == 'true' ]]; then
    echo 'true'
  else
    echo 'false'
  fi
}

# Append enabled my-avbroot-setup modules while preserving the historical
# argument order: all module archives first, followed by their signatures.
function append_enabled_module_arguments() {
  local args_name="${1}"
  local -n args_ref="${args_name}"
  local entry module flag
  local -a enabled_modules=()
  local -a module_entries=(
    "custota:CUSTOTA"
    "msd:MSD"
    "bcr:BCR"
    "oemunlockonboot:OEMUNLOCKONBOOT"
    "alterinstaller:ALTERINSTALLER"
    "boot-animation:BOOT_ANIMATION"
  )

  for entry in "${module_entries[@]}"; do
    module="${entry%%:*}"
    flag="${entry#*:}"

    if [[ "${ADDITIONALS[${flag}]}" == 'true' ]]; then
      enabled_modules+=("${module}")
    fi
  done

  for module in "${enabled_modules[@]}"; do
    args_ref+=("--module-${module}" "${WORKDIR}/modules/${module}.zip")
  done

  for module in "${enabled_modules[@]}"; do
    args_ref+=("--module-${module}-sig" "${WORKDIR}/signatures/${module}.zip.sig")
  done
}

# Validate and register the optional local boot-animation module before any
# patch command runs. The helper's Module/ModuleRequirements API is the same
# API used by src/debugmod.py at the pinned helper revision.
function prepare_boot_animation_module() {
  local helper_root="${1}"
  local payload_path dark_payload_path init_file registry_file module_source

  if [[ "${ADDITIONALS[BOOT_ANIMATION]}" != 'true' ]]; then
    return 0
  fi

  _resolve_boot_animation_payloads || return 1
  payload_path="${BOOT_ANIMATION_LIGHT_PAYLOAD}"
  dark_payload_path="${BOOT_ANIMATION_DARK_PAYLOAD}"

  if ! python3 src/boot_animation.py validate "${payload_path}" >/dev/null ||
    ! python3 src/boot_animation.py validate "${dark_payload_path}" >/dev/null; then
    echo "Error: boot animation validation failed; refusing to patch." >&2
    return 1
  fi

  init_file="${helper_root}/lib/modules/__init__.py"
  registry_file="${helper_root}/lib/modules/registry.py"
  module_source="${helper_root}/lib/modules/boot_animation.py"
  if [[ ! -f "${init_file}" || -L "${init_file}" ]]; then
    echo "Error: pinned patch helper lacks its module registry." >&2
    return 1
  fi
  if [[ ! -d "${helper_root}/lib/modules" || -L "${helper_root}/lib/modules" ]]; then
    echo "Error: pinned patch helper has no safe module directory." >&2
    return 1
  fi
  if [[ ! -f "${registry_file}" || -L "${registry_file}" ]]; then
    echo "Error: pinned patch helper lacks its legacy module registry." >&2
    return 1
  fi
  if [[ -L "${module_source}" ]]; then
    echo "Error: pinned patch helper has an unsafe boot-animation module path." >&2
    return 1
  fi

  cp -- src/boot_animation.py "${module_source}" || return 1
  if ! grep -Fq 'def all_modules' "${init_file}" ||
    ! grep -Fq 'legacy_cli_module_types' "${init_file}" ||
    ! grep -Fq 'def legacy_cli_module_types' "${registry_file}" ||
    ! grep -Fq 'result: list[type[LegacyCliModule]] = []' "${registry_file}" ||
    ! grep -Fq '    return tuple(result)' "${registry_file}"; then
    echo "Error: unsupported pinned helper module registry API." >&2
    return 1
  fi

  if ! grep -Fq 'from lib.modules.boot_animation import BootAnimationMod' "${registry_file}"; then
    awk '/^    result: list\[type\[LegacyCliModule\]\] = \[\]$/ {
      print
      print "    from lib.modules.boot_animation import BootAnimationMod"
      next
    }
    {print}' "${registry_file}" >"${registry_file}.tmp" || return 1
    mv -- "${registry_file}.tmp" "${registry_file}" || return 1
  fi
  if ! grep -Fq '    result.append(BootAnimationMod)' "${registry_file}"; then
    awk '/^    return tuple\(result\)$/ {
      print "    result.append(BootAnimationMod)"
      print
      next
    }
    {print}' "${registry_file}" >"${registry_file}.tmp" || return 1
    mv -- "${registry_file}.tmp" "${registry_file}" || return 1
  fi

  mkdir -p -- "${WORKDIR}/modules" "${WORKDIR}/signatures" || return 1
  : >"${WORKDIR}/modules/boot-animation.zip"
  : >"${WORKDIR}/signatures/boot-animation.zip.sig"
  export PIXENEOS_BOOT_ANIMATION_PATH="${payload_path}"
  export PIXENEOS_BOOT_ANIMATION_DARK_PATH="${dark_payload_path}"
}

# Resolve and acquire the locked F-Droid inputs before exposing them to the
# patch command. Artifact URLs and versions belong exclusively to the lock.
function prepare_fdroid_privileged_extension() {
  local args_name="${1}"
  local helper_root="${2}"
  local -n args_ref="${args_name}"
  local lock_path="${FDROID_PRIVILEGED_EXTENSION_LOCK}"
  local profile_path="${FDROID_PRIVILEGED_EXTENSION_PROFILE}"
  local cache_path="${FDROID_PRIVILEGED_EXTENSION_CACHE:-${WORKDIR}/locked-artifacts}"
  local report_path="${FDROID_PRIVILEGED_EXTENSION_PATCH_REPORT:-${OUTPUTS[PATCHED_OTA]}.patch-report.json}"
  local module_tool="${helper_root}/module-tool.py"

  if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" != 'true' ]]; then
    return 0
  fi

  if ! verify_fdroid_privileged_extension_inputs \
    "${lock_path}" "${profile_path}"; then
    return 1
  fi
  if [[ ! -f "${module_tool}" || -L "${module_tool}" ]]; then
    echo "Error: the pinned patch helper lacks the locked module tool." >&2
    return 1
  fi

  if ! python "${module_tool}" resolve \
    --profile "${profile_path}" \
    --lock "${lock_path}" \
    --format json >/dev/null; then
    echo "Error: F-Droid locked profile resolution failed." >&2
    return 1
  fi
  if ! python "${module_tool}" artifacts fetch \
    --lock "${lock_path}" \
    --cache "${cache_path}" \
    --module fdroid-privileged-extension >/dev/null; then
    echo "Error: F-Droid locked artifact fetch failed." >&2
    return 1
  fi
  if ! python "${module_tool}" artifacts verify \
    --lock "${lock_path}" \
    --cache "${cache_path}" \
    --module fdroid-privileged-extension >/dev/null; then
    echo "Error: F-Droid locked artifact verification failed." >&2
    return 1
  fi

  args_ref+=(
    "--module-lock" "${lock_path}"
    "--module-profile" "${profile_path}"
    "--module-cache" "${cache_path}"
    "--patch-report" "${report_path}"
  )
}

function write_microg_resolution_profile() {
  local profile_path="${1}"
  local root_mode='rootless'

  resolve_root_mode >/dev/null || return 1
  [[ "${RESOLVED_ROOT_MODE}" != 'magisk' ]] || root_mode='rooted'

  mkdir -p -- "$(dirname -- "${profile_path}")" || return 1
  cat >"${profile_path}" <<EOF
schema_version = 1
id = 'lineageos-microg-${root_mode}'
rom_family = 'lineageos'
root_mode = '${root_mode}'
abi = 'arm64-v8a'
api_level = 36
output_scope = '${OUTPUT_SCOPE}'
enabled_modules = ['microg']
acknowledgements = []
experimental_acknowledgements = []

[capabilities]
root_providers = []
zygisk_providers = []
selective_signature_spoofing = true
product_priv_app = true
custom_init_selinux = false
EOF
}

function prepare_microg() {
  local args_name="${1}"
  local helper_root="${2}"
  local -n args_ref="${args_name}"
  local lock_path="${helper_root}/locks/microg-v0.3.15.250932.json"
  local cache_path="${WORKDIR}/locked-artifacts"
  local profile_path="${WORKDIR}/locked-profiles/microg.toml"
  local report_path="${OUTPUTS[PATCHED_OTA]}.microg-patch-report.json"
  local module_tool="${helper_root}/module-tool.py"

  if [[ "${ADDITIONALS[MICROG]}" != 'true' ]]; then
    return 0
  fi
  if [[ "${ROM_FAMILY}" != 'lineageos' ]]; then
    echo "Error: microG is supported only for LineageOS." >&2
    return 1
  fi
  if [[ ! -f "${lock_path}" || -L "${lock_path}" ||
    ! -f "${module_tool}" || -L "${module_tool}" ]]; then
    echo "Error: pinned helper lacks the reviewed microG lock/module tool." >&2
    return 1
  fi
  write_microg_resolution_profile "${profile_path}" || return 1
  python "${module_tool}" artifacts fetch \
    --lock "${lock_path}" \
    --cache "${cache_path}" \
    --module microg >/dev/null || return 1

  args_ref+=(
    "--module-lock" "${lock_path}"
    "--module-profile" "${profile_path}"
    "--module-cache" "${cache_path}"
    "--patch-report" "${report_path}"
  )
}

# Function to create and make the release called by main script
function create_and_make_release() {
  if [[ ! -d $WORKDIR ]]; then
    echo -e "Error: $WORKDIR is non-existent. Downloading the tools..."

    # Check for requirements and download them accordingly
    check_and_download_dependencies
  fi

  # Reject a stale or unexpected helper checkout before downloading a large OTA.
  helper_repository_preflight || return 1

  # Calls the download_ota function to download the OTA if not found
  download_ota || return 1
  # Calls the create_ota function to create the OTA
  create_ota
}

function create_ota() {
  [[ "${CLEANUP}" != 'true' ]] && trap cleanup EXIT ERR

  # Generate output file names
  generate_ota_info || return 1
  # Setup environment variables, apply the pinned compatibility transform, and
  # install the helper's Python dependencies.
  env_setup || return 1
  # Smoke-test the transformed helper before touching the OTA.
  helper_contract_preflight || return 1
  # Patch OTA with avbroot and afsr by leveraging my-avbroot-setup
  patch_ota
}

# Function to cleanup the temporary files and unset the keys when not in interactive mode
function cleanup() {
  if [[ "${CLEANUP}" != 'true' ]]; then
    echo -e "Cleanup is disabled. Exiting...\n"
    return
  fi

  echo "Cleaning up..."
  rm -rf "${WORKDIR}"
  unset "${KEYS[@]}"
  echo "Cleanup complete."
}

# Generate the AVB and OTA signing keys.
# Has to be called manually.
function generate_keys() {
  # Keep locally generated signing material in the ignored .keys directory unless
  # the caller explicitly set custom KEYS paths before sourcing this file.
  if [[ "${KEYS[AVB]}" == "avb.key" ]]; then
    KEYS[AVB]=".keys/avb.key"
  fi
  if [[ "${KEYS[OTA]}" == "ota.key" ]]; then
    KEYS[OTA]=".keys/ota.key"
  fi
  if [[ "${KEYS[CERT_OTA]}" == "ota.crt" ]]; then
    KEYS[CERT_OTA]=".keys/ota.crt"
  fi
  if [[ "${KEYS[PKMD]}" == "avb_pkmd.bin" ]]; then
    KEYS[PKMD]=".keys/avb_pkmd.bin"
  fi

  mkdir -p \
    "$(dirname "${KEYS[AVB]}")" \
    "$(dirname "${KEYS[OTA]}")" \
    "$(dirname "${KEYS[CERT_OTA]}")" \
    "$(dirname "${KEYS[PKMD]}")"

  # Generate the AVB and OTA signing keys
  run_executable_tool avbroot key generate-key -o "${KEYS[AVB]}" || return 1
  run_executable_tool avbroot key generate-key -o "${KEYS[OTA]}" || return 1

  # Convert the public key portion of the AVB signing key to the AVB public key metadata format
  # This is the format that the bootloader requires when setting the custom root of trust
  run_executable_tool avbroot key extract-avb \
    -k "${KEYS[AVB]}" -o "${KEYS[PKMD]}" || return 1

  # Generate a self-signed certificate for the OTA signing key
  # This is used by recovery to verify OTA updates when sideloading
  run_executable_tool avbroot key generate-cert \
    -k "${KEYS[OTA]}" -o "${KEYS[CERT_OTA]}" || return 1

  # Convert the keys to base64 which can be used in CI/CD pipeline environment
  base64_encode
}

# Function to patch the OTA with the AVB and OTA keys
# Leverages `my-avbroot-setup` to patch the OTA
# This function does a lot of things before patching the OTA
function patch_ota() {
  resolve_root_mode || return 1

  if [[ -z "${ROM_PROFILE[PROVIDER]:-}" ]]; then
    resolve_rom_profile || return 1
  fi

  if [[ "${INTERACTIVE_MODE}" != 'true' ]]; then
    base64_decode
  fi

  # Set the paths
  local ota_zip="${WORKDIR}/${GRAPHENEOS[OTA_TARGET]}"
  local pkmd="${KEYS[PKMD]}"
  local grapheneos_pkmd="${WORKDIR}/extracted/avb_pkmd.bin"
  local grapheneos_otacert="${WORKDIR}/extracted/ota/META-INF/com/android/otacert"
  local magisk_path="${WORKDIR}/modules/magisk.apk"
  local my_avbroot_setup="${WORKDIR}/tools/my-avbroot-setup"
  local -a locked_module_args=()

  # Activate the virtual environment
  if [ -z "${VIRTUAL_ENV:-}" ]; then
    enable_venv || return 1
  fi

  if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" == 'true' &&
    "${ADDITIONALS[MICROG]}" == 'true' ]]; then
    echo "Error: multiple locked module bundles are not supported in one build." >&2
    return 1
  fi

  # Locked module artifacts must be resolved, fetched, and verified before any
  # OTA contents are unpacked. Keep the disabled path on its legacy ordering.
  if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" == 'true' ]]; then
    rm -rf -- "${WORKDIR}/extracted/extracts/"
    if ! prepare_fdroid_privileged_extension \
      locked_module_args "${my_avbroot_setup}"; then
      return 1
    fi
  fi

  # Extract the official public keys and certificates if not found
  if [[ ! -e "${grapheneos_pkmd}" || ! -e "${grapheneos_otacert}" ]]; then
    echo "Extracting official keys..."
    extract_official_keys
  fi

  # Legacy output markers do not encode a locked module selection. Never reuse
  # one for an enabled F-Droid build. A dual build is reusable only when both
  # OTA triplets and both per-flavor update-info files are already complete.
  local outputs_ready=false
  if [[ "${RESOLVED_ROOT_MODE}" == 'both' ]]; then
    if [[ -f "${OUTPUTS[PATCHED_OTA_ROOTLESS]}" &&
      -f "${OUTPUTS[PATCHED_OTA_ROOTLESS]}.csig" &&
      -f "${OUTPUTS[PATCHED_OTA_MAGISK]}" &&
      -f "${OUTPUTS[PATCHED_OTA_MAGISK]}.csig" &&
      -f "${OUTPUTS[OTA_METADATA_ROOTLESS]}" &&
      -f "${OUTPUTS[OTA_METADATA_MAGISK]}" ]]; then
      outputs_ready=true
    fi
  elif [[ -f "${OUTPUTS[PATCHED_OTA]}" ]]; then
    outputs_ready=true
  fi

  if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" != 'true' &&
    "${outputs_ready}" == true ]]; then
    echo -e "Requested OTA output already exists locally. Patch skipped."
  else
    echo -e "Patching OTA..."
    local args=()

    # OTA input and output
    args+=("--input" "${ota_zip}.zip")
    args+=("--output" "${OUTPUTS[PATCHED_OTA]}")

    # GrapheneOS public key metadata and certificate
    args+=("--verify-public-key-avb" "${grapheneos_pkmd}")
    args+=("--verify-cert-ota" "${grapheneos_otacert}")

    # Never enable the exception just because this is LineageOS/pdx235.
    # The October 9 source's independent publisher/fingerprint provenance is
    # unresolved; the checked-in pin is intentionally empty (strict default).
    # If a source is ever independently reviewed and pinned, also require the
    # exact source bytes, release filename, device and avbroot version.
    if [[ -n "${_SOURCE_CARE_MAP_PDX235_20261009_SHA256}" &&
      "${ROM_FAMILY}" == 'lineageos' && "${DEVICE_NAME:-}" == 'pdx235' ]]; then
      local active_avbroot_version
      if active_avbroot_version="$(run_executable_tool avbroot --version)" &&
        source_care_map_exception_allowed \
          "${ROM_FAMILY}" "${DEVICE_NAME}" "${GRAPHENEOS[OTA_TARGET]}" \
          "${ota_zip}.zip" "${active_avbroot_version}"; then
        args+=("--allow-source-care-map-mismatch")
      fi
    fi

    # PixeneOS decoded keys and certificates
    args+=("--sign-key-avb" "${KEYS[AVB]}")
    args+=("--sign-key-ota" "${KEYS[OTA]}")
    args+=("--sign-cert-ota" "${KEYS[CERT_OTA]}")

    # Passphrases for AVB and OTA keys
    args+=("--pass-avb-env-var" "PASSPHRASE_AVB")
    args+=("--pass-ota-env-var" "PASSPHRASE_OTA")

    # Preserve the legacy cleanup ordering when locked modules are disabled.
    # Enabled builds already cleared this tree before locked acquisition so a
    # caller-selected cache below it remains available to patch.py.
    if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" != 'true' ]]; then
      rm -rf -- "${WORKDIR}/extracted/extracts/"
    fi

    # Modules and their signatures
    if [[ "${ADDITIONALS[BOOT_ANIMATION]}" == 'true' ]] &&
      ! prepare_boot_animation_module "${my_avbroot_setup}"; then
      return 1
    fi
    append_enabled_module_arguments args
    if [[ "${ADDITIONALS[FDROID_PRIVILEGED_EXTENSION]}" == 'true' ]]; then
      args+=("${locked_module_args[@]}")
    elif [[ "${ADDITIONALS[MICROG]}" == 'true' ]]; then
      prepare_microg args "${my_avbroot_setup}" || return 1
    elif ! prepare_fdroid_privileged_extension args "${my_avbroot_setup}"; then
      return 1
    fi

    if [[ "${ROM_PROFILE[CLEAR_VBMETA_FLAGS]}" == 'true' ]]; then
      args+=("--patch-arg=--clear-vbmeta-flags")
    fi

    # Add debug module if unauthorized ADB is enabled
    if [[ "${ADDITIONALS[DEBUG]}" == 'true' ]]; then
        echo -e "Unauthorized ADB is enabled. Setting up debug module...\n"
        setup_debug_module
        args+=("--module-debug" "${WORKDIR}/modules/dummy.zip")
        args+=("--module-debug-sig" "${WORKDIR}/modules/dummy.zip.sig")
    else
        echo -e "Unauthorized ADB is not enabled. Skipping debug module setup...\n"
    fi

    echo -e "MAS_COMPATIBLE_SEPOLICY value: ${ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]}" #debug Placed above the patch arguments
    if [[ "${ADDITIONALS[MAS_COMPATIBLE_SEPOLICY]}" == 'true' ]]; then
      echo -e "Compatible SEPolicy Flag is enabled.  Adding patch argument to setup script...\n"
      args+=("--compatible-sepolicy")
    else
      echo -e "Compatible SEPolicy Flag is NOT enabled. Continuing...\n"
    fi

    # Root selection is the only part of the helper patch plan that differs
    # between the two outputs. ROOT_MODE=both keeps rootless as the primary
    # output and asks the helper for a Magisk secondary output from the exact
    # same prepared replacement images.
    case "${RESOLVED_ROOT_MODE}" in
      magisk)
        echo -e "Magisk is enabled. Modifying the setup script...\n"
        args+=("--patch-arg=--magisk" "--patch-arg" "${magisk_path}")
        args+=("--patch-arg=--magisk-preinit-device" "--patch-arg" "${MAGISK[PREINIT]}")
        ;;
      rootless)
        args+=("--patch-arg=--rootless")
        echo -e "Magisk is not enabled. Continuing rootless...\n"
        ;;
      both)
        args+=("--patch-arg=--rootless")
        args+=("--skip-custota-tool")
        args+=("--secondary-output" "${OUTPUTS[PATCHED_OTA_MAGISK]}")
        args+=("--secondary-patch-arg=--magisk")
        args+=("--secondary-patch-arg" "${magisk_path}")
        args+=("--secondary-patch-arg=--magisk-preinit-device")
        args+=("--secondary-patch-arg" "${MAGISK[PREINIT]}")
        # LineageOS vbmeta commonly ships with verification-disabled flags.
        # The rootless plan already clears them; the Magisk secondary plan
        # must receive the same profile-wide requirement independently.
        if [[ "${ROM_PROFILE[CLEAR_VBMETA_FLAGS]}" == 'true' ]]; then
          args+=("--secondary-patch-arg=--clear-vbmeta-flags")
        fi
        ;;
    esac

    # Python command to run the patch script
    python "${my_avbroot_setup}/patch.py" "${args[@]}" || return 1

    # A Magisk label is publication metadata, not proof of a working runtime
    # root environment. Static CI can verify the Magisk boot patch and paired
    # output separation, but /data/adb/magisk is provisioned on-device by
    # Magisk's additional-setup/environment-fix flow.
    verify_requested_root_outputs || return 1
    verify_requested_boot_animation_outputs || return 1

    if [[ "${RESOLVED_ROOT_MODE}" == 'both' ]]; then
      generate_custota_variant_sidecars         "${OUTPUTS[PATCHED_OTA_ROOTLESS]}"         "${OUTPUTS[OTA_METADATA_ROOTLESS]}" || return 1
      generate_custota_variant_sidecars         "${OUTPUTS[PATCHED_OTA_MAGISK]}"         "${OUTPUTS[OTA_METADATA_MAGISK]}" || return 1
    fi
  fi

  # Deactivate the virtual environment after patching the OTA
  deactivate
}

function extract_ota_boot_target() {
  local ota_path="${1}"
  local directory="${2}"
  local partitions target image_path

  [[ -f "${ota_path}" ]] || {
    echo "Error: missing OTA for boot-target inspection: ${ota_path}" >&2
    return 1
  }

  partitions="$(run_executable_tool avbroot ota list --input "${ota_path}")" ||
    return 1
  if grep -Fxq -- 'init_boot' <<<"${partitions}"; then
    target='init_boot'
  elif grep -Fxq -- 'boot' <<<"${partitions}"; then
    target='boot'
  else
    echo "Error: OTA has no boot or init_boot partition: ${ota_path}" >&2
    return 1
  fi

  mkdir -p -- "${directory}" || return 1
  if ! run_executable_tool avbroot ota extract \
    --input "${ota_path}" \
    --directory "${directory}" \
    --partition "${target}" >/dev/null; then
    return 1
  fi

  image_path="${directory}/${target}.img"
  [[ -s "${image_path}" ]] || {
    echo "Error: boot-target inspection did not extract ${target}.img." >&2
    return 1
  }

  printf '%s\n' "${target}"
}

function verify_boot_animation_ota() {
  local ota_path="${1}"
  local temp_dir payload_path dark_payload_path avbroot_bin afsr_bin ota_abs
  local extract_dir unpack_dir image_path raw_image

  [[ -f "${ota_path}" ]] || {
    echo "Error: missing OTA for boot-animation inspection: ${ota_path}" >&2
    return 1
  }

  _resolve_boot_animation_payloads || return 1
  payload_path="${BOOT_ANIMATION_LIGHT_PAYLOAD}"
  dark_payload_path="${BOOT_ANIMATION_DARK_PAYLOAD}"
  temp_dir="$(mktemp -d "${WORKDIR}/boot-animation-verify.XXXXXX")" || return 1
  temp_dir="$(realpath -- "${temp_dir}")" || return 1
  ota_abs="$(realpath -- "${ota_path}")" || {
    rm -rf -- "${temp_dir}"
    return 1
  }
  avbroot_bin="$(resolve_executable_tool avbroot)" || {
    rm -rf -- "${temp_dir}"
    return 1
  }
  afsr_bin="$(resolve_executable_tool afsr)" || {
    rm -rf -- "${temp_dir}"
    return 1
  }
  avbroot_bin="$(realpath -- "${avbroot_bin}")" || {
    rm -rf -- "${temp_dir}"
    return 1
  }
  afsr_bin="$(realpath -- "${afsr_bin}")" || {
    rm -rf -- "${temp_dir}"
    return 1
  }

  extract_dir="${temp_dir}/extract-product"
  unpack_dir="${temp_dir}/unpack-product"
  mkdir -p -- "${extract_dir}" "${unpack_dir}" || {
    rm -rf -- "${temp_dir}"
    return 1
  }

  if ! "${avbroot_bin}" ota extract \
    --input "${ota_abs}" \
    --directory "${extract_dir}" \
    --partition product >/dev/null; then
    rm -rf -- "${temp_dir}"
    return 1
  fi

  image_path="${extract_dir}/product.img"
  [[ -s "${image_path}" ]] || {
    echo "Error: boot-animation verification did not extract product.img." >&2
    rm -rf -- "${temp_dir}"
    return 1
  }
  image_path="$(realpath -- "${image_path}")" || {
    rm -rf -- "${temp_dir}"
    return 1
  }

  if ! (
    cd -- "${unpack_dir}" &&
      "${avbroot_bin}" avb unpack --quiet --input "${image_path}" &&
      raw_image="$(realpath -- raw.img)" &&
      "${afsr_bin}" unpack --input "${raw_image}"
  ); then
    rm -rf -- "${temp_dir}"
    return 1
  fi

  if ! python3 src/boot_animation.py verify-runtime \
    "${payload_path}" \
    "${dark_payload_path}" \
    "${unpack_dir}/fs_tree/media/bootanimation.zip" \
    "${unpack_dir}/fs_tree/media/bootanimation-dark.zip"; then
    rm -rf -- "${temp_dir}"
    return 1
  fi

  rm -rf -- "${temp_dir}"
  echo "Verified custom boot animation in finished OTA product image: ${ota_path}"
}

function verify_requested_boot_animation_outputs() {
  local -a ota_paths=()
  local ota_path

  [[ "${ADDITIONALS[BOOT_ANIMATION]}" == 'true' ]] || return 0

  case "${RESOLVED_ROOT_MODE}" in
    rootless|magisk)
      ota_paths=("${OUTPUTS[PATCHED_OTA]}")
      ;;
    both)
      ota_paths=(
        "${OUTPUTS[PATCHED_OTA_ROOTLESS]}"
        "${OUTPUTS[PATCHED_OTA_MAGISK]}"
      )
      ;;
    *)
      echo "Error: cannot verify boot animation for root mode: ${RESOLVED_ROOT_MODE}" >&2
      return 1
      ;;
  esac

  for ota_path in "${ota_paths[@]}"; do
    if ! verify_boot_animation_ota "${ota_path}"; then
      rm -f -- "${ota_path}" "${ota_path}.csig"
      echo "Error: refusing to keep or publish an OTA without verified custom boot animation." >&2
      return 1
    fi
  done
}


function verify_magisk_ota() {
  local ota_path="${1}"
  local expected_preinit="${2}"
  local temp_dir target image_path magisk_info

  temp_dir="$(mktemp -d "${WORKDIR}/magisk-verify.XXXXXX")" || return 1
  target="$(extract_ota_boot_target "${ota_path}" "${temp_dir}")" || {
    rm -rf -- "${temp_dir}"
    return 1
  }
  image_path="${temp_dir}/${target}.img"

  if ! magisk_info="$(run_executable_tool avbroot boot magisk-info \
    --image "${image_path}" 2>&1)"; then
    echo "Error: OTA labeled as Magisk has no detectable Magisk boot patch." >&2
    rm -rf -- "${temp_dir}"
    return 1
  fi
  rm -rf -- "${temp_dir}"

  if ! grep -Fxq -- "PREINITDEVICE=${expected_preinit}" <<<"${magisk_info}"; then
    echo "Error: Magisk OTA does not contain the expected PREINITDEVICE=${expected_preinit}." >&2
    return 1
  fi

  echo "Verified Magisk boot-patch evidence in ${ota_path} (${target}, PREINITDEVICE=${expected_preinit})."
}

function verify_paired_root_outputs() {
  local rootless_ota="${1}"
  local magisk_ota="${2}"
  local temp_dir rootless_dir magisk_dir
  local rootless_target magisk_target rootless_image magisk_image
  local rootless_digest magisk_digest

  temp_dir="$(mktemp -d "${WORKDIR}/root-pair-verify.XXXXXX")" || return 1
  rootless_dir="${temp_dir}/rootless"
  magisk_dir="${temp_dir}/magisk"

  rootless_target="$(extract_ota_boot_target "${rootless_ota}" "${rootless_dir}")" || {
    rm -rf -- "${temp_dir}"
    return 1
  }
  magisk_target="$(extract_ota_boot_target "${magisk_ota}" "${magisk_dir}")" || {
    rm -rf -- "${temp_dir}"
    return 1
  }

  if [[ "${rootless_target}" != "${magisk_target}" ]]; then
    echo "Error: paired outputs selected different Magisk boot targets: rootless=${rootless_target}, magisk=${magisk_target}." >&2
    rm -rf -- "${temp_dir}"
    return 1
  fi

  rootless_image="${rootless_dir}/${rootless_target}.img"
  magisk_image="${magisk_dir}/${magisk_target}.img"
  rootless_digest="$(sha256sum -- "${rootless_image}" | awk '{print $1}')"
  magisk_digest="$(sha256sum -- "${magisk_image}" | awk '{print $1}')"

  if [[ "${rootless_digest}" == "${magisk_digest}" ]]; then
    echo "Error: paired rootless and Magisk outputs have identical ${magisk_target} images." >&2
    rm -rf -- "${temp_dir}"
    return 1
  fi

  if run_executable_tool avbroot boot magisk-info \
    --image "${rootless_image}" >/dev/null 2>&1; then
    echo "Error: paired rootless output unexpectedly contains Magisk boot evidence." >&2
    rm -rf -- "${temp_dir}"
    return 1
  fi

  rm -rf -- "${temp_dir}"
  echo "Verified paired boot targets differ and the rootless ${rootless_target} has no Magisk evidence."
}

function verify_requested_root_outputs() {
  local magisk_ota

  case "${RESOLVED_ROOT_MODE}" in
    rootless)
      return 0
      ;;
    magisk)
      magisk_ota="${OUTPUTS[PATCHED_OTA]}"
      ;;
    both)
      magisk_ota="${OUTPUTS[PATCHED_OTA_MAGISK]}"
      ;;
    *)
      echo "Error: cannot verify unknown resolved root mode: ${RESOLVED_ROOT_MODE}" >&2
      return 1
      ;;
  esac

  if ! verify_magisk_ota "${magisk_ota}" "${MAGISK[PREINIT]}"; then
    rm -f -- "${magisk_ota}" "${magisk_ota}.csig"
    echo "Error: refusing to keep or publish an OTA without verified Magisk boot-patch evidence." >&2
    return 1
  fi

  if [[ "${RESOLVED_ROOT_MODE}" == both ]] &&
    ! verify_paired_root_outputs "${OUTPUTS[PATCHED_OTA_ROOTLESS]}" "${magisk_ota}"; then
    rm -f -- "${magisk_ota}" "${magisk_ota}.csig"
    echo "Error: refusing to keep or publish an invalid rootless/Magisk output pair." >&2
    return 1
  fi
}

function release_location_for_output() {
  local artifact_name="${1}"

  resolve_release_repository
  if [[ -n "${PIXENEOS_RELEASE_BASE_URL}" ]]; then
    printf '%s/%s' "${PIXENEOS_RELEASE_BASE_URL%/}" "${artifact_name}"
  else
    printf '%s/%s/%s/releases/download/%s/%s'       "${DOMAIN}"       "${PIXENEOS_RELEASE_OWNER}"       "${PIXENEOS_RELEASE_REPOSITORY}"       "${VERSION[GRAPHENEOS]}"       "${artifact_name}"
  fi
}

function generate_custota_variant_sidecars() {
  local ota_path="${1}"
  local metadata_path="${2}"
  local location

  [[ -f "${ota_path}" ]] || {
    echo "Error: missing OTA for Custota sidecars: ${ota_path}" >&2
    return 1
  }
  location="$(release_location_for_output "${ota_path}")" || return 1

  run_executable_tool custota-tool gen-csig     --input "${ota_path}"     --key "${KEYS[OTA]}"     --cert "${KEYS[CERT_OTA]}"     --passphrase-env-var PASSPHRASE_OTA || return 1

  run_executable_tool custota-tool gen-update-info     --file "${metadata_path}"     --location "${location}"
}

function resolve_release_repository() {
  local github_repository="${GITHUB_REPOSITORY:-}"

  if [[ -z "${PIXENEOS_RELEASE_OWNER}" && "${github_repository}" == */* ]]; then
    PIXENEOS_RELEASE_OWNER="${github_repository%%/*}"
  fi

  if [[ -z "${PIXENEOS_RELEASE_REPOSITORY}" && "${github_repository}" == */* ]]; then
    PIXENEOS_RELEASE_REPOSITORY="${github_repository#*/}"
  fi

  PIXENEOS_RELEASE_OWNER="${PIXENEOS_RELEASE_OWNER:-0cwa}"
  PIXENEOS_RELEASE_REPOSITORY="${PIXENEOS_RELEASE_REPOSITORY:-PixeneOS}"
}

# Function to setup the environment for the my-avbroot-setup script
function my_avbroot_setup() {
  resolve_release_repository

  local helper_root="${WORKDIR}/tools/my-avbroot-setup"
  local compatibility_helper="tools/compat/avbroot_setup_compat.py"
  local helper_source="${PIXENEOS_AVBROOT_SETUP_SOURCE:-${DOMAIN}/0cwa/my-avbroot-setup}"
  local location_path

  if [[ -n "${PIXENEOS_RELEASE_BASE_URL}" ]]; then
    location_path="${PIXENEOS_RELEASE_BASE_URL%/}/${OUTPUTS[PATCHED_OTA]}"
  else
    location_path="${DOMAIN}/${PIXENEOS_RELEASE_OWNER}/${PIXENEOS_RELEASE_REPOSITORY}/releases/download/${VERSION[GRAPHENEOS]}/${OUTPUTS[PATCHED_OTA]}"
  fi

  echo -e "Running script modifications..."
  python3 "${compatibility_helper}" \
    --source "${helper_source}" \
    "${helper_root}" \
    "${location_path}" \
    "${VERSION[AVBROOT_SETUP]}"
}

# Fail early when the helper checkout is not the exact revision PixeneOS pins.
# The compatibility transformer performs the stronger origin/status/source-shape
# validation later; this cheap check intentionally runs before OTA acquisition.
function helper_repository_preflight() {
  local helper_root="${WORKDIR}/tools/my-avbroot-setup"
  local actual

  actual="$(git -C "${helper_root}" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" || {
    echo "Error: helper repository is missing or has no commit: ${helper_root}" >&2
    return 1
  }

  if [[ "${actual}" != "${VERSION[AVBROOT_SETUP]}" ]]; then
    echo "Error: helper contract mismatch: expected ${VERSION[AVBROOT_SETUP]}, got ${actual}" >&2
    return 1
  fi
}

# Run after env_setup: by this point the fail-closed compatibility transform and
# pyproject dependencies are in place, so --help exercises the effective helper.
function helper_contract_preflight() {
  local helper_root="${WORKDIR}/tools/my-avbroot-setup"

  if ! python "${helper_root}/patch.py" --help >/dev/null 2>&1; then
    echo "Error: helper patch.py contract smoke check failed" >&2
    return 1
  fi
}

# Function to setup the environment variables and paths for patching the OTA
function env_setup() {
  local my_avbroot_setup="${WORKDIR}/tools/my-avbroot-setup"
  local pyproject_file="${my_avbroot_setup}/pyproject.toml"
  local tool flag executable variable path_prefix
  local -a selected_tools=()
  local -a resolved_executables=()
  local -a executable_directories=()

  # Restore the caller PATH from the last successful setup before resolving a
  # new selection. Only the exact prefix injected by this function is removed.
  unset PIXENEOS_AVBROOT_BIN PIXENEOS_AFSR_BIN PIXENEOS_CUSTOTA_TOOL_BIN
  if [[ -n "${PIXENEOS_EXECUTABLE_PATH_PREFIX:-}" ]]; then
    if [[ "${PATH}" == "${PIXENEOS_EXECUTABLE_PATH_PREFIX}" ]]; then
      PATH=""
    elif [[ "${PATH}" == "${PIXENEOS_EXECUTABLE_PATH_PREFIX}:"* ]]; then
      PATH="${PATH#"${PIXENEOS_EXECUTABLE_PATH_PREFIX}:"}"
    elif [[ -n "${PIXENEOS_EXECUTABLE_BASE_PATH+x}" ]]; then
      PATH="${PIXENEOS_EXECUTABLE_BASE_PATH}"
      export PATH
      unset PIXENEOS_EXECUTABLE_PATH_PREFIX PIXENEOS_EXECUTABLE_BASE_PATH
      echo "Error: executable PATH prefix changed after setup." >&2
      return 1
    else
      unset PIXENEOS_EXECUTABLE_PATH_PREFIX
      echo "Error: executable PATH tracking is incomplete." >&2
      return 1
    fi
    export PATH
  fi
  unset PIXENEOS_EXECUTABLE_PATH_PREFIX PIXENEOS_EXECUTABLE_BASE_PATH

  # Resolve the complete enabled set before modifying helper source, activating
  # an environment, or exposing any executable binding.
  for tool in "${LOCKED_EXECUTABLE_TOOLS[@]}"; do
    flag="$(flag_check "${tool}")"
    if [[ "${flag}" != "true" ]]; then
      continue
    fi
    executable="$(resolve_executable_tool "${tool}")" || return 1
    selected_tools+=("${tool}")
    resolved_executables+=("${executable}")
  done

  # Set up `my-avbroot-setup` only after every enabled executable resolved.
  my_avbroot_setup || return 1

  # Enabled python virtual environment
  enable_venv || return 1

  # Install required Python packages from the maintained helper's pyproject.
  if [[ -f "${pyproject_file}" ]]; then
    if ! command -v uv &>/dev/null; then
      echo -e "uv not found. Installing..."
      python3 -m pip install uv || return 1
    fi

    echo -e "Installing required Python packages from pyproject.toml..."
    uv pip install -r "${pyproject_file}" || return 1
  else
    echo -e "Warning: pyproject.toml not found at ${my_avbroot_setup}"
  fi

  local index
  for index in "${!selected_tools[@]}"; do
    tool="${selected_tools[${index}]}"
    executable="${resolved_executables[${index}]}"
    case "${tool}" in
      avbroot) variable="PIXENEOS_AVBROOT_BIN" ;;
      afsr) variable="PIXENEOS_AFSR_BIN" ;;
      custota-tool) variable="PIXENEOS_CUSTOTA_TOOL_BIN" ;;
    esac
    printf -v "${variable}" '%s' "${executable}"
    export "${variable}"
    executable_directories+=("$(dirname -- "${executable}")")
  done

  # The pinned helper currently resolves these names through PATH. Track the
  # exact injected prefix so a later setup can restore the caller's base PATH.
  if ((${#executable_directories[@]})); then
    path_prefix="$(IFS=:; echo "${executable_directories[*]}")"
    PIXENEOS_EXECUTABLE_BASE_PATH="${PATH}"
    PIXENEOS_EXECUTABLE_PATH_PREFIX="${path_prefix}"
    export PATH="${path_prefix}:${PATH}"
  fi
}

# Function to enable the python virtual environment
function enable_venv() {
  local dir_path='' # Default value is empty string
  local base_path=$(basename "$(pwd)")
  local venv_path=''

  # Check presence of venv
  # Create a virtual environment if not found
  if [[ "${base_path}" == "my-avbroot-setup" ]]; then
    if [ ! -d "venv" ]; then
      echo -e "Virtual environment not found. Creating..."
      python3 -m venv venv
    fi
  else
    echo -e "The script is not run from the \`my-avbroot-setup\` directory.\nSearching for the directory..."
    dir_path=$(find . -type d -name "my-avbroot-setup" -print -quit)
    if [ ! -d "${dir_path}/venv" ]; then
      echo -e "Virtual environment not found in path \`${dir_path}\`. Creating..."
      python3 -m venv "${dir_path}/venv"
    fi
  fi

  # Set the virtual environment path
  if [ -n "${dir_path}" ]; then
    venv_path="${dir_path}/venv/bin/activate"
  else
    venv_path="venv/bin/activate"
  fi

  # Ensure venv_path is set correctly and activate the virtual environment
  if [[ ! -f "${venv_path}" ]]; then
    echo -e "Virtual environment activation script not found at \`${venv_path}\`."
    return 1
  fi
  source "${venv_path}" || return 1
  [[ -n "${VIRTUAL_ENV:-}" ]]
}

# Construct URL for the tools and download them
# This function is called by download_dependencies function when running in non-interactive mode
function url_constructor() {
  local repository="${1}"
  local user='chenxiaolong'
  local authority=''
  INTERACTIVE_MODE="${2:-true}"

  local repository_upper_case=$(echo "${repository}" | tr '[:lower:]' '[:upper:]')

  echo -e "Constructing URL for \`${repository}\` as \`${repository}\` is non-existent at \`${WORKDIR}\`..."
  # `my-avbroot-setup` is git repository
  if [[ "${repository}" == "my-avbroot-setup" ]]; then
    URL="${PIXENEOS_AVBROOT_SETUP_SOURCE:-${DOMAIN}/0cwa/${repository}}"
    SIGNATURE_URL=""
    case "${URL}" in
      git@*:* )
        [[ "${URL%%@*}" == 'git' ]] || {
          echo 'Error: authenticated helper repository URLs are not allowed.' >&2
          return 1
        }
        ;;
      *://*)
        authority="${URL#*://}"
        authority="${authority%%/*}"
        if [[ "${authority}" == *'@'* && "${URL}" != ssh://git@* ]]; then
          echo 'Error: authenticated helper repository URLs are not allowed.' >&2
          return 1
        fi
        ;;
    esac
  elif is_locked_executable_tool "${repository}"; then
    echo "Error: executable tools must be acquired from the immutable lock." >&2
    return 1
  else
    local suffix="release"

    local download_page="${DOMAIN}/${user}/${repository}/releases/download"
    local version="v${VERSION[${repository_upper_case}]}"
    local application="${repository}-${VERSION[${repository_upper_case}]}-${suffix}.zip"

    URL="${download_page}/${version}/${application}"
    SIGNATURE_URL="${download_page}/${version}/${application}.sig"
  fi

  if [[ "${repository}" == 'my-avbroot-setup' ]]; then
    echo -e "URL for \`${repository}\` configured."
  else
    echo -e "URL for \`${repository}\`: ${URL}"
  fi

  # If the script is running in interactive mode, prompt the user to overwrite the existing files
  if [[ "${INTERACTIVE_MODE}" == 'true' ]]; then
    if [[ -e "${WORKDIR}/tools/${repository}" || -e "${WORKDIR}/modules/${repository}.zip" || -e "${WORKDIR}/signatures/${repository}.zip.sig" ]]; then
      echo -n "Warning: \`${repository}\` already exists in \`${WORKDIR}\`\nOverwrite? (y/n) [default: yes]: "
      read -r confirm
      confirm=${confirm:-"yes"}
      if [[ $confirm =~ ^[yY](es|ES)?$ ]]; then
        echo "Removing existing files..."
        rm -rf "${WORKDIR}/tools/${repository}" "${WORKDIR}/modules/${repository}.zip" "${WORKDIR}/signatures/${repository}.zip.sig"
      else
        echo "Aborted."
        exit 1
      fi
    fi
  fi

  # Make the get call to download the tools and modules
  get "${repository}" "${URL}" "${SIGNATURE_URL}"
}

# Function to download the dependencies
# This calls the constructor that constructs the URL for the tools and modules
function download_dependencies() {
  local tool="${1}"
  INTERACTIVE_MODE='false'

  if type url_constructor &>/dev/null; then
    url_constructor "${tool}" "${INTERACTIVE_MODE}"
  else
    echo -e "Error: \`url_constructor\` function is not defined."
    exit 1
  fi
}

# Extract the OTA's embedded verification certificate and root vbmeta AVB
# public key. Use avbroot's structured extractor: the old "avb info | sed"
# pipeline also matched chained-partition public_key fields, concatenating
# multiple independent keys into one invalid AVB key file.
function extract_official_keys() {
  local ota_zip="${WORKDIR}/${GRAPHENEOS[OTA_TARGET]}.zip"
  local public_key="${WORKDIR}/extracted/avb_pkmd.bin"

  mkdir -p -- "${WORKDIR}/extracted" "${WORKDIR}/extracted/extracts" || return 1
  rm -f -- "${public_key}" || return 1

  # The pinned avbroot 3.34.1 extracts only the root vbmeta header's key;
  # chained descriptors are never included. Retain the existing --all image
  # extraction for compatibility with downstream consumers.
  run_executable_tool avbroot ota extract \
    --input "${ota_zip}" \
    --directory "${WORKDIR}/extracted/extracts" \
    --all \
    --public-key-avb "${public_key}" || return 1

  if [[ ! -s "${public_key}" ]]; then
    echo "Error: the source OTA has no root vbmeta AVB public key; refusing to patch without verification." >&2
    return 1
  fi

  # Preserve the existing OTA certificate extraction path. The helper uses
  # this certificate to validate the source OTA and recovery consistency.
  unzip "${ota_zip}" -d "${WORKDIR}/extracted/ota" || return 1
}

function dirty_suffix() {
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "-dirty"
  else
    echo ""
  fi
}

# Function to make directories
function make_directories() {
  mkdir -p \
    "${WORKDIR}" \
    "${WORKDIR}/.keys" \
    "${WORKDIR}/extracted/extracts" \
    "${WORKDIR}/extracted/ota" \
    "${WORKDIR}/modules" \
    "${WORKDIR}/signatures" \
    "${WORKDIR}/tools"
  chmod 0700 -- "${WORKDIR}" "${WORKDIR}/.keys" "${WORKDIR}/tools"
}

function _generate_ota_variant_info() {
  local variant="${1}"
  local original_root="${ADDITIONALS[ROOT]}"
  local flavor debug_suffix=''

  case "${variant}" in
    rootless)
      ADDITIONALS[ROOT]=false
      flavor='rootless'
      ;;
    magisk)
      ADDITIONALS[ROOT]=true
      flavor="magisk-${VERSION[MAGISK]}"
      ;;
    *)
      echo "Error: unsupported concrete root variant: ${variant}" >&2
      return 1
      ;;
  esac

  if [[ "${ADDITIONALS[DEBUG]}" == 'true' ]]; then
    debug_suffix='-debug-adb'
  fi

  if ! module_selection_fingerprint >/dev/null; then
    ADDITIONALS[ROOT]="${original_root}"
    return 1
  fi

  VARIANT_SELECTION_FINGERPRINT="${MODULE_SELECTION_FINGERPRINT}"
  VARIANT_PATCHED_OTA="${DEVICE_NAME}-${VERSION[GRAPHENEOS]}-${flavor}${debug_suffix}-${VARIANT_SELECTION_FINGERPRINT}-$(git rev-parse --short HEAD)$(dirty_suffix).zip"
  ADDITIONALS[ROOT]="${original_root}"
}

function generate_ota_info() {
  validate_device_name || return 1
  resolve_root_mode || return 1

  OUTPUTS[PATCHED_OTA_ROOTLESS]=''
  OUTPUTS[PATCHED_OTA_MAGISK]=''
  OUTPUTS[OTA_METADATA_ROOTLESS]=''
  OUTPUTS[OTA_METADATA_MAGISK]=''
  MODULE_SELECTION_FINGERPRINT_ROOTLESS=''
  MODULE_SELECTION_FINGERPRINT_MAGISK=''

  case "${RESOLVED_ROOT_MODE}" in
    rootless)
      _generate_ota_variant_info rootless || return 1
      OUTPUTS[PATCHED_OTA]="${VARIANT_PATCHED_OTA}"
      OUTPUTS[PATCHED_OTA_ROOTLESS]="${VARIANT_PATCHED_OTA}"
      MODULE_SELECTION_FINGERPRINT="${VARIANT_SELECTION_FINGERPRINT}"
      MODULE_SELECTION_FINGERPRINT_ROOTLESS="${VARIANT_SELECTION_FINGERPRINT}"
      ;;
    magisk)
      _generate_ota_variant_info magisk || return 1
      OUTPUTS[PATCHED_OTA]="${VARIANT_PATCHED_OTA}"
      OUTPUTS[PATCHED_OTA_MAGISK]="${VARIANT_PATCHED_OTA}"
      MODULE_SELECTION_FINGERPRINT="${VARIANT_SELECTION_FINGERPRINT}"
      MODULE_SELECTION_FINGERPRINT_MAGISK="${VARIANT_SELECTION_FINGERPRINT}"
      ;;
    both)
      _generate_ota_variant_info rootless || return 1
      OUTPUTS[PATCHED_OTA]="${VARIANT_PATCHED_OTA}"
      OUTPUTS[PATCHED_OTA_ROOTLESS]="${VARIANT_PATCHED_OTA}"
      MODULE_SELECTION_FINGERPRINT="${VARIANT_SELECTION_FINGERPRINT}"
      MODULE_SELECTION_FINGERPRINT_ROOTLESS="${VARIANT_SELECTION_FINGERPRINT}"

      _generate_ota_variant_info magisk || return 1
      OUTPUTS[PATCHED_OTA_MAGISK]="${VARIANT_PATCHED_OTA}"
      MODULE_SELECTION_FINGERPRINT_MAGISK="${VARIANT_SELECTION_FINGERPRINT}"

      # Keep the legacy singular values bound to the primary/rootless output.
      MODULE_SELECTION_FINGERPRINT="${MODULE_SELECTION_FINGERPRINT_ROOTLESS}"
      OUTPUTS[OTA_METADATA_ROOTLESS]="${DEVICE_NAME}-rootless.json"
      OUTPUTS[OTA_METADATA_MAGISK]="${DEVICE_NAME}-magisk.json"
      ;;
  esac
}

function _toml_trim() {
  local value="${1}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

function _toml_fail() {
  echo "Error: ${1}" >&2
  return 1
}

function _toml_decode_string() {
  local raw="${1}"
  local value="${raw:1:${#raw}-2}"
  local decoded='' char next index

  for ((index = 0; index < ${#value}; index++)); do
    char="${value:index:1}"
    if [[ "${char}" == "\\" ]]; then
      index=$((index + 1))
      [[ ${index} -lt ${#value} ]] || return 1
      next="${value:index:1}"
      [[ "${next}" == "\\" || "${next}" == '"' ]] || return 1
      decoded+="${next}"
    elif [[ "${char}" == '"' || "${char}" == $'\n' || "${char}" == $'\r' ]]; then
      return 1
    else
      decoded+="${char}"
    fi
  done

  printf '%s' "${decoded}"
}

function _toml_key_definition() {
  local section="${1}"
  local key="${2}"
  local legacy_mode="${3}"

  TOML_KEY_CANONICAL=''
  TOML_KEY_TYPE=''

  config_schema_lookup_key "${section}" "${key}" "${legacy_mode}" || return 1
  TOML_KEY_CANONICAL="${CONFIG_SCHEMA_CANONICAL}"
  TOML_KEY_TYPE="${CONFIG_SCHEMA_TYPE[${TOML_KEY_CANONICAL}]}"
}

function _toml_caller_override_present() {
  config_schema_caller_present "${1}"
}

function _toml_apply_value() {
  local canonical="${1}"
  local value="${2}"

  _toml_caller_override_present "${canonical}" && return 0
  config_schema_apply_value "${canonical}" "${value}"
}

function check_toml_env() {
  local toml_file="${1:-env.toml}"
  local line section='' raw_key raw_value key value type
  local legacy_mode=true seen_section=false
  declare -A seen_sections=()

  TOML_CONFIG_PRESENT=()
  TOML_CONFIG_VALUES=()
  [[ -f "${toml_file}" ]] || return 0

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="$(_toml_trim "${line}")"
    [[ -z "${line}" || "${line}" == \#* ]] && continue

    if [[ "${line}" =~ ^\[([a-z]+)\]$ ]]; then
      section="${BASH_REMATCH[1]}"
      case "${section}" in
        device|build|github) ;;
        *) _toml_fail "unknown configuration section: ${section}"; return 1 ;;
      esac
      [[ ${seen_sections[${section}]+x} ]] && {
        _toml_fail "duplicate configuration section: ${section}"
        return 1
      }
      seen_sections[${section}]=true
      seen_section=true
      [[ "${section}" != device ]] && legacy_mode=false
      continue
    fi

    [[ "${line}" == \[* ]] && {
      _toml_fail "malformed configuration section: ${line}"
      return 1
    }
    [[ "${line}" =~ ^([^=]+)=(.*)$ ]] || {
      _toml_fail "malformed configuration assignment: ${line}"
      return 1
    }
    raw_key="$(_toml_trim "${BASH_REMATCH[1]}")"
    raw_value="$(_toml_trim "${BASH_REMATCH[2]}")"

    case "${raw_key}" in
      \'*\')
        [[ "${raw_key: -1}" == "'" && ${#raw_key} -gt 2 ]] || {
          _toml_fail "malformed configuration key: ${raw_key}"
          return 1
        }
        key="${raw_key:1:${#raw_key}-2}"
        ;;
      *) key="${raw_key}" ;;
    esac
    [[ "${key}" =~ ^[A-Z_][A-Z0-9_]*$ ||
      "${key}" =~ ^(GRAPHENEOS|ADDITIONALS|MAGISK)\[[A-Z_][A-Z0-9_]*\]$ ]] || {
      _toml_fail "malformed configuration key: ${key}"
      return 1
    }

    if ! _toml_key_definition "${section}" "${key}" "${legacy_mode}"; then
      _toml_fail "unsupported configuration key in [${section:-legacy}]: ${key}"
      return 1
    fi
    type="${TOML_KEY_TYPE}"

    case "${raw_value}" in
      true|false) value="${raw_value}" ;;
      '"'*)
        [[ "${raw_value: -1}" == '"' && ${#raw_value} -ge 2 ]] || {
          _toml_fail "malformed configuration value for ${key}"
          return 1
        }
        value="$(_toml_decode_string "${raw_value}")" || {
          _toml_fail "malformed configuration string for ${key}"
          return 1
        }
        ;;
      *)
        _toml_fail "malformed configuration value for ${key}"
        return 1
        ;;
    esac

    if [[ "${type}" == string && ( "${raw_value}" == true || "${raw_value}" == false ) ]]; then
      _toml_fail "configuration value for ${key} must be a quoted string"
      return 1
    fi

    if ! config_schema_validate_value "${TOML_KEY_CANONICAL}" "${value}"; then
      if [[ "${type}" == boolean ]]; then
        _toml_fail "configuration value for ${key} must be true or false"
      else
        _toml_fail "configuration value for ${key} contains a newline"
      fi
      return 1
    fi

    local canonical="${TOML_KEY_CANONICAL}"
    [[ ${TOML_CONFIG_PRESENT[${canonical}]+x} ]] && {
      _toml_fail "duplicate configuration assignment: ${canonical}"
      return 1
    }
    TOML_CONFIG_PRESENT[${canonical}]=true
    TOML_CONFIG_VALUES[${canonical}]="${value}"
    _toml_apply_value "${canonical}" "${value}"
  done <"${toml_file}"

  if [[ "${seen_section}" == true ]]; then
    echo "Loaded typed configuration from \`${toml_file}\`."
  fi
}

function toml_config_has() {
  [[ ${TOML_CONFIG_PRESENT[${1}]+x} ]]
}

function toml_resolve_value() {
  local canonical="${1-}"
  local fallback="${2-}"

  # Keep the historical public adapter contract: callers may ask for an
  # unknown key and receive their fallback. Strict schema callers use the
  # config_schema_* helpers directly and still fail closed for unknown keys.
  if ! config_schema_key_exists "${canonical}"; then
    printf '%s' "${fallback}"
    return 0
  fi

  config_schema_resolve_value "$@"
}

function supported_tools() {
  local arg="${1:-}"
  local tools=("avbroot" "afsr" "alterinstaller" "custota" "custota-tool" "msd" "bcr" "oemunlockonboot" "my-avbroot-setup")

  if [[ "${arg}" == "cdd" ]]; then
    echo "${tools[@]}"
    return
  fi

  echo -e "Supported tools:"
  for tool in "${tools[@]}"; do
    echo -e "- ${tool}"
  done
  echo -e "- magisk"
}

function help() {
  cat <<EOF
Usage: source src/<file>.sh [functions] [arguments]
functions:
  - url_constructor        Run the URL Constructor function
    - arguments            Supported tool name.
                           Check 'supported_tools' for more info
  - generate_keys          Generate keys
  - help                   Show this help message
  - check_toml_env         Check TOML environment
  - supported_tools        List supported tools
EOF
}
