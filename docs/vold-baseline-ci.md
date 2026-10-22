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

## Dependency-closure strategy (begin with 70 of 1,243 projects)

The original workflow fetched **all 1,243 pinned projects** before issuing
`m vold`. That is unnecessary for a single module, and it made each trial
expensive. Instead, the dependency-analysis recipe in
[`locks/lineage-vold-source-probe-20261016.json`](../locks/lineage-vold-source-probe-20261016.json)
selects **70 initial projects**. This is a conservative initial dependency
seed, NOT a complete or proven Soong build graph.

The source reasoning is grounded in three exact upstream revisions:

- [`system/vold/Android.bp`](https://github.com/LineageOS/android_system_vold/blob/11d8982f824d52e8122c7aa732207ba66b8f23c8/Android.bp):
  `vold` links `libvold`, and its defaults include Binder, cryptography,
  AVB, fs_mgr, native libc, and system service libraries. The module also
  directly requires `mke2fs`, `vold_prepare_subdirs`, and `fuseMedia.bpf`.
- [`device/sony/pdx235/device.mk`](https://github.com/LineageOS/android_device_sony_pdx235/blob/0559de69466e1e9af69b121985f592c4f4609283/device.mk):
  the pdx235 build inherits chipset definitions and a generated proprietary
  Sony vendor makefile.
- [`device/sony/sm6375-common/common.mk`](https://github.com/LineageOS/android_device_sony_sm6375-common/blob/d1d80af1ad9032cb092ff6d85db437709cc54e28/common.mk):
  the common product also includes proprietary `vendor/sony/sm6375-common`.
  Two other Sony vendor BoardConfig files are referenced.

**Important external dependency:** the exact official public manifest does
**not** contain `vendor/sony/pdx235` or
`vendor/sony/sm6375-common`. Do not invent proprietary blobs or disable
Soong missing-dependency checks. There is a narrower, source-grounded route
for their *makefiles*: the immutable
`device/sony/pdx235/extract-files.py --regenerate_makefiles` uses the
pinned `tools/extract-utils` and both Sony device trees to produce
`pdx235-vendor.mk`, `sm6375-common-vendor.mk`, `BoardConfigVendor.mk`,
and the relevant `Android.bp` declarations **without extracting any
proprietary binaries**. This is confirmed by the pinned extract-utils code:
the `--regenerate_makefiles` path skips source extraction. The probe's
[`prepare_vold_vendor_makefiles.py`](../src/ci/prepare_vold_vendor_makefiles.py)
runs that specific mode once after source sync and records a hash-only
`sony-generated-makefiles-report.json` receipt, reused on subsequent
iterations. It rejects unexpected binary blobs, symlinks and changes.

Generated public makefile declarations are **not** equivalent to the
original unpublished proprietary vendor files or complete product inputs.
If `m vold` requires actual missing blobs, the build still fails closed.
An independently verified original OTA may supply some blob bytes to the
reviewed extract-utils workflow, but neither runtime compatibility nor
publisher-identity follows merely from regenerating makefiles.

The on-disk dependency resolver
[`src/ci/vold_source_closure.py`](../src/ci/vold_source_closure.py)
verifies the complete original manifest and produces a pinned selection.
After a limited `repo sync <project paths>`, it scans the available
`Android.bp` definitions for a **partial** transitive module graph. The real
Soong compiler, rather than the static scan, remains authoritative for
conditional/generated modules. Any missing path reported in the compiler
diagnostics is mapped to its **exact existing** project in the official
manifest. Known module names have reviewed initial provider hints; unknown
providers and unpinned vendor paths stop for manual investigation.

Within a single CI run,
[`src/ci/probe_original_vold.sh`](../src/ci/probe_original_vold.sh)
automatically repeats up to **three** controlled passes. It caches already
synchronized projects in the same working tree, fetches only newly identified
pinned dependencies, and saves small diagnostic JSON documents showing
unknown modules and other blockers. It never enables
`SOONG_ALLOW_MISSING_DEPENDENCIES`, changes the build target, or applies
the exFAT timeout patch. Any unexpected source revision or modified
checkout fails immediately.

You can test the graph on an ordinary GitHub-hosted runner with **Actions →
Qualify original LineageOS pdx235 vold (no patch) → Run workflow**,
choosing `plan` (the default). This needs **no self-hosted runner or
repository variable** and produces a
`vold-source-provisional-plan` artifact. It does **not** compile or
qualify a binary.

## Runner provisioning for source compilation

For `probe` or `compare`, the workflow still requires a trusted,
isolated self-hosted Linux x64 runner labeled `modos-lineage-large`.
Provide a dedicated scratch filesystem with **at least 100 GB free as an
experimental starting allowance**, sufficient CPU/RAM, the Android 17
build prerequisites, and `repo`, `avbroot`, `debugfs`, `unzip`,
`curl`, `openssl`, `git`, and `python3`. One iteration may require
more storage. The 100 GB threshold is **not an empirically measured maximum
or proof** that the selected dependency graph is complete.

Set repository Actions variables:

- `MODOS_LINEAGE_LARGE_SCRATCH` — an absolute, real, isolated scratch
  directory on the trusted self-hosted runner.
- `MODOS_VOLD_BASELINE_RUNNER_READY=true` — only when that runner has been
  provisioned and independently checked.

Use `probe` to attempt the smaller pinned source checkout and compile
unmodified `m vold` **without downloading the official source OTA**.
A successfully compiled probe is **not** binary-equality evidence; its
workflow job explicitly labels it as unqualified. It is useful for
discovering missing modules, vendor inputs and toolchains cheaply.

Use `compare` for a real proof. It first reauthenticates the exact
publisher-signed official 2026-10-16 OTA, extracts the original
`/system/bin/vold`, and invokes the same bounded dependency solver.
**Only** a real `byte_identical: true` report with equal original and
built hashes qualifies this baseline. If the binaries differ, it emits a
diagnostic comparison receipt and fails.

From a checked-out repository, local inspection is also possible without
compiling or obtaining the proprietary vendor files:

```sh
python3 src/ci/vold_source_closure.py plan --output /tmp/vold-projects.json
# After a limited repo sync on a trusted build host:
python3 src/ci/vold_source_closure.py index \
  --source-root /path/to/pinned-source \
  --selection /tmp/vold-projects.json \
  --output /tmp/vold-blueprint-graph.json
# After an unsuccessful m vold build:
python3 src/ci/vold_source_closure.py diagnose \
  --selection /tmp/vold-projects.json \
  --log /path/to/build-failure.log \
  --output /tmp/vold-diagnosis.json \
  --next-selection /tmp/vold-projects-expanded.json
```

All selection changes must remain part of the exact original manifest.
The build helper supports `sync --projects-file ... --resume` to fetch
additional pinned projects without repeating an entire sync.

The workflow uses a read-only GitHub token, never receives release or
signing secrets, uploads diagnostics/comparison receipts only, and clears
the large checkout and original OTA after completion. Do not use an
untrusted shared runner.

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
