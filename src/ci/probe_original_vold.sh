#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Same-workspace, immutable-revision probing: sync only selected projects,
# reuse downloaded Git objects, map errors to the pinned manifest, expand.
# Never apply the timeout patch or publish an OTA.
set -euo pipefail

: "${VOLD_WORK:?isolated scratch workspace is required}"
: "${VOLD_PROBE_STAGE:?probe or compare stage is required}"

[[ "${VOLD_PROBE_STAGE}" == probe || "${VOLD_PROBE_STAGE}" == compare ]] || {
  echo 'Error: unsupported unmodified vold dependency stage' >&2
  exit 1
}
[[ "${VOLD_WORK}" == /* && -d "${VOLD_WORK}" && ! -L "${VOLD_WORK}" ]] || {
  echo 'Error: isolated scratch directory is missing or unsafe' >&2
  exit 1
}
[[ "${VOLD_PROBE_STAGE}" != compare || ( "${VOLD_SOURCE_REF:-}" == /* && -s "${VOLD_SOURCE_REF}" && ! -L "${VOLD_SOURCE_REF}" ) ]] || {
  echo 'Error: authenticated original vold reference is required' >&2
  exit 1
}
jobs="${VOLD_PROBE_JOBS:-4}"
[[ "${jobs}" =~ ^[1-9][0-9]?$ ]] && (( jobs <= 16 )) || {
  echo 'Error: probe concurrency must be between 1 and 16' >&2
  exit 1
}

selection="${VOLD_WORK}/source-selection.json"
python3 src/ci/vold_source_closure.py plan --output "${selection}"
# Check the three small pinned public vendor-input projects BEFORE fetching
# toolchains. At these revisions --regenerate_makefiles still reads absent
# proprietary ELF files. The preflight records that blocker without generating
# misleading partial declarations or downloading the rest of the source seed.
python3 src/ci/build_original_vold.py sync-vendor-sources \
  --source-root "${VOLD_WORK}/source" --jobs "${jobs}"
python3 src/ci/prepare_vold_vendor_makefiles.py \
  --source-root "${VOLD_WORK}/source" \
  --report "${VOLD_WORK}/sony-vendor-input-report.json"

python3 src/ci/build_original_vold.py sync --resume \
  --source-root "${VOLD_WORK}/source" --projects-file "${selection}" --jobs "${jobs}"

# Batch reviewed provider hints from the checked-out Blueprint graph before
# compiling. Unknown or conditional modules remain diagnostics for real Soong;
# they are never mapped to guessed repositories. Stop after three scans.
for scan in 1 2 3; do
  next="${VOLD_WORK}/selection-static-next.json"
  rm -f -- "${next}"
  python3 src/ci/vold_source_closure.py index \
    --source-root "${VOLD_WORK}/source" --selection "${selection}" \
    --output "${VOLD_WORK}/source-index-static-${scan}.json" \
    --next-selection "${next}"
  if [[ ! -f "${next}" ]]; then
    break
  fi
  mv -- "${next}" "${selection}"
  python3 src/ci/build_original_vold.py sync --resume \
    --source-root "${VOLD_WORK}/source" --projects-file "${selection}" --jobs "${jobs}"
done

# Most missing dependencies are reported during Soong setup before the
# expensive target compile. Resolve their exact pinned project paths once
# and reuse the same checkout and Android out directory on each iteration.
# Unknown module providers must stop for review; never turn on missing
# dependency flags or attempt an unverified project URL.
for iteration in 1 2 3; do
  log="${VOLD_WORK}/vold-build-pass-${iteration}.log"
  if [[ "${VOLD_PROBE_STAGE}" == probe ]]; then
    build_cmd=(python3 src/ci/build_original_vold.py probe
      --source-root "${VOLD_WORK}/source" --projects-file "${selection}" --jobs "${jobs}")
  else
    build_cmd=(python3 src/ci/build_original_vold.py compare
      --source-root "${VOLD_WORK}/source" --projects-file "${selection}"
      --reference-vold "${VOLD_SOURCE_REF}"
      --output-dir "${VOLD_WORK}/baseline-${iteration}" --jobs "${jobs}")
  fi

  if "${build_cmd[@]}" >"${log}" 2>&1; then
    tail -n 15 -- "${log}"
    if [[ "${VOLD_PROBE_STAGE}" == probe ]]; then
      echo '::notice::Unmodified vold compiled, but no authenticated binary equality has been established.'
      echo "Compile-only probe completed; next stage MUST use authenticated compare." >"${VOLD_WORK}/probe-only.txt"
    else
      echo '::notice::Unmodified vold is byte-identical to the authenticated reference.'
    fi
    exit 0
  fi

  echo "::warning::Pinned source probe pass ${iteration} failed. Last diagnostic lines:"
  tail -n 55 -- "${log}"
  next="${VOLD_WORK}/selection-next.json"
  rm -f -- "${next}"
  python3 src/ci/vold_source_closure.py diagnose \
    --selection "${selection}" --log "${log}" \
    --output "${VOLD_WORK}/dependency-diagnosis-${iteration}.json" \
    --next-selection "${next}"

  if [[ ! -f "${next}" || "${iteration}" == 3 ]]; then
    echo '::error::No automatically identifiable missing pinned projects remain, or expansion budget exhausted.'
    echo '::error::Review diagnostic JSON for unknown Soong modules, missing vendor inputs or real build errors.'
    exit 1
  fi
  mv -- "${next}" "${selection}"
  echo "::notice::Expanding the same pinned checkout on pass $((iteration + 1))."
  python3 src/ci/build_original_vold.py sync --resume \
    --source-root "${VOLD_WORK}/source" \
    --projects-file "${selection}" --jobs "${jobs}"
done

echo '::error::Source probing exceeded the bounded three-pass limit' >&2
exit 1
