#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

# Decide whether the release workflow needs to build. A successful skip is
# allowed only for one unambiguous, variant-aware asset triplet:
#
#   <artifact>.zip
#   <artifact>.zip.csig
#   <artifact>.zip.selection.json
#
# The selection metadata is the authority for the artifact identity. API,
# JSON, metadata, or asset-shape uncertainty never becomes a skip decision.
#
# Reads from the environment:
#   DEVICE_NAME         Device code name
#   GRAPHENEOS_VERSION  GrapheneOS version to release
#   REPOSITORY          GitHub repository as owner/name
#   ROM_FAMILY          ROM family expected in selection metadata
#   OUTPUT_SCOPE        Output scope expected in selection metadata
#   ROOT                "true" builds the magisk flavor, anything else rootless
#   DEBUG               "true" adds the debug-adb filename suffix
#   MAGISK_VERSION      Magisk version used in rooted output filenames
#   EXPECTED_VARIANT    Canonical module-selection SHA-256 identity
#   FORCE_UPDATE        "true" bypasses an existing-asset skip
#
# Writes FORCE_REBUILD=true to GITHUB_ENV when FORCE_UPDATE requests a build.

set -o nounset -o pipefail -o errexit

die() {
  echo "::error::$*" >&2
  return 1
}

write_output() {
  local value="${1}"

  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'should_build=%s\n' "${value}" >>"${GITHUB_OUTPUT}"
  fi
}

write_force_rebuild() {
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    printf 'FORCE_REBUILD=true\n' >>"${GITHUB_ENV}"
  fi
}

continue_with_build() {
  local reason="${1}"

  echo "${reason} Proceeding with build."
  if [[ "${FORCE_UPDATE:-false}" == 'true' ]]; then
    write_force_rebuild
  fi
  write_output true
  exit 0
}

[[ -n "${DEVICE_NAME:-}" ]] || die "DEVICE_NAME is required"
[[ -n "${GRAPHENEOS_VERSION:-}" ]] || die "GRAPHENEOS_VERSION is required"
[[ "${DEVICE_NAME}" =~ ^[a-z0-9_]+$ ]] ||
  die "DEVICE_NAME must contain only lowercase letters, digits, and underscores"
[[ "${GRAPHENEOS_VERSION}" =~ ^[A-Za-z0-9._-]+$ ]] ||
  die "GRAPHENEOS_VERSION contains unsupported characters"

repository="${REPOSITORY:-}"
[[ "${repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] ||
  die "REPOSITORY must be an owner/name pair"

rom_family="${ROM_FAMILY:-}"
output_scope="${OUTPUT_SCOPE:-}"
if [[ -z "${rom_family}" || -z "${output_scope}" ||
  "${rom_family}" == *$'\n'* || "${rom_family}" == *$'\r'* ||
  "${output_scope}" == *$'\n'* || "${output_scope}" == *$'\r'* ]]; then
  continue_with_build "Selection metadata context is unresolved."
fi
[[ "${rom_family}" =~ ^[a-z0-9_-]+$ ]] ||
  continue_with_build "Selection ROM family is invalid."
[[ "${output_scope}" =~ ^[a-z0-9_-]+$ ]] ||
  continue_with_build "Selection output scope is invalid."

root="${ROOT:-false}"
debug="${DEBUG:-false}"
case "${root}" in
  true|false) ;;
  *) continue_with_build "Root selection is unresolved." ;;
esac
case "${debug}" in
  true|false) ;;
  *) continue_with_build "Debug selection is unresolved." ;;
esac

expected_variant="${EXPECTED_VARIANT:-}"
if [[ ! "${expected_variant}" =~ ^[0-9a-f]{64}$ ]]; then
  continue_with_build "Expected selection identity is unresolved."
fi

