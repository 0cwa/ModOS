#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

repo_root="$(pwd -P)"
prepare_script="${repo_root}/src/ci/prepare_pdx235_custota_metadata.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "${scratch}"' EXIT

die() { echo "FAIL: $*" >&2; exit 1; }
run_case() {
  local name="$1"
  mkdir -p -- "${scratch}/${name}"
  cd -- "${scratch}/${name}"
}
reject_without_destination() {
  if bash "${prepare_script}" >/dev/null 2>&1; then
    die "${1} was accepted"
  fi
  [[ ! -e pdx235.json && ! -L pdx235.json ]] ||
    die "${1} created the canonical metadata file"
}

# The generated pre-device filename is an input filename only. Preserve every
# byte of the generated update-info body under the canonical device filename.
run_case exact-copy
printf '{"version":2,"full":{"location_ota":"https://example.invalid/build.zip","location_csig":"https://example.invalid/build.zip.csig"}}\n' >XQ-DC72.json
bash "${prepare_script}"
cmp -s XQ-DC72.json pdx235.json || die 'canonical metadata is not byte-identical'
[[ -f pdx235.json && ! -L pdx235.json && -s pdx235.json ]] ||
  die 'canonical metadata is not a nonempty regular file'
bash "${prepare_script}" || die 'identical existing canonical metadata was not idempotent'

run_case missing-source
reject_without_destination 'missing source metadata'

run_case empty-source
: >XQ-DC72.json
reject_without_destination 'empty source metadata'

run_case symlink-source
printf 'target\n' >real.json
ln -s real.json XQ-DC72.json
reject_without_destination 'symlink source metadata'

run_case directory-source
mkdir XQ-DC72.json
reject_without_destination 'directory source metadata'

run_case conflicting-destination
printf 'generated\n' >XQ-DC72.json
printf 'stale\n' >pdx235.json
if bash "${prepare_script}" >/dev/null 2>&1; then
  die 'conflicting canonical metadata was overwritten'
fi
grep -Fxq 'stale' pdx235.json || die 'conflicting canonical metadata changed after rejection'

run_case symlink-destination
printf 'generated\n' >XQ-DC72.json
ln -s missing.json pdx235.json
if bash "${prepare_script}" >/dev/null 2>&1; then
  die 'symlink canonical metadata was accepted'
fi
[[ -L pdx235.json ]] || die 'unsafe canonical symlink was modified'

run_case destination-race
printf 'generated\n' >XQ-DC72.json
mkdir fake-bin
cat >fake-bin/mv <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$#" -eq 4 && "$1" == -n && "$2" == -- ]] || exit 2
printf 'raced\n' >"$4"
SH
chmod +x fake-bin/mv
if PATH="${PWD}/fake-bin:${PATH}" bash "${prepare_script}" >/dev/null 2>&1; then
  die 'destination race was accepted'
fi
grep -Fxq 'raced' pdx235.json || die 'simulated raced destination was not preserved'
if compgen -G '.pdx235.json.tmp.*' >/dev/null; then
  die 'temporary metadata file remained after destination-race rejection'
fi

workflow="${repo_root}/.github/workflows/build-rom.yml"
python3 - "${workflow}" <<'PY'
import pathlib
import sys

workflow = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
prepare = "      - name: Prepare canonical pdx235 Custota metadata\n"
patch = "      - name: Patch OTA\n"
publish = "      - name: Publish GitHub release\n"
if workflow.count(prepare) != 1:
    raise SystemExit("canonical metadata step must appear exactly once")
step_start = workflow.index(prepare)
if not workflow.index(patch) < step_start < workflow.index(publish):
    raise SystemExit("canonical metadata must be prepared after patching and before release publication")
condition_start = workflow.index("        if:", step_start)
condition = workflow[condition_start:workflow.index("\n", condition_start)]
for required in (
    "inputs.publish",
    "inputs.release-type != 'build-only'",
    "inputs.rom-family == 'lineageos'",
    "inputs.device-id == 'pdx235'",
    "env.ROOT_MODE_RESOLVED == 'rootless'",
    "env.ROOT_MODE_RESOLVED == 'magisk'",
):
    if required not in condition:
        raise SystemExit(f"canonical metadata step is missing scope condition: {required}")
PY

echo 'pdx235 Custota metadata preparation tests passed'
