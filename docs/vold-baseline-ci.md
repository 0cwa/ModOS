# First gate: reproduce LineageOS pdx235's original vold in CI

This is a **separate, nonpublishing, unmodified-source** workflow. It does
not use or import the exFAT timeout patch, the ModOS vold injection module,
OTA signing secrets, or any publishing workflow.

A patched vold must never be promoted on the basis of synthetic ELF fixtures
or mere compatibility. First prove that a clean build of the **unmodified**
source produces the identical executable found in the cryptographically
authenticated original LineageOS OTA.

## Exact pinned inputs

- Source: `lineage-24.0-20261016-nightly-pdx235-signed.zip`.
- Official source OTA SHA-256:
  `8af46661a1440e98018bb2abb155317d6d4ae54e051160f0786b75c1e6806c82`.
- Complete 1,243-project release manifest:
  `locks/lineage-pdx235-20261016-build-manifest.xml`.
- Source lock: `locks/lineage-vold-baseline-20261016.json`.
- Exact build: `lineage_pdx235-userdebug`, then `m vold`, without
  applying any patch or introducing a replacement binary.

These pins were recorded from signed ModOS source-verification runs
[#39632433468](https://github.com/0cwa/ModOS/actions/runs/39632433468)
and [#39678546023](https://github.com/0cwa/ModOS/actions/runs/39678546023).
The complete OTA is reauthenticated during each real baseline run:
downloaded bytes must match the digest, the embedded certificate's key must
match the independently pinned LineageOS official publisher key, the ZIP
certificate must be unique, and `avbroot ota verify` must succeed.

The original `system.img` is extracted read-only, then its
`/system/bin/vold` is recovered as the reference executable. A full,
clean, exact-revision LineageOS source checkout builds another unmodified
`vold` for the **same** pinned product. The resulting report includes
the reference and built SHA-256s, byte counts and first differing offset.

Only `byte_identical: true` is success. The workflow fails on a mismatch
and uploads a diagnostic JSON receipt, rather than bypassing the check.

## Runner provisioning (required before first real run)

The GitHub Actions workflow
[`vold-baseline.yml`](../.github/workflows/vold-baseline.yml) is
manual-only (`workflow_dispatch`) and runs on the repository's `main`
branch. A regular 14 GB GitHub-hosted Ubuntu runner is too small.

The repository owner must provision a **trusted, isolated Linux x64
self-hosted runner**, labeled `modos-lineage-large`, with a dedicated
scratch filesystem offering at least **250 GB free** and sufficient CPU/RAM
for an Android 17 `m vold` build. It needs LineageOS/Android build
prerequisites, the `repo` tool, Git, Python 3, curl, OpenSSL, unzip,
`debugfs`, and a verified `avbroot` binary supporting `ota verify` and
`ota extract`. Install and verify these dependencies through the host's
normal trusted administration process, not from an unreviewed workflow PR.

Configure two repository variables:

- `MODOS_LINEAGE_LARGE_SCRATCH` — absolute path to that real, dedicated
  scratch directory on the self-hosted runner.
- `MODOS_VOLD_BASELINE_RUNNER_READY=true` — set only when the owner has
  confirmed the runner is isolated, sufficiently provisioned and trusted.

The workflow's GitHub token has **read-only** repository permission and does
not receive signing keys or publishing secrets. Concurrent baseline jobs
are serialized. A failed preflight stops before allocating the source
workspace; all scratch content is deleted after uploading the small
comparison report. Do not register an untrusted shared self-hosted runner
for this purpose.

Once the baseline-only PR has passed ordinary CI and been merged,
open **Actions → Qualify original LineageOS pdx235 vold (no patch) →
Run workflow** on `main`. Any missing runner, unavailable pinned OTA,
manifest mismatch, build failure, or byte mismatch is a failure, not an
approval. The full source tree or reference image is never uploaded.

## After the first authentic success

Review the actual run URL and download its
`original-pdx235-vold-reproducibility-report` artifact. Record the exact
source, manifest and reference SHA-256, plus the CI commit and run ID.
Verify the report contains `byte_identical: true` with equal SHA-256s.
Independent assessment of the real reference extraction and Android
build remains essential. Do not infer these properties from synthetic tests.

**Only then** return to the separate draft
[timeout module PR #157](https://github.com/0cwa/ModOS/pull/157) to
integrate the changed two-line timeout, repeat a clean build, check ABI,
prepare a nonpublishing OTA, and qualify real device boot, storage and
exFAT behavior. Never enable scheduled pdx235 builds or force-publish merely
because the original-build proof passes. Newer LineageOS nightlies require
their own source/binary compatibility evidence.
