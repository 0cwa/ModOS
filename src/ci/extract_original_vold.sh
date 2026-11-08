#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Extract a read-only reference executable from the authenticated source OTA.
# Never touch OTA signing keys, alter a partition or create a patched image.
set -euo pipefail

if [[ "$#" != 2 ]]; then
  echo 'Usage: extract_original_vold.sh <downloaded-ota.zip> <empty-output-dir>' >&2
  exit 2
fi
source_zip="$1"
out_dir="$2"
[[ -f "$source_zip" && ! -L "$source_zip" ]] || {
  echo 'Error: official OTA ZIP is missing or unsafe' >&2
  exit 1
}
[[ ! -L "$out_dir" && ( ! -e "$out_dir" || ( -d "$out_dir" && -z "$(ls -A -- "$out_dir")" ) ) ]] || {
  echo 'Error: reference directory must be absent or empty' >&2
  exit 1
}
for tool in python3 sha256sum unzip openssl avbroot debugfs; do
  command -v "$tool" >/dev/null || {
    echo "Error: missing trusted baseline prerequisite: $tool" >&2
    exit 1
  }
done

expected="$(python3 - <<'PY'
import json
from pathlib import Path
import re
lock = json.loads(Path("locks/lineage-vold-baseline-20261016.json").read_text())
sha = lock["source_ota_sha256"]
assert re.fullmatch("[0-9a-f]{64}", sha)
print(sha)
PY
)"
digest="$(sha256sum -- "$source_zip" | awk '{print $1}')"
[[ "$digest" == "$expected" ]] || {
  echo 'Error: downloaded OTA does not match the reviewed immutable SHA-256' >&2
  exit 1
}
mkdir -p -- "$out_dir"
cert="$out_dir/source-otacert.pem"
unzip -p "$source_zip" META-INF/com/android/otacert > "$cert"
[[ -s "$cert" ]] || {
  echo 'Error: source ZIP has no embedded signer certificate' >&2
  exit 1
}

# This pinned certificate digest comes from LineageOS/update_verifier;
# verifying an arbitrary embedded certificate alone would NOT be sufficient.
source src/source_care_map_policy.sh
source_care_map_require_lineageos_pdx235_publisher lineageos pdx235 "$source_zip" "$cert"
avbroot ota verify --input "$source_zip" --cert-ota "$cert"

mkdir -p -- "$out_dir/partitions"
avbroot ota extract --input "$source_zip" --directory "$out_dir/partitions" --all
system_image="$out_dir/partitions/system.img"
[[ -f "$system_image" && ! -L "$system_image" ]] || {
  echo 'Error: authenticated OTA did not contain a regular system.img' >&2
  exit 1
}

# The reference bytes come from the original ext4 filesystem, not a rebuilt
# partition, APK, helper module, or overlay.
reference="$out_dir/original-vold"
debugfs -R "dump /system/bin/vold $reference" "$system_image"
[[ -s "$reference" && -f "$reference" && ! -L "$reference" ]] || {
  echo 'Error: cannot extract /system/bin/vold from the authentic OTA' >&2
  exit 1
}
python3 - "$reference" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, "src/ci")
from vold_baseline_compare import read_vold, sha256
data = read_vold(Path(sys.argv[1]))
print(f"official_vold_sha256={sha256(data)}")
PY
