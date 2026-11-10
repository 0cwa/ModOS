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

- exactly one `exfat::Check` symbol and one `ForkExecvpTimeout` symbol;
- exactly one `mov w1, #45` inside `exfat::Check` that is followed, with no
  other branch in between, by a `bl` whose target is `ForkExecvpTimeout`.

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

## Offline use

```sh
python3 src/lineage_vold.py check /path/to/vold            # exactly one patchable site?
python3 src/lineage_vold.py patch /path/to/vold /path/to/vold.patched
```

## Status

Unit tests use a synthetic ELF. The patcher was also run against the vold
installed on a LineageOS 24.0 pdx235 (`24.0-20261106-NIGHTLY`): it found one
call site and changed one instruction. **No OTA containing the patch has been
built or flashed, and the longer timeout has not been observed on a device.**

This replaces the source-built approach (a pinned 1,243-project manifest, a
full `m vold` build and a byte-identical baseline proof). The separate
[original-vold baseline workflow](vold-baseline-ci.md) is no longer needed by
this module.
