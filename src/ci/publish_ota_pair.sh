#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

# Publish a rootless + Magisk pair after both release asset triplets have been
# uploaded and verified. This is intentionally separate from publish_ota.sh so
# the established single-variant path remains unchanged.

set -o nounset -o pipefail -o errexit

# Belt-and-suspenders check: both output flavors must remain unpublished.
_publish_ci_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "${_publish_ci_dir}/pdx235_release_quarantine.sh"
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

validate_variant() {
  local flavor="${1}"
  local artifact="${2}"
  local selection="${3}"
  local metadata="${4}"
  local fingerprint="${5}"
  local artifact_name selection_name metadata_name

  [[ "${flavor}" == rootless || "${flavor}" == magisk ]] ||
    die "Unsupported ROM flavor: ${flavor}"
  [[ "${fingerprint}" =~ ^[0-9a-f]{64}$ ]] ||
    die "${flavor} fingerprint must be SHA-256"

  artifact_name="$(basename -- "${artifact}")"
  selection_name="$(basename -- "${selection}")"
  metadata_name="$(basename -- "${metadata}")"
  [[ "${artifact_name}" == "${artifact}" ]] ||
    die "${flavor} artifact must be in the current workspace"
  [[ "${selection_name}" == "${selection}" ]] ||
    die "${flavor} selection metadata must be in the current workspace"
  [[ "${metadata_name}" == "${metadata}" ]] ||
    die "${flavor} OTA metadata must be in the current workspace"
  [[ "${selection_name}" == "${artifact_name}.selection.json" ]] ||
    die "${flavor} selection metadata does not belong to its OTA"

  require_regular_file "${flavor} OTA artifact" "${artifact}"
  require_regular_file "${flavor} OTA signature" "${artifact}.csig"
  require_regular_file "${flavor} selection metadata" "${selection}"
  require_regular_file "${flavor} generated device metadata" "${metadata}"

  jq -e \
    --arg artifact "${artifact_name}" \
    --arg device "${DEVICE_NAME}" \
    --arg family "${ROM_FAMILY}" \
    --arg version "${GRAPHENEOS_VERSION}" \
    --arg fingerprint "${fingerprint}" \
    --arg scope "${OUTPUT_SCOPE}" '
      type == "object" and
      .schema_version == 2 and
      .artifact_name == $artifact and
      .device == $device and
      .rom_family == $family and
      .grapheneos_version == $version and
      .module_selection_fingerprint == $fingerprint and
      .output_scope == $scope
    ' "${selection}" >/dev/null ||
    die "${flavor} selection metadata does not match the resolved variant"
}

ROM_FAMILY="${ROM_FAMILY-}"
ROM_PROFILE_PROVIDER="${ROM_PROFILE_PROVIDER-}"
DEVICE_NAME="${DEVICE_NAME-}"
GRAPHENEOS_VERSION="${GRAPHENEOS_VERSION-}"
RELEASE_TYPE="${RELEASE_TYPE-}"
OUTPUT_SCOPE="${OUTPUT_SCOPE-}"

ROOTLESS_OTA_ARTIFACT_PATH="${ROOTLESS_OTA_ARTIFACT_PATH-}"
ROOTLESS_SELECTION_METADATA_PATH="${ROOTLESS_SELECTION_METADATA_PATH-}"
ROOTLESS_OTA_METADATA_PATH="${ROOTLESS_OTA_METADATA_PATH-}"
ROOTLESS_MODULE_SELECTION_FINGERPRINT="${ROOTLESS_MODULE_SELECTION_FINGERPRINT-}"

MAGISK_OTA_ARTIFACT_PATH="${MAGISK_OTA_ARTIFACT_PATH-}"
MAGISK_SELECTION_METADATA_PATH="${MAGISK_SELECTION_METADATA_PATH-}"
MAGISK_OTA_METADATA_PATH="${MAGISK_OTA_METADATA_PATH-}"
MAGISK_MODULE_SELECTION_FINGERPRINT="${MAGISK_MODULE_SELECTION_FINGERPRINT-}"

for field in ROM_FAMILY ROM_PROFILE_PROVIDER DEVICE_NAME GRAPHENEOS_VERSION RELEASE_TYPE OUTPUT_SCOPE; do
  require_value "${field}" "${!field}"
done
require_slug ROM_FAMILY "${ROM_FAMILY}"
require_slug ROM_PROFILE_PROVIDER "${ROM_PROFILE_PROVIDER}"
require_slug DEVICE_NAME "${DEVICE_NAME}"
require_slug GRAPHENEOS_VERSION "${GRAPHENEOS_VERSION}"

