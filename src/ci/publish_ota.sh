#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Publish already-generated OTA metadata after the release asset triplet has
# been verified. This helper deliberately consumes resolved values from the
# build workflow; it does not load configuration or derive selection identity.
#
# Required publication inputs:
#   ROM_FAMILY               resolved ROM family/profile family
#   ROM_PROFILE_PROVIDER     resolved provider from the ROM profile
#   ROM_FLAVOR               resolved OTA flavor (magisk or rootless)
#   DEVICE_NAME              resolved device code name
#   MODULE_SELECTION_FINGERPRINT
#   GRAPHENEOS_VERSION       release version/tag
#   RELEASE_TYPE             default or force-publish
#   OUTPUT_SCOPE             published, or local-unpublished for a no-op
#   OTA_ARTIFACT_PATH        generated OTA asset path
#   SELECTION_METADATA_PATH  generated selection metadata path
#   OTA_METADATA_PATH        generated provider-neutral device metadata path
#
# GIT_COMMIT_EMAIL and GIT_COMMIT_NAME are used only when a publication is
# requested. GitHub CLI authentication/repository context is supplied by the
# workflow (GH_TOKEN/GITHUB_REPOSITORY).

set -o nounset -o pipefail -o errexit

# Recheck the same immutable release quarantine when invoked directly.
_publish_ci_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "${_publish_ci_dir}/pdx235_release_quarantine.sh"
source "${_publish_ci_dir}/pdx235_rootless_hold.sh"
source "${_publish_ci_dir}/publish_custota_device_alias.sh"
unset _publish_ci_dir

die() {
  echo "::error::$*" >&2
  return 1
}

