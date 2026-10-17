#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Hermetic negative/positive proof of source+code-bound existing OTA skips.
set -euo pipefail

project_root="$(pwd -P)"
scratch="$(mktemp -d)"
trap 'rm -rf -- "${scratch}"' EXIT
mkdir -p "${scratch}/bin" "${scratch}/repo" "${scratch}/metadata"
git -C "${scratch}/repo" init -q
git -C "${scratch}/repo" -c user.name=Fixture -c user.email=fixture@example.invalid \
  -c core.hooksPath=/dev/null commit --allow-empty -qm fixture
git -C "${scratch}/repo" tag 20261016
commit="$(git -C "${scratch}/repo" rev-parse HEAD)"
source_sha="$(printf 'b%.0s' {1..64})"
fingerprint="$(printf 'a%.0s' {1..64})"
target='lineage-24.0-20261016-nightly-pdx235-signed'
artifact="pdx235-20261016-rootless-${fingerprint}-${commit:0:7}.zip"
metadata="${scratch}/metadata/${artifact}.selection.json"
python3 - "${metadata}" "${artifact}" "${source_sha}" "${target}" "${commit}" "${fingerprint}" <<'PY'
import json, pathlib, sys
path, artifact, source_sha, target, commit, fingerprint = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    "artifact_name": artifact, "device": "pdx235",
    "grapheneos_version": "20261016", "rom_family": "lineageos",
    "output_scope": "published", "module_selection_fingerprint": fingerprint,
    "source_ota_sha256": source_sha, "source_ota_target": target,
    "modos_commit": commit, "schema_version": 2
}))
PY
python3 - "${scratch}/response.json" "${artifact}" <<'PY'
import json, pathlib, sys
path, artifact = sys.argv[1:]
base = "https://github.com/0cwa/ModOS/releases/download/20261016/"
data = {"assets": [{"name": name, "browser_download_url": base + name}
                   for name in (artifact, artifact + ".csig",
                                artifact + ".selection.json")]}
pathlib.Path(path).write_text(json.dumps(data))
PY
cat >"${scratch}/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
output=''
url=''
while (($#)); do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    --retry|--retry-delay) shift 2 ;;
    --silent|--show-error|--location|--fail) shift ;;
    *) url="$1"; shift ;;
  esac
done
if [[ "${url}" == */releases/download/* ]]; then
  cp "${FIXTURE_METADATA_DIR}/${url##*/}" "${output}"
else
  cp "${FIXTURE_RESPONSE}" "${output}"
fi
SH
chmod +x "${scratch}/bin/curl"

check() {
  local label="$1" expected="$2"
  shift 2
  local out="${scratch}/${label}.out"
  : >"${out}"
  (
    cd "${scratch}/repo"
    env PATH="${scratch}/bin:${PATH}" \
      FIXTURE_METADATA_DIR="${scratch}/metadata" FIXTURE_RESPONSE="${scratch}/response.json" \
      DEVICE_NAME=pdx235 GRAPHENEOS_VERSION=20261016 REPOSITORY=0cwa/ModOS \
      ROM_FAMILY=lineageos OUTPUT_SCOPE=published ROOT=false DEBUG=false \
      EXPECTED_VARIANT="${fingerprint}" SOURCE_OTA_SHA256="${source_sha}" \
      SOURCE_OTA_TARGET="${target}" BUILD_COMMIT="${commit}" FORCE_UPDATE=false \
      GITHUB_OUTPUT="${out}" "$@" "${project_root}/src/ci/check_existing_build.sh" >/dev/null
  )
  grep -Fxq "should_build=${expected}" "${out}" ||
    { echo "${label}: expected should_build=${expected}" >&2; exit 1; }
}

check complete_skip false
check changed_source_digest true SOURCE_OTA_SHA256="$(printf 'c%.0s' {1..64})"
check changed_source_target true SOURCE_OTA_TARGET=lineage-24.0-20261017-nightly-pdx235-signed
check changed_build_commit true BUILD_COMMIT="$(printf 'd%.0s' {1..40})"

# Legacy metadata does not carry new source/revision binding; never skip it.
cp "${metadata}" "${metadata}.full"
python3 - "${metadata}" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
j = json.loads(p.read_text())
for key in ("source_ota_sha256", "source_ota_target", "modos_commit"):
    j.pop(key)
p.write_text(json.dumps(j))
PY
check legacy_metadata true
mv "${metadata}.full" "${metadata}"

rm "${metadata}"
# Incomplete asset triplet must fail closed; a missing metadata download
# cannot authorize a false skip.
if (check missing_metadata true) >/dev/null 2>&1; then
  echo 'missing downloaded selection metadata was unexpectedly accepted' >&2
  exit 1
fi

# The scheduled workflow must keep this decision independent of
# repository secrets and skip the reusable job only for a verified false.
python3 - <<'PY'
from pathlib import Path
flow = Path('.github/workflows/release-lineage.yml').read_text()
assert 'should_build: ${{ steps.existing.outputs.should_build }}' in flow
assert 'bash src/ci/check_scheduled_pdx235_pair.sh' in flow
assert "needs.schedule_config.outputs.should_build == 'true'" in flow
preflight = Path('src/ci/check_scheduled_pdx235_pair.sh').read_text()
assert 'source src/util_functions.sh' in preflight
assert 'enforce_pdx235_pilot_source' in preflight
assert 'bash src/ci/check_existing_pair.sh' in preflight
assert 'GITHUB_REPOSITORY' in preflight
assert 'SOURCE_OTA_SHA256' in preflight
assert 'BUILD_COMMIT' in preflight
assert 'ROOT_MODE:-' in preflight
build = Path('.github/workflows/build-rom.yml').read_text()
for token in ('source_ota_sha256', 'source_ota_target', 'modos_commit'):
    assert f'"{token}"' in build
PY

echo 'pdx235 source/revision-bound preflight tests passed'