case "${RELEASE_TYPE}" in
  default|force-publish) ;;
  *) die "Dual publication requires default or force-publish release type" ;;
esac
[[ "${OUTPUT_SCOPE}" == published ]] ||
  die "Dual OTA publication requires published output scope"
enforce_pdx235_publication_scope \
  "${ROM_FAMILY}" "${DEVICE_NAME}" "${OUTPUT_SCOPE}" \
  "${GRAPHENEOS_UPDATE_CHANNEL-}" "${GRAPHENEOS_VERSION}" || exit 1

validate_variant rootless \
  "${ROOTLESS_OTA_ARTIFACT_PATH}" \
  "${ROOTLESS_SELECTION_METADATA_PATH}" \
  "${ROOTLESS_OTA_METADATA_PATH}" \
  "${ROOTLESS_MODULE_SELECTION_FINGERPRINT}"
validate_variant magisk \
  "${MAGISK_OTA_ARTIFACT_PATH}" \
  "${MAGISK_SELECTION_METADATA_PATH}" \
  "${MAGISK_OTA_METADATA_PATH}" \
  "${MAGISK_MODULE_SELECTION_FINGERPRINT}"

asset_names="$(gh release view "${GRAPHENEOS_VERSION}" --json assets --jq '.assets[].name')" ||
  die "Could not verify release assets for ${GRAPHENEOS_VERSION}"
for expected_asset in \
  "${ROOTLESS_OTA_ARTIFACT_PATH}" \
  "${ROOTLESS_OTA_ARTIFACT_PATH}.csig" \
  "${ROOTLESS_SELECTION_METADATA_PATH}" \
  "${MAGISK_OTA_ARTIFACT_PATH}" \
  "${MAGISK_OTA_ARTIFACT_PATH}.csig" \
  "${MAGISK_SELECTION_METADATA_PATH}"; do
  grep -Fxq -- "${expected_asset}" <<<"${asset_names}" ||
    die "Release is missing expected asset: ${expected_asset}"
done

git config user.email "${GIT_COMMIT_EMAIL-}"
git config user.name "${GIT_COMMIT_NAME-}"
current_commit="$(git rev-parse --short HEAD)"

git checkout gh-pages
mkdir -p \
  rootless \
  magisk \
  "variants/${ROM_FAMILY}/rootless" \
  "variants/${ROM_FAMILY}/magisk"

rootless_target="rootless/${DEVICE_NAME}.json"
magisk_target="magisk/${DEVICE_NAME}.json"
rootless_variant="variants/${ROM_FAMILY}/rootless/${DEVICE_NAME}-${ROOTLESS_MODULE_SELECTION_FINGERPRINT}.json"
magisk_variant="variants/${ROM_FAMILY}/magisk/${DEVICE_NAME}-${MAGISK_MODULE_SELECTION_FINGERPRINT}.json"

cp -- "${ROOTLESS_OTA_METADATA_PATH}" "${rootless_target}"
cp -- "${ROOTLESS_OTA_METADATA_PATH}" "${rootless_variant}"
cp -- "${MAGISK_OTA_METADATA_PATH}" "${magisk_target}"
cp -- "${MAGISK_OTA_METADATA_PATH}" "${magisk_variant}"
git add -- "${rootless_target}" "${rootless_variant}" "${magisk_target}" "${magisk_variant}"

# The Sony device reports XQ-DC72 as its OTA pre-device. Custota therefore
# requests XQ-DC72.json, not pdx235.json, for either build flavor.
# Write aliases from the exact canonical, release-asset-verified metadata.
publish_custota_device_alias "${ROM_FAMILY}" "${DEVICE_NAME}" rootless "${rootless_target}"
publish_custota_device_alias "${ROM_FAMILY}" "${DEVICE_NAME}" magisk "${magisk_target}"

if [[ "${RELEASE_TYPE}" == force-publish ]]; then
  marker_file="force-publish/${DEVICE_NAME}.marker"
  mkdir -p -- "$(dirname -- "${marker_file}")"
  printf 'force-publish run %s (attempt %s)\n' \
    "${GITHUB_RUN_ID-unknown}" "${GITHUB_RUN_ATTEMPT-unknown}" >"${marker_file}"
  git add -- "${marker_file}"
fi

if ! git diff-index --quiet HEAD; then
  git commit -m "release(${current_commit}): publish ${ROM_FAMILY} ${GRAPHENEOS_VERSION} root pair"
  git push origin gh-pages
fi

echo "Published paired OTA metadata for ${ROM_FAMILY}/${DEVICE_NAME}."
