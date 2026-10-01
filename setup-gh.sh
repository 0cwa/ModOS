#!/usr/bin/env bash
set -eo pipefail

cd "$(git rev-parse --show-toplevel)"

command -v gh >/dev/null || { echo "gh CLI is required" >&2; exit 1; }
gh auth status >/dev/null 2>&1

repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
keys_dir=.keys
key_files=(avb.key ota.key ota.crt avb_pkmd.bin)

umask 077
mkdir -p "$keys_dir"

read -rsp "AVB key passphrase: " PASSPHRASE_AVB; echo
read -rsp "OTA key passphrase: " PASSPHRASE_OTA; echo
[[ -n "$PASSPHRASE_AVB" && -n "$PASSPHRASE_OTA" ]] || {
  echo "Passphrases must not be empty" >&2
  exit 1
}
export PASSPHRASE_AVB PASSPHRASE_OTA
trap 'unset PASSPHRASE_AVB PASSPHRASE_OTA' EXIT

present=0
for file in "${key_files[@]}"; do
  [[ -f "$keys_dir/$file" ]] && ((present += 1))
done

if (( present == 0 )); then
  source src/util_functions.sh
  run_executable_tool avbroot key generate-key -t rsa4096 -o "$keys_dir/avb.key" --pass-env-var PASSPHRASE_AVB
  run_executable_tool avbroot key generate-key -t rsa4096 -o "$keys_dir/ota.key" --pass-env-var PASSPHRASE_OTA
  run_executable_tool avbroot key extract-avb -k "$keys_dir/avb.key" -o "$keys_dir/avb_pkmd.bin" --pass-env-var PASSPHRASE_AVB
  run_executable_tool avbroot key generate-cert -k "$keys_dir/ota.key" -o "$keys_dir/ota.crt" --pass-env-var PASSPHRASE_OTA
elif (( present != ${#key_files[@]} )); then
  echo "Incomplete signing material in $keys_dir; refusing to overwrite it." >&2
  exit 1
fi

chmod 600 "$keys_dir"/{avb.key,ota.key,ota.crt,avb_pkmd.bin}

set_secret() {
  printf '%s' "$2" | gh secret set "$1" --repo "$repo"
}

set_secret AVB_KEY "$(base64 -w0 "$keys_dir/avb.key")"
set_secret CERT_OTA "$(base64 -w0 "$keys_dir/ota.crt")"
set_secret OTA_KEY "$(base64 -w0 "$keys_dir/ota.key")"
set_secret PASSPHRASE_AVB "$PASSPHRASE_AVB"
set_secret PASSPHRASE_OTA "$PASSPHRASE_OTA"

email="$(git config user.email || true)"
if [[ -z "$email" ]]; then
  login="$(gh api user --jq .login)"
  email="${login}@users.noreply.github.com"
fi
set_secret EMAIL "$email"

token="${GH_TOKEN:-}"
if [[ -z "$token" ]]; then
  token="$(gh auth token)"
fi
set_secret GH_TOKEN "$token"

echo "Configured $repo. Signing files are stored in $keys_dir/."
