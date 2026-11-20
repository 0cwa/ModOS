#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

# This file consists of functions to verify the downloaded tools and signatures existence
# Verifier also looks after retries if the tool is not found
# Module signatures are checked by my-avbroot-setup. Magisk APK identity is
# checked here against the selected reviewed or explicitly supplied digest.

# Source the declarations file
source src/declarations.sh
source src/magisk.sh

# Initialize retry count and maximum retries
RETRY_COUNT=0
MAX_RETRIES=3

# Require lock/profile inputs to be regular, non-symlink files whose exact
# contents are already committed in the current PixeneOS checkout.
function verify_checked_in_locked_input() {
  local input_path="${1}"
  local repository_root resolved_path relative_path
  local committed_object working_object

  [[ -n "${input_path}" && -f "${input_path}" && ! -L "${input_path}" ]] ||
    return 1

  repository_root="$(git rev-parse --show-toplevel 2>/dev/null)" || return 1
  resolved_path="$(realpath -e -- "${input_path}" 2>/dev/null)" || return 1

  case "${resolved_path}" in
  "${repository_root}"/*)
    relative_path="${resolved_path#"${repository_root}"/}"
    ;;
  *)
    return 1
    ;;
  esac

  git -C "${repository_root}" ls-files --error-unmatch -- \
    "${relative_path}" >/dev/null 2>&1 || return 1
  git -C "${repository_root}" diff --quiet --no-ext-diff -- \
    "${relative_path}" || return 1
  git -C "${repository_root}" diff --cached --quiet --no-ext-diff -- \
    "${relative_path}" || return 1
  committed_object="$(
    git -C "${repository_root}" rev-parse --verify \
      "HEAD:${relative_path}" 2>/dev/null
  )" || return 1
  working_object="$(
    git -C "${repository_root}" hash-object -- "${resolved_path}" 2>/dev/null
  )" || return 1
  [[ "${working_object}" == "${committed_object}" ]]
}

function verify_fdroid_privileged_extension_inputs() {
  local lock_path="${1}"
  local profile_path="${2}"

  if [[ -z "${lock_path}" || -z "${profile_path}" ]]; then
    echo "Error: F-Droid locked mode requires an explicit lock and profile." >&2
    return 1
  fi

  if ! verify_checked_in_locked_input "${lock_path}" ||
    ! verify_checked_in_locked_input "${profile_path}"; then
    echo "Error: F-Droid lock and profile must be clean checked-in regular files." >&2
    return 1
  fi
}

# Function to look after number of times a retry has been made if the auto retry flag is enabled
function auto_retry_check() {
  if [[ "${ADDITIONALS[RETRY]}" == "true" ]]; then
    if ((RETRY_COUNT < MAX_RETRIES)); then
      RETRY_COUNT=$((RETRY_COUNT + 1))
      echo -e "Auto retry is enabled. Retrying (${RETRY_COUNT}/${MAX_RETRIES})...\n"
      RETRY="true"
      return 0
    else
      echo -e "Maximum retry limit reached. Exiting...\n"
      exit 1
    fi
  else
    echo -e "Auto retry is not enabled. Exiting...\n"
    exit 1
  fi
}

# Function to verify the downloaded tools and signatures
function verify_downloads() {
  # Verify the downloaded tools
  # If the tools are not present, exit the script
  # Else, continue with the script
  local tool="${1}"
  local tool_path=""

  if [[ "${tool}" == "afsr" || "${tool}" == "avbroot" || "${tool}" == "custota-tool" ]]; then
    echo "Error: executable tools require immutable-lock bootstrap verification." >&2
    return 1
  fi

  echo "Verifying \`${tool}\`..."

  if [[ "${tool}" == magisk ]]; then
    if ! run_magisk_artifact verify --apk "${WORKDIR}/modules/magisk.apk"; then
      RETRY=false
      return 1
    fi
    RETRY=false
    return 0
  fi

  if [[ -f "${WORKDIR}/modules/${tool}.zip" ]] && [ "${tool}" != "magisk" ]; then
    tool_path="${WORKDIR}/modules/${tool}.zip"
  elif [[ -f "${WORKDIR}/modules/${tool}.apk" ]]; then
    tool_path="${WORKDIR}/modules/${tool}.apk"
  elif [[ -d "${WORKDIR}/tools/${tool}" ]]; then
    tool_path="${WORKDIR}/tools/${tool}"
  fi

  # Check if the tool_path has a value
  if [[ -e "${tool_path}" ]]; then
    # Check if the tool is a directory or a file
    if [[ -d "${tool_path}" ]]; then
      echo -e "Tool ${tool} is a directory in \`${WORKDIR}/tools/\`. Verified.\n"
    elif [[ -f "${tool_path}" ]]; then
      echo -e "Module \`${tool_path}\` found and verified in \`${WORKDIR}/modules/\`.\n"
    fi
    RETRY="false"
  else
    echo -e "Error: \`${tool}\` not found in \`${WORKDIR}\`\n"
    auto_retry_check
    return $?
  fi

  # Require the matching signature for every downloaded artifact except for
  # `my-avbroot-setup` and `magisk`.
  if [[ "${tool}" != "my-avbroot-setup" && "${tool}" != "magisk" &&
    ! -f "${WORKDIR}/signatures/${tool}.zip.sig" ]]; then
    echo -e "Error: Signature for \`${tool}\` not found in \`${WORKDIR}/signatures\`\n"
    auto_retry_check
    return $?
  fi
}
