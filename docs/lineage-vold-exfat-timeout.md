# Source-built LineageOS vold for pdx235

The `lineage-vold-exfat-timeout` adapter replaces `/system/bin/vold` with a
binary built from the exact source snapshot used by the LineageOS
`lineage-24.0-20261016-nightly-pdx235-signed` OTA. Its patch gives exFAT fsck a
120-second limit while leaving the shared 45-second timeout unchanged.

The source contract is in [`locks/lineage-vold-exfat-timeout.json`](../locks/lineage-vold-exfat-timeout.json).
The complete official 1,243-project manifest is checked in and hash-pinned at
[`locks/lineage-pdx235-20261016-build-manifest.xml`](../locks/lineage-pdx235-20261016-build-manifest.xml).
The lock also pins the vold source revision, pdx235 device-tree revision,
manifest repository revision, target lunch product, timeout patch digest, and
installed binary path.

## First gate: reproduce the original unmodified vold

**Do not patch, inject, or publish anything until an unmodified build of
`/system/bin/vold` exactly matches the executable inside the authenticated
official LineageOS OTA, byte for byte.** Matching the ELF ABI, dependencies or
source revision is not enough.

The `lineage_vold_exact_baseline` job in [CI](../.github/workflows/ci.yml)
implements this gate. It is nonpublishing, read-only on the GitHub repository,
and restricted to this owned review PR. Because syncing the full 1,243-project
LineageOS tree needs substantial space, ordinary GitHub-hosted CI cannot run
this job. Provision an isolated **self-hosted Linux x64** runner with:

- the runner label `modos-lineage-large`, Android/LineageOS build prerequisites
  and the `repo`, `avbroot`, `debugfs`, `unzip`, `curl`, `openssl`, `git` and
  `python3` executables;
- at least **220 GB free** on a dedicated scratch volume; set repository
  variable `MODOS_LINEAGE_LARGE_SCRATCH` to that absolute directory;
- repository variable `MODOS_VOLD_BASELINE_RUNNER_READY=true` when that
  trusted build runner is available. Until enabled, the heavy PR job is
  intentionally skipped.

The baseline job selects the exact
`lineage-24.0-20261016-nightly-pdx235-signed.zip` from the **official
LineageOS builds API**, refusing a substituted later nightly. It checks the
API SHA-256 against downloaded bytes, verifies the embedded certificate
against the independently pinned official LineageOS publisher public key,
and performs cryptographic whole-OTA verification with `avbroot ota verify`.
It then extracts `system.img` and the original `/system/bin/vold` as
**reference bytes only**. No keys for signing or publishing are accessible.

Next, with a fresh empty source directory, the job executes:

```sh
python3 src/ci/build_lineage_vold.py sync \
  --source-root /large/scratch/lineage-pdx235-20261016 --jobs 4
python3 src/ci/build_lineage_vold.py baseline \
  --source-root /large/scratch/lineage-pdx235-20261016 \
  --reference-vold /large/scratch/reference/reference-vold \
  --source-ota-sha256 "$OFFICIAL_SOURCE_SHA256" \
  --output-dir /large/scratch/unmodified-baseline --jobs 4
```

`baseline` validates all manifest revisions and the clean working trees,
runs the pinned `lineage_pdx235-userdebug` lunch target and `m vold` **without
applying the timeout patch**, and compares the resulting installed executable
against the source OTA reference **byte for byte**. It produces
`baseline-report.json` containing the source OTA identity, full manifest
digest, reference/build SHA-256s, ELF identities, sizes, exact equality flag
and first differing byte offset. A mismatch fails the job, and **no patched
build is allowed**. CI retains the report only, not the large source checkout,
vold binary, decrypted partitions or an OTA.

If the official API no longer lists the 2026-10-16 archive or the full source
cannot be compiled on the runner, this gate correctly remains blocked.
Investigate and independently pin the archived source rather than silently
substituting an unrelated image. Even a successful fixture/unit test is NOT
a successful real baseline reproduction.

## Second gate: compile the modified vold only after success

After inspecting an actual **`byte_identical: true`** receipt from the
full baseline CI run, a separately reviewed patched-build job may be developed.
The baseline CI job intentionally deletes its source checkout, reference and
binary after uploading the receipt. Therefore, the later patched-build job
must re-authenticate the exact source OTA and reproduce its unmodified vold
again, without trusting just the uploaded JSON. Only then can it build the
patched artifact with these mandatory proof inputs:

