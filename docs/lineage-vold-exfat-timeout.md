# LineageOS vold exFAT fsck timeout

`ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]` (default off, LineageOS only) gives
exFAT filesystem checks 120 seconds instead of vold's shared 45-second limit.
Large exFAT volumes can exceed 45 seconds, and vold then kills `fsck.exfat`.

## What it changes

During OTA patching, `src/lineage_vold.py` edits **one AArch64 instruction** in
the `/system/bin/vold` that is already in the OTA: in `exfat::Check`, the
argument `mov w1, #45` to `ForkExecvpTimeout` becomes `mov w1, #120`.
`vfat::Check` and every other caller keep 45 seconds. Every other byte of vold
is what LineageOS shipped, so the linker, dependencies, ABI and SELinux label
cannot differ from the official binary. Nothing is rebuilt, downloaded or
pinned, and no Java or Android build tree is needed.

## How the call site is found

Android builds ship compressed mini-debuginfo in vold's `.gnu_debugdata`
section. The adapter reads the symbols there and requires:

- exactly one `exfat::Check` symbol and one `ForkExecvpTimeout` symbol, with
  the reviewed C++ signatures so the timeout is still the second argument;
- exactly one `mov w1, #45` inside `exfat::Check` that is followed, with no
  other branch in between, by a `bl` whose target is `ForkExecvpTimeout`;
- only decoded, independent argument setup between those instructions. No
  intervening instruction may read or overwrite `w1`/`x1`. Unknown instructions
  are refused until reviewed.

It does not use a fixed offset or hash, so it applies to any LineageOS build
whose vold has this structure, including newer nightlies. If anything differs
(a different instruction, a second call site, missing symbols, an already
patched binary, an upstream change) it **fails and no OTA is produced**.
Afterwards it checks that the output differs from the input in exactly one
instruction, from `mov w1,#45` to `mov w1,#120`, and that a 45-second call site
no longer exists.

At injection the adapter also requires one regular executable `/system/bin/vold`
with the `u:object_r:vold_exec:s0` label, and writes through the existing ExtFs
entry so mode, owner and SELinux metadata stay attached to the path.

## Selection fingerprint

The adapter's SHA-256 joins the module-selection fingerprint only when the
option is enabled, so existing fingerprints do not change.

## Build selection and PR qualification

The reusable build workflow accepts the boolean input
`lineage-vold-exfat-timeout`, which defaults to `false`. A direct
`workflow_call` caller can select it for LineageOS; other ROM families are
rejected before checkout. The normal release triggers and scheduled builds
keep this module disabled.

For the same-repository PR branch `feat/vold-exfat-timeout-binary-patch`,
`lineage24_qualification` selects both this patch and `microg-spoof-patch`.
It builds rootless and Magisk outputs together with disposable OTA signing
keys, read-only repository permissions, and `publish: false`. The existing
integration-branch selections and GrapheneOS qualification route are unchanged.
Successful completion of that job establishes combined OTA build and
cryptographic verification evidence; physical-device acceptance remains a
separate requirement.

## Offline use

```sh
python3 src/lineage_vold.py check /path/to/vold            # exactly one patchable site?
python3 src/lineage_vold.py patch /path/to/vold /path/to/vold.patched
```

## Status

Unit tests use a synthetic ELF and cover timeout-register overwrites, copying
the timeout into another argument, changed function signatures, and malformed
ELF headers. On 2026-11-10 the reviewed patcher was also checked against vold
extracted from the official LineageOS 24.0 pdx235 20261106 OTA. The download
matched the official API's SHA-256, and its publisher key and whole-file/payload
signatures were verified. The patch changes only two bytes of the instruction
at `0xbce74`; the full `vfat::Check` function is unchanged. The actual intervening
load/store instructions are covered by a regression test. See the
[binary qualification evidence](evidence/vold-binary-20261110.json) for exact
input/output hashes, the instruction sequence and authenticated tool versions.

The source OTA's full avbroot verification still reports its care-map mismatch;
that is recorded separately from signature authentication and was not waived
for production. That binary qualification did not build or flash a patched
OTA. The PR qualification job above checks the combined OTA build separately;
its result is recorded in the workflow run. **The longer timeout has not been
observed on a physical device.**

Before enabling this module for releases, validate the current patcher against
an authenticated OTA's vold, build and verify nonpublishing OTAs for each
supported root mode, and check physical-device boot, encrypted storage,
mounting, and an exFAT check that exceeds 45 seconds. A passing synthetic test
suite does not establish those outcomes.

This replaces the source-built approach (a pinned 1,243-project manifest, a
full `m vold` build and a byte-identical baseline proof). The separate
[original-vold baseline workflow](vold-baseline-ci.md) is no longer needed by
this module.
