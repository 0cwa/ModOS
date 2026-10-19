#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

# This project is highly dependent on chenxiaolong's projects.
# Project PixeneOS needs to be up-to-date with chenxiaolong's projects

# make code more robust by catching unset variables, detecting errors in pipelines, and halting execution upon encountering errors
set -o nounset -o pipefail -o errexit

source src/fetcher.sh
source src/util_functions.sh

function main() {
  if [[ "${INTERACTIVE_MODE}" == "true" ]]; then
    echo -e "Running in interactive mode...\n"
    check_toml_env
  fi

  resolve_rom_profile
  enforce_output_policy "${OUTPUT_SCOPE}"
  activate_development_profile
  resolve_root_mode

  # Fetch the latest ROM version and Magisk
  get_latest_version
  # The normal provider must never silently advance this test release to an
  # unreviewed nightly. Enforce both the official API digest and exact target
  # before downloading dependencies or accessing repository signing keys.
  if [[ "${ROM_FAMILY:-}" == lineageos && "${DEVICE_NAME:-}" == pdx235 &&
    "${OUTPUT_SCOPE}" == published ]]; then
    enforce_pdx235_supported_source
  fi
  # Check for requirements and download them accordingly
  check_and_download_dependencies
  # Patch the OTA, sign it
  create_and_make_release
}

main "$@"