```sh
python3 src/ci/build_lineage_vold.py build \
  --source-root /large/scratch/lineage-pdx235-20261016 \
  --output-dir /large/scratch/patched-vold \
  --baseline-report /large/scratch/unmodified-baseline/baseline-report.json \
  --baseline-binary /large/scratch/unmodified-baseline/baseline-vold \
  --reference-vold /large/scratch/reference/reference-vold \
  --jobs 4
```

`build` independently rechecks the receipt against both supplied real
binary files, the pinned manifest and the SHA-256 digests **before** it applies
the narrow exFAT-only timeout patch. It then restores the pristine vold
checkout. A patched artifact directory contains `vold` and
`provenance.json`, including the prior exact baseline's reference digest.
The patch stage is NOT yet wired into the baseline CI job; it must only be
activated after proving the baseline and separately reviewing the result.

## Injection contract

When registered by ModOS, the adapter is opt-in and requires the
`lineage-vold-exfat-timeout` module selection. It supports only LineageOS
`pdx235` and the pinned OTA build. Source-built vold requires both the
system and product partitions for validation: the device identity is read from
`/system/build.prop`, and the `ro.lineage.*` release properties are read
from `/product/build.prop` (the pinned LineageOS
`vendor/lineage/config/version.mk` installs them via
`PRODUCT_PRODUCT_PROPERTIES`). The ModOS setup must set
`PIXENEOS_VOLD_ARTIFACT_DIR` to the artifact directory and
`PIXENEOS_VOLD_BASE_BUILD_ID` to
`lineage-24.0-20261016-nightly-pdx235-signed`.

The module is default-off for both scheduled and manual release workflows.
Their selectors are wired to a staged-artifact acquisition step, but this
step intentionally fails before any download until the reviewed binary and
provenance SHA-256 digests, plus the staging release tag, are pinned in the
source lock. Local-unpublished builds can consume a locally built artifact
without this final publication approval. Build the artifact first, then set
`PIXENEOS_VOLD_ARTIFACT_DIR` to its output directory and
`PIXENEOS_VOLD_BASE_BUILD_ID` to the exact id above in the build process
environment. Selection fails before patching if either input is absent or if
the artifact provenance does not match the lock.

At injection, the adapter checks the partition-specific properties and the
exact `ro.lineage.version` from the pinned source snapshot. It requires
one root-owned executable `/system/bin/vold`, the actual `vold_exec` SELinux
xattr and expected context mapping, and the same AArch64 interpreter and
dependencies as the original OTA binary. It overwrites the existing ExtFs
entry, verifies all original filesystem metadata, and re-hashes the written
binary. A failed check stops the build before a patched OTA is emitted.

This module is kept separate from scheduled selections and the SELinux force
release. The current workspace does not have Repo installed and has only about
41 GB free, so the full source sync and Android build have not been run here.

## Production promotion checklist (not yet complete)

**This module is not qualified for public OTA use.** The lightweight tests
use synthetic ELF and filesystem fixtures; they do not establish an actual
byte-identical result for the pinned LineageOS OTA.

1. Provision the dedicated CI runner and complete the **unmodified,
   byte-exact baseline** proof described above. Review a real successful
   `baseline-report.json`, not fixture output. If any byte differs, investigate
   compiler/toolchain/build flags and repeat the proof without weakening it.
2. Only after the unmodified baseline passes, build the two-line timeout
   patch with its mandatory evidence inputs. Review the resulting artifact
   provenance and binary dependencies against the verified reference.
3. Produce a **nonpublishing** pdx235 OTA, inspect AVB and OTA checks, and
   test physical-device boot, file-based encryption, storage mounting and
   exFAT fsck. Retain rollback/recovery options. A passing CI compile alone
   does not establish device safety.
4. Stage independently reviewed `vold` and `provenance.json` as a dedicated
   non-OTA GitHub release, and record their exact SHA-256 digests and staging
   tag in the source lock. The source lock's approval fields remain **empty**
   until steps 1–3 pass; production injection fails closed.
5. Only after those gates, enable the LineageOS pdx235 schedule selector,
   run CI, merge and intentionally force-publish. Verify the full rootless
   and Magisk OTA pair plus Custota update delivery.

**Nightly rotation:** This lock and the original-vold baseline identify
*only* the 2026-10-16 nightly. A newer official source OTA requires a fresh
complete baseline reproduction and patch qualification before it can be
included in rolling scheduled builds. Do not reuse the older vold binary.

GrapheneOS remains excluded by explicit workflow and schedule guards.
