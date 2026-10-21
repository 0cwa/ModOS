#!/usr/bin/env bash
# Source-first publication for releases containing the opt-in exfatprogs module.
set -o nounset -o pipefail -o errexit

die() { echo "::error::$*" >&2; exit 1; }
for name in RELEASE_TAG RELEASE_TITLE RELEASE_NOTES EXFAT_SOURCE_BUNDLE_PATH \
  EXFAT_SOURCE_BUNDLE_NAME EXFAT_SOURCE_BUNDLE_SHA256 ROOT_MODE_RESOLVED; do
  [[ -n "${!name-}" ]] || die "${name} is required"
done
[[ "${EXFAT_SOURCE_BUNDLE_VERIFIED:-}" == true ]] || die "source bundle verification signal is missing"
[[ "${EXFAT_SOURCE_BUNDLE_SHA256}" =~ ^[0-9a-f]{64}$ ]] || die "source bundle digest is invalid"
[[ -f "${EXFAT_SOURCE_BUNDLE_PATH}" && ! -L "${EXFAT_SOURCE_BUNDLE_PATH}" ]] || die "source bundle is missing or unsafe"
[[ "$(sha256sum -- "${EXFAT_SOURCE_BUNDLE_PATH}" | awk '{print $1}')" == "${EXFAT_SOURCE_BUNDLE_SHA256}" ]] || die "source bundle changed after generation"
[[ "$(basename -- "${EXFAT_SOURCE_BUNDLE_PATH}")" == "${EXFAT_SOURCE_BUNDLE_NAME}" ]] || die "source bundle name does not match its path"
[[ "${RELEASE_TAG}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "release tag is invalid"
[[ -f "${RELEASE_NOTES}" ]] || die "release notes are missing"

assets=("${EXFAT_SOURCE_BUNDLE_PATH}#${EXFAT_SOURCE_BUNDLE_NAME}")
if [[ "${ROOT_MODE_RESOLVED}" == both ]]; then
  for path in "${OUTPUTS_PATCHED_OTA_ROOTLESS-}" "${OUTPUTS_PATCHED_OTA_ROOTLESS-}.csig" \
    "${SELECTION_METADATA_ROOTLESS-}" "${OUTPUTS_PATCHED_OTA_MAGISK-}" \
    "${OUTPUTS_PATCHED_OTA_MAGISK-}.csig" "${SELECTION_METADATA_MAGISK-}"; do
    [[ -n "${path}" && -f "${path}" && -s "${path}" ]] || die "paired release asset missing: ${path}"
    assets+=("${path}")
  done
else
  for path in "${OUTPUTS_PATCHED_OTA-}" "${OUTPUTS_PATCHED_OTA-}.csig" "${SELECTION_METADATA-}"; do
    [[ -n "${path}" && -f "${path}" && -s "${path}" ]] || die "release asset missing: ${path}"
    assets+=("${path}")
  done
fi

is_draft=false
if state="$(gh release view "${RELEASE_TAG}" --json isDraft 2>/dev/null)"; then
  is_draft="$(jq -r '.isDraft' <<<"${state}")"
else
  gh release create "${RELEASE_TAG}" --draft --title "${RELEASE_TITLE}" --notes-file "${RELEASE_NOTES}"
  is_draft=true
fi

# Add the unique content-addressed source asset before exposing any new OTA
# asset. Reuse a prior exact-name asset only after downloading and rechecking it.
# Existing published releases remain published; new releases stay draft.
verify_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/exfat-source-verify.XXXXXX")"
cleanup() { rm -rf -- "${verify_dir}"; }
trap cleanup EXIT
verify_remote_source() {
  rm -f -- "${verify_dir:?}/${EXFAT_SOURCE_BUNDLE_NAME}"
  gh release download "${RELEASE_TAG}" --pattern "${EXFAT_SOURCE_BUNDLE_NAME}" --dir "${verify_dir}"
  local downloaded="${verify_dir}/${EXFAT_SOURCE_BUNDLE_NAME}"
  [[ -f "${downloaded}" && ! -L "${downloaded}" ]] || die "uploaded source asset could not be downloaded"
  [[ "$(sha256sum -- "${downloaded}" | awk '{print $1}')" == "${EXFAT_SOURCE_BUNDLE_SHA256}" ]] || die "uploaded source asset digest differs from verified bundle"
}
existing_assets="$(gh release view "${RELEASE_TAG}" --json assets --jq '.assets[].name')"
if grep -Fxq -- "${EXFAT_SOURCE_BUNDLE_NAME}" <<<"${existing_assets}"; then
  verify_remote_source
else
  gh release upload "${RELEASE_TAG}" "${assets[0]}"
  verify_remote_source
fi

gh release upload "${RELEASE_TAG}" --clobber "${assets[@]:1}"
asset_names="$(gh release view "${RELEASE_TAG}" --json assets --jq '.assets[].name')"
for asset in "${assets[@]}"; do
  name="${asset##*#}"
  name="$(basename -- "${name}")"
  grep -Fxq -- "${name}" <<<"${asset_names}" || die "release is missing uploaded asset: ${name}"
done
if [[ "${is_draft}" == true ]]; then
  gh release edit "${RELEASE_TAG}" --draft=false
fi
echo "Source bundle and release assets verified for ${RELEASE_TAG}."
