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
`pdx235` and the pinned OTA build. The ModOS setup must set
`PIXENEOS_VOLD_ARTIFACT_DIR` to the artifact directory and
`PIXENEOS_VOLD_BASE_BUILD_ID` to
`lineage-24.0-20261016-nightly-pdx235-signed`.

The module is opt-in and is not enabled in the scheduled build or release
workflow. Build the artifact first, then set
`PIXENEOS_VOLD_ARTIFACT_DIR` to its output directory and
`PIXENEOS_VOLD_BASE_BUILD_ID` to the exact id above in the build process
environment. Selection fails before patching if either input is absent or if
the artifact provenance does not match the lock.

At injection, the adapter checks `ro.product.device`, `ro.lineage.build.version`,
and the exact `ro.lineage.version` from the pinned source snapshot. It requires
one regular executable `/system/bin/vold`, the `vold_exec` SELinux label, and
the same AArch64 interpreter and dependencies as the original OTA binary. It
writes bytes through the existing ExtFs file entry so its image mode, owner,
and SELinux metadata stay attached to the existing path. A failed check stops
the build before a patched OTA is emitted.

This module is kept separate from scheduled selections and the SELinux force
release. The current workspace does not have Repo installed and has only about
41 GB free, so the full source sync and Android build have not been run here.