# For pdx235's OTA preflight a module fingerprint alone is insufficient:
# the source ZIP could have changed under the same date, or the ModOS build
# logic could have changed while the module selection stayed constant.
# Historical selection files without these fields always trigger rebuilding.
source_sha="${SOURCE_OTA_SHA256:-}"
source_target="${SOURCE_OTA_TARGET:-}"
build_commit="${BUILD_COMMIT:-}"
if [[ "${rom_family}" == lineageos && "${DEVICE_NAME}" == pdx235 ]]; then
  if [[ ! "${source_sha}" =~ ^[0-9a-f]{64}$ ||
        ! "${source_target}" =~ ^lineage-24[.]0-[0-9]{8}-nightly-pdx235-signed$ ||
        ! "${build_commit}" =~ ^[0-9a-f]{40}$ ]]; then
    continue_with_build "Source or exact ModOS build identity is not provable."
  fi
fi

if [[ "${root}" == 'true' ]]; then
  magisk_version="${MAGISK_VERSION:-}"
  [[ "${magisk_version}" =~ ^[A-Za-z0-9._-]+$ ]] ||
    continue_with_build "Magisk version for the rooted filename is unresolved."
  expected_flavor="magisk-${magisk_version}"
else
  expected_flavor='rootless'
fi
debug_suffix=''
[[ "${debug}" == 'true' ]] && debug_suffix='-debug-adb'

if ! git show-ref --tags --verify --quiet "refs/tags/${GRAPHENEOS_VERSION}"; then
  echo "Tag ${GRAPHENEOS_VERSION} does not exist. Proceeding with build."
  if [[ "${FORCE_UPDATE:-false}" == 'true' ]]; then
    write_force_rebuild
  fi
  write_output true
  exit 0
fi

echo "Tag ${GRAPHENEOS_VERSION} exists. Checking the expected ${expected_flavor} variant."
response_file="$(mktemp)"
trap 'rm -f -- "${response_file}"' EXIT
repo_url="https://api.github.com/repos/${repository}/releases/tags/${GRAPHENEOS_VERSION}?per_page=100"

if ! curl --silent --show-error --location --fail --retry 2 --retry-delay 1 \
  --output "${response_file}" "${repo_url}"; then
  die "GitHub release API request failed"
fi

if ! jq -e 'type == "object" and has("assets") and (.assets | type == "array")' \
  "${response_file}" >/dev/null; then
  die "GitHub release API returned malformed or incomplete JSON"
fi

if jq -e 'has("message") or has("documentation_url") or has("errors")' \
  "${response_file}" >/dev/null; then
  die "GitHub release API returned an error response"
fi

if ! jq -e 'all(.assets[]; type == "object" and (.name | type == "string") and
  (.name | (length > 0 and (contains("\n") | not) and (contains("\r") | not))))' \
  "${response_file}" >/dev/null; then
  die "GitHub release API returned an invalid asset entry"
fi

if ! jq -e '([.assets[].name] | unique | length) == (.assets | length)' \
  "${response_file}" >/dev/null; then
  die "GitHub release API returned duplicate asset names"
fi

artifact_prefix="${DEVICE_NAME}-${GRAPHENEOS_VERSION}-${expected_flavor}${debug_suffix}-${expected_variant}-"
artifact_suffix_pattern='^[0-9a-f]{7,}(-dirty)?\.zip$'
mapfile -t candidate_metadata < <(
  jq -r --arg token "${expected_variant}" '
    .assets[].name
    | select(endswith(".selection.json"))
    | select(contains("-" + $token + "-"))
  ' "${response_file}"
)

