#!/usr/bin/env bash
# Build-only outputs deliberately use a disposable signer, never release keys.
set -euo pipefail
umask 077
: "${GITHUB_ENV:?GITHUB_ENV is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
key_dir="$(mktemp -d "${RUNNER_TEMP}/modos-test-signing.XXXXXX")"
trap 'rm -rf "${key_dir}"' EXIT
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 \
  -out "${key_dir}/avb.key" 2>/dev/null
openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 7 \
  -subj '/CN=ModOS disposable build-only signer/' \
  -keyout "${key_dir}/ota.key" -out "${key_dir}/ota.crt" 2>/dev/null
for binding in AVB:avb.key OTA:ota.key CERT_OTA:ota.crt; do
  name="${binding%%:*}"
  encoded="$(base64 -w0 "${key_dir}/${binding#*:}")"
  printf '::add-mask::%s\n' "${encoded}"
  printf 'KEYS_%s_BASE64=%s\n' "${name}" "${encoded}" >> "${GITHUB_ENV}"
done
printf '%s\n' 'Build-only output uses an ephemeral test signer; it is not a release.' >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