require_value() {
  local name="${1}"
  local value="${2-}"

  [[ -n "${value}" && "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] ||
    die "${name} is required and must not contain line breaks"
}

require_slug() {
  local name="${1}"
  local value="${2}"

  [[ "${value}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
    die "${name} contains unsupported characters"
}

require_regular_file() {
  local name="${1}"
  local path="${2}"

  [[ -f "${path}" ]] || die "Missing ${name}: ${path}"
  [[ -s "${path}" ]] || die "Empty ${name}: ${path}"
}

ROM_FAMILY="${ROM_FAMILY-}"
ROM_PROFILE_PROVIDER="${ROM_PROFILE_PROVIDER-}"
ROM_FLAVOR="${ROM_FLAVOR-}"
DEVICE_NAME="${DEVICE_NAME-}"
MODULE_SELECTION_FINGERPRINT="${MODULE_SELECTION_FINGERPRINT-}"
GRAPHENEOS_VERSION="${GRAPHENEOS_VERSION-}"
RELEASE_TYPE="${RELEASE_TYPE-}"
OUTPUT_SCOPE="${OUTPUT_SCOPE-}"
OTA_ARTIFACT_PATH="${OTA_ARTIFACT_PATH-}"
SELECTION_METADATA_PATH="${SELECTION_METADATA_PATH-}"
OTA_METADATA_PATH="${OTA_METADATA_PATH-}"

require_value RELEASE_TYPE "${RELEASE_TYPE}"
require_value OUTPUT_SCOPE "${OUTPUT_SCOPE}"
case "${RELEASE_TYPE}" in
  default|force-publish) ;;
  build-only)
    [[ "${OUTPUT_SCOPE}" == 'local-unpublished' ]] ||
      die "build-only publication requires local-unpublished output"
    echo "Build-only output is local-unpublished; OTA publication skipped."
    exit 0
    ;;
  *) die "Unsupported RELEASE_TYPE: ${RELEASE_TYPE}" ;;
esac

if [[ "${OUTPUT_SCOPE}" == 'local-unpublished' ]]; then
  echo "Local-unpublished output; OTA publication skipped."
  exit 0
fi
[[ "${OUTPUT_SCOPE}" == 'published' ]] ||
  die "OTA publication requires published output scope"
enforce_pdx235_care_map_quarantine \
  "${ROM_FAMILY}" "${DEVICE_NAME}" "${OUTPUT_SCOPE}" || exit 1
enforce_pdx235_rootless_hold "${ROM_FAMILY}" "${DEVICE_NAME}" "${ROM_FLAVOR}" "${OUTPUT_SCOPE}" || exit 1

for field in \
  ROM_FAMILY \
  ROM_PROFILE_PROVIDER \
  ROM_FLAVOR \
  DEVICE_NAME \
  MODULE_SELECTION_FINGERPRINT \
  GRAPHENEOS_VERSION \
  OTA_ARTIFACT_PATH \
  SELECTION_METADATA_PATH \
  OTA_METADATA_PATH; do
  require_value "${field}" "${!field}"
done

require_slug ROM_FAMILY "${ROM_FAMILY}"
require_slug ROM_PROFILE_PROVIDER "${ROM_PROFILE_PROVIDER}"
require_slug DEVICE_NAME "${DEVICE_NAME}"
require_slug GRAPHENEOS_VERSION "${GRAPHENEOS_VERSION}"
[[ "${ROM_FLAVOR}" == 'magisk' || "${ROM_FLAVOR}" == 'rootless' ]] ||
  die "ROM_FLAVOR must be magisk or rootless"
[[ "${MODULE_SELECTION_FINGERPRINT}" =~ ^[0-9a-f]{64}$ ]] ||
  die "MODULE_SELECTION_FINGERPRINT must be a SHA-256 fingerprint"

artifact_name="$(basename -- "${OTA_ARTIFACT_PATH}")"
selection_metadata_name="$(basename -- "${SELECTION_METADATA_PATH}")"
metadata_name="$(basename -- "${OTA_METADATA_PATH}")"
[[ "${artifact_name}" == "${OTA_ARTIFACT_PATH}" ]] ||
  die "OTA_ARTIFACT_PATH must name a file in the current workspace"
[[ "${selection_metadata_name}" == "${SELECTION_METADATA_PATH}" ]] ||
  die "SELECTION_METADATA_PATH must name a file in the current workspace"
[[ "${metadata_name}" == "${OTA_METADATA_PATH}" ]] ||
  die "OTA_METADATA_PATH must name a file in the current workspace"
[[ "${metadata_name}" == "${DEVICE_NAME}.json" ]] ||
  die "OTA_METADATA_PATH must be the canonical device metadata path"
[[ "${selection_metadata_name}" == "${artifact_name}.selection.json" ]] ||
  die "Selection metadata does not belong to the OTA artifact"

require_regular_file "OTA artifact" "${OTA_ARTIFACT_PATH}"
require_regular_file "OTA signature" "${OTA_ARTIFACT_PATH}.csig"
require_regular_file "selection metadata" "${SELECTION_METADATA_PATH}"
require_regular_file "generated device metadata" "${OTA_METADATA_PATH}"

# The selection document is the canonical local proof that the release asset
# belongs to this resolved variant. Do not reconstruct these fields here.
jq -e \
  --arg artifact "${artifact_name}" \
  --arg device "${DEVICE_NAME}" \
  --arg family "${ROM_FAMILY}" \
  --arg version "${GRAPHENEOS_VERSION}" \
  --arg fingerprint "${MODULE_SELECTION_FINGERPRINT}" \
  --arg scope "${OUTPUT_SCOPE}" '
    type == "object" and
    .schema_version == 2 and
    .artifact_name == $artifact and
    .device == $device and
    .rom_family == $family and
    .grapheneos_version == $version and
    .module_selection_fingerprint == $fingerprint and
    .output_scope == $scope
  ' "${SELECTION_METADATA_PATH}" >/dev/null ||
  die "Selection metadata does not match the resolved publication variant"

# The release action must have completed before this step. Verify all assets
# by exact name before checking out gh-pages or changing either OTA pointer.
asset_names="$(gh release view "${GRAPHENEOS_VERSION}" --json assets --jq '.assets[].name')" ||
  die "Could not verify release assets for ${GRAPHENEOS_VERSION}"
for expected_asset in \
  "${artifact_name}" \
  "${artifact_name}.csig" \
  "${selection_metadata_name}"; do
  grep -Fxq -- "${expected_asset}" <<<"${asset_names}" ||
    die "Release is missing expected asset: ${expected_asset}"
done
echo "Verified release assets for ${GRAPHENEOS_VERSION}."

git config user.email "${GIT_COMMIT_EMAIL-}"
git config user.name "${GIT_COMMIT_NAME-}"
current_commit="$(git rev-parse --short HEAD)"

git checkout gh-pages
target_file="${ROM_FLAVOR}/${DEVICE_NAME}.json"
variant_file="variants/${ROM_FAMILY}/${ROM_FLAVOR}/${DEVICE_NAME}-${MODULE_SELECTION_FINGERPRINT}.json"
mkdir -p -- "${ROM_FLAVOR}" "$(dirname -- "${variant_file}")"
cp -- "${OTA_METADATA_PATH}" "${target_file}"
cp -- "${OTA_METADATA_PATH}" "${variant_file}"
git add -- "${target_file}" "${variant_file}"

# Keep Custota's pre-device XQ-DC72 entry aligned with the verified pdx235
# metadata also when only one OTA flavor is published.
publish_custota_device_alias "${ROM_FAMILY}" "${DEVICE_NAME}" "${ROM_FLAVOR}" "${target_file}"

if [[ "${RELEASE_TYPE}" == 'force-publish' ]]; then
  marker_file="force-publish/${DEVICE_NAME}.marker"
  mkdir -p -- "$(dirname -- "${marker_file}")"
  printf 'force-publish run %s (attempt %s)\n' \
    "${GITHUB_RUN_ID-unknown}" "${GITHUB_RUN_ATTEMPT-unknown}" >"${marker_file}"
  git add -- "${marker_file}"
fi

if ! git diff-index --quiet HEAD; then
  git commit -m "release(${current_commit}): publish ${ROM_FAMILY} ${GRAPHENEOS_VERSION} ${MODULE_SELECTION_FINGERPRINT}"
  git push origin gh-pages
fi

echo "Published OTA metadata for ${ROM_FAMILY}/${ROM_FLAVOR}/${DEVICE_NAME}."