if (( ${#candidate_metadata[@]} == 0 )); then
  continue_with_build "No selection metadata asset contains the expected identity."
fi

matching_artifact=''
matching_metadata=''
matching_count=0
for metadata_asset in "${candidate_metadata[@]}"; do
  canonical_metadata_url="https://github.com/${repository}/releases/download/${GRAPHENEOS_VERSION}/${metadata_asset}"
  metadata_url="$(jq -r --arg name "${metadata_asset}" '
    .assets[] | select(.name == $name) | .browser_download_url // empty
  ' "${response_file}")"
  [[ "${metadata_url}" == "${canonical_metadata_url}" ]] ||
    die "Selection metadata asset has no canonical download URL: ${metadata_asset}"

  metadata_file="$(mktemp)"
  if ! curl --silent --show-error --location --fail --retry 2 --retry-delay 1 \
    --output "${metadata_file}" "${metadata_url}"; then
    rm -f -- "${metadata_file}"
    die "Selection metadata download failed: ${metadata_asset}"
  fi

  if ! jq -e '
    type == "object" and
    .schema_version == 2 and
    (.device | type == "string") and
    (.rom_family | type == "string") and
    (.grapheneos_version | type == "string") and
    (.output_scope | type == "string") and
    (.module_selection_fingerprint | type == "string") and
    (.artifact_name | type == "string") and
    (try (.artifact_name | endswith(".zip")) catch false)
  ' "${metadata_file}" >/dev/null; then
    if ! jq -e 'type == "object"' "${metadata_file}" >/dev/null 2>&1; then
      rm -f -- "${metadata_file}"
      die "Selection metadata is not valid JSON: ${metadata_asset}"
    fi
    rm -f -- "${metadata_file}"
    continue_with_build "Selection metadata is missing the required identity fields."
  fi

  artifact_name="$(jq -r \
    --arg metadata_asset "${metadata_asset}" \
    --arg device "${DEVICE_NAME}" \
    --arg version "${GRAPHENEOS_VERSION}" \
    --arg family "${rom_family}" \
    --arg scope "${output_scope}" \
    --arg expected "${expected_variant}" \
    --arg prefix "${artifact_prefix}" \
    --arg suffix_pattern "${artifact_suffix_pattern}" \
    --arg source_sha "${source_sha}" \
    --arg source_target "${source_target}" \
    --arg build_commit "${build_commit}" '
      if .device == $device and
        .rom_family == $family and
        .grapheneos_version == $version and
        .output_scope == $scope and
        .module_selection_fingerprint == $expected and
        (if $family == "lineageos" and $device == "pdx235"
         then .source_ota_sha256 == $source_sha and
              .source_ota_target == $source_target and
              .modos_commit == $build_commit
         else true end) and
        .artifact_name == ($metadata_asset | sub("\\.selection\\.json$"; "")) and
        (.artifact_name | startswith($prefix)) and
        ((.artifact_name | ltrimstr($prefix)) | test($suffix_pattern))
      then .artifact_name else empty end
    ' "${metadata_file}")"
  rm -f -- "${metadata_file}"

  if [[ -n "${artifact_name}" ]]; then
    matching_count=$((matching_count + 1))
    matching_artifact="${artifact_name}"
    matching_metadata="${metadata_asset}"
  fi
done

if (( matching_count > 1 )); then
  die "Multiple selection metadata assets match the expected variant"
fi
if (( matching_count == 0 )); then
  continue_with_build "Selection metadata does not confirm the requested variant."
fi

if ! jq -e --arg name "${matching_artifact}" \
  'any(.assets[]; .name == $name)' "${response_file}" >/dev/null; then
  continue_with_build "${matching_artifact} is missing."
fi
if ! jq -e --arg name "${matching_artifact}.csig" \
  'any(.assets[]; .name == $name)' "${response_file}" >/dev/null; then
  continue_with_build "${matching_artifact}.csig is missing."
fi
if ! jq -e --arg name "${matching_artifact}.selection.json" \
  'any(.assets[]; .name == $name)' "${response_file}" >/dev/null; then
  continue_with_build "${matching_artifact}.selection.json is missing."
fi

if [[ "${FORCE_UPDATE:-false}" == 'true' ]]; then
  echo "Existing matching assets found, but FORCE_UPDATE is enabled. Proceeding with build."
  write_force_rebuild
  write_output true
  exit 0
fi

echo "${matching_artifact}, its signature, and ${matching_metadata} already exist. Skipping build."
write_output false
exit 0
