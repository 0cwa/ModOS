#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Unmodified reference extraction only. NEVER modify OTA partitions or sign.
set -euo pipefail

if [[ "$#" != 3 ]]; then
  echo "Usage: $0 <official-pdx235-ota.zip> <empty-output-dir> <official-API-sha256>" >&2
  exit 2
fi
ota="$1"
dest="$2"
expected_sha="$3"

[[ "${expected_sha}" =~ ^[0-9a-f]{64}$ ]] || {
  echo "Error: the official LineageOS API SHA-256 is required." >&2
  exit 1
}
[[ -f "${ota}" && ! -L "${ota}" ]] || {
  echo "Error: expected an existing regular OTA ZIP file." >&2
  exit 1
}
[[ ! -L "${dest}" && ( ! -e "${dest}" || ( -d "${dest}" && -z "$(ls -A -- "${dest}")" ) ) ]] || {
  echo "Error: reference extraction directory must be new or empty." >&2
  exit 1
}

for program in unzip openssl sha256sum avbroot debugfs python3; do
  command -v "${program}" >/dev/null || {
    echo "Error: required baseline host tool ${program} is unavailable." >&2
    exit 1
  }
done

actual_sha="$(sha256sum -- "${ota}")"
[[ "${actual_sha%% *}" == "${expected_sha}" ]] || {
  echo "Error: source OTA differs from the official LineageOS API SHA-256." >&2
  exit 1
}
mkdir -p -- "${dest}"
cert_path="${dest}/lineage-official-otacert.pem"
unzip -p -- "${ota}" META-INF/com/android/otacert >"${cert_path}"
[[ -s "${cert_path}" ]] || {
  echo "Error: source ZIP lacks its official OTA certificate." >&2
  exit 1
}

# Independent LineageOS publisher trust anchor. The certificate *embedded*
# inside the ZIP is NOT trusted by itself: avbroot must also cryptographically
# verify the complete OTA signature against precisely that certificate.
source src/source_care_map_policy.sh
source_care_map_require_lineageos_pdx235_publisher lineageos pdx235 "${ota}" "${cert_path}"
avbroot ota verify --input "${ota}" --cert-ota "${cert_path}"

# OTA full-payload partition extraction follows successful digest and
# signature verification. No modification, AVB resigning, or OTA emission.
images="${dest}/images"
mkdir -p -- "${images}"
avbroot ota extract --input "${ota}" --directory "${images}" --all
system_image="${images}/system.img"
[[ -f "${system_image}" && ! -L "${system_image}" ]] || {
  echo "Error: verified source OTA did not yield system.img." >&2
  exit 1
}

reference="${dest}/reference-vold"
debugfs -R "dump -p /system/bin/vold ${reference}" "${system_image}"
[[ -f "${reference}" && ! -L "${reference}" && -s "${reference}" ]] || {
  echo "Error: source system partition lacks /system/bin/vold." >&2
  exit 1
}

# debugfs retains bytes; chmod adjusts only local extracted metadata so the
# separate baseline builder can safely read an executable reference.
chmod 0755 -- "${reference}"
python3 - "${reference}" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, "src")
from lineage_vold import _elf_identity
raw = Path(sys.argv[1]).read_bytes()
identity = _elf_identity(raw)
if identity["machine"] != "AArch64" or identity["interpreter"] != "/system/bin/linker64":
    raise SystemExit("Error: source OTA vold ELF target is unexpected")
PY

printf 'source_ota_sha256=%s\n' "${expected_sha}"
sha256sum -- "${reference}"
