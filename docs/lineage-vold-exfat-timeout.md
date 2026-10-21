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

## Build the artifact

The source sync and Android build need a dedicated Linux build host with the
LineageOS build prerequisites and ample disk space. Sync is an explicit step;
the module never starts a large source download during an OTA build. Use a new
or empty source directory:

```sh
python3 src/ci/build_lineage_vold.py sync \
  --source-root /path/to/lineage-pdx235-20261016 \
  --jobs 4
```

The sync command initializes Repo from the manifest repository commit pinned in
the official OTA manifest, installs that complete manifest, synchronizes the
projects, and checks every project revision. Build only `vold` and write its
binary plus provenance to a new or empty artifact directory:

```sh
python3 src/ci/build_lineage_vold.py build \
  --source-root /path/to/lineage-pdx235-20261016 \
  --output-dir /path/to/lineage-vold-artifact \
  --jobs 4
```

The build command refuses a dirty or mismatched vold checkout. It applies the
locked patch, runs the `lineage_pdx235-userdebug` lunch target and `m vold`,
checks the AArch64 ELF and Android linker, then restores the vold source tree.
The output directory contains `vold` and `provenance.json`; the adapter checks
the source manifest, source revisions, patch digest, binary digest, ABI, and
dynamic dependencies before injection.

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

**This module is not qualified for public OTA use.** CI exercises synthetic
ELF and filesystem fixtures; a full 1,243-project source sync, Android `m vold`
build, authenticated OTA integration and real-device boot/exFAT checks remain
necessary. Do not set the scheduled default to true or force-publish it yet.

1. On a suitably provisioned, isolated Linux build host, run the pinned
   `sync` and `build` commands above. Retain the generated `vold` and
   `provenance.json` without editing either.
2. Validate them against the authentic
   `lineage-24.0-20261016-nightly-pdx235-signed` source and run a
   **nonpublishing** rootless/Magisk OTA qualification. Confirm both images
   boot, vold starts, encrypted storage mounts, and exFAT fsck behaves as
   expected; preserve test results. This is not satisfied by unit-test
   fixtures.
3. Stage the exact `vold` and `provenance.json` as two independent assets
   in a **separate non-OTA release** in `0cwa/ModOS`, using a tag beginning
   with `vold-pdx235-20261016-`. Record both
   `sha256sum vold provenance.json` values and verify the staging upload.
4. Only after binary, OTA and device qualification, commit the independent
   digest approval in `approved_binary_sha256` and the
   `artifact_distribution.release_tag` and
   `artifact_distribution.provenance_sha256` in the source lock.
   `src/ci/fetch_lineage_vold_artifact.py` downloads only this pair and
   independently checks its bytes; the scheduled preflight and actual build
   both verify it again.
5. Enable `ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]` only on the LineageOS
   pdx235 schedule, run full CI, then intentionally force-publish and confirm
   the resulting rootless/Magisk outputs and update delivery.

**Nightly rotation:** This lock and artifact intentionally identify *only*
the 2026-10-16 nightly. A newer official LineageOS OTA will fail closed
rather than combining an outdated vold binary with a different source. To
keep future scheduled builds enabled, obtain, qualify and review a fresh
source-locked artifact for each new published nightly before rolling over
the schedule. Never silently reuse the previous binary.

GrapheneOS remains excluded by explicit workflow and schedule guards.
