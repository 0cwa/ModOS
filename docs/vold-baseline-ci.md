# Build and qualify LineageOS pdx235's original vold locally

This is a **separate, nonpublishing, unmodified-source** workflow. It does
not use or import the exFAT timeout patch, the ModOS vold injection module,
OTA signing secrets, or any publishing workflow.

A patched vold must never be promoted on the basis of synthetic ELF fixtures
or mere compatibility. First prove that a clean build of the **unmodified**
source produces the identical executable found in the cryptographically
authenticated original LineageOS OTA.

**A GitHub runner is optional.** The local entry point acquires the pinned
source and vendor inputs, keeps them between invocations, builds only `m vold`,
and performs the same authenticated byte comparison. The two public vendor
mirrors have been downloaded and verified with the real pinned generator;
an Android compilation and byte-identical result are still outstanding.

## Local quick start

Use Linux x86_64 with an SSD and a work directory without whitespace. Ubuntu
24.04 is the documented setup. On another operating system, use a Linux x86_64
build machine or VM. The local command needs no GitHub token, repository
variables, Actions runner registration, phone, `adb`, or signing keys.

From a checkout of this repository, install the host packages once:

```sh
sudo apt-get update
sudo apt-get install -y \
  git git-lfs gnupg openssh-client openssl curl python3 build-essential flex bison \
  zip unzip rsync bc zlib1g-dev libc6-dev-i386 lib32z1-dev \
  libx11-dev x11proto-core-dev libgl1-mesa-dev libxml2-utils xsltproc \
  fontconfig libssl-dev libelf-dev lz4 erofs-utils e2fsprogs
```

This follows the [AOSP host setup](https://source.android.com/docs/setup/start/requirements)
with the extraction utilities used here. The pinned Android projects provide
their matching compiler and JDK; do not replace those with a random host JDK.

Choose one persistent directory, install the small host tools there, then build:

```sh
export VOLD_LOCAL_WORK="$HOME/build/modos-vold"
python3 src/ci/setup_vold_local_tools.py --work-dir "$VOLD_LOCAL_WORK"
source "$VOLD_LOCAL_WORK/host-tools/env.sh"

# Check the host before a long build.
python3 src/ci/vold_local.py doctor --for compare --work-dir "$VOLD_LOCAL_WORK"

# One command: vendor inputs, source sync, compile, authenticate, compare.
python3 src/ci/vold_local.py compare --work-dir "$VOLD_LOCAL_WORK" --jobs 4
```

The setup command installs a SHA-256-pinned official Repo launcher and the
existing [publisher-authenticated avbroot tool](executable-tool-trust.md).
Its `avbroot` wrapper uses the existing sealed executable runner. Repo retains
its normal signed bootstrap; pinning its launcher does not claim that every
future Repo implementation version is pinned. Nothing is installed globally.
Run `source .../host-tools/env.sh` in each new terminal. If the ModOS checkout
moves, rerun setup to refresh the wrapper's absolute path. `--repo-only` skips
avbroot installation until comparison is needed.

`compare` first performs a compile-only probe. Only after that succeeds does it
download the exact locked OTA, verify its digest and publisher signature,
extract a fresh reference, and repeat the incremental `m vold` comparison.
The source checkout and intermediate output are reused. An existing OTA can
be supplied with `--ota-file /absolute/path/lineage-24.0-20261016-nightly-pdx235-signed.zip`;
its exact filename, SHA-256 and signature are still checked.

### Smaller stages and resuming

All stages accept the same `--work-dir` and `--jobs` options. Repeating a command
resumes the same pinned checkout. Each invocation writes a new log directory.

| Command | Work performed |
| --- | --- |
| `plan` | Write the provisional 84-project selection, without downloads; preserve any expanded active selection. |
| `doctor --for vendor` | Check only the host tools needed for vendor preparation. |
| `vendor` | Sync the three small public prerequisite projects, acquire/verify both pinned vendor trees, and regenerate their eight declarations. Stop before toolchains. |
| `prepare` | Run vendor preparation, sync the selected source/toolchain projects, and perform bounded static dependency scans. |
| `probe` | Prepare/resume sources and compile the unmodified `vold`, without downloading an OTA. |
| `compare` | Compile first, then authenticate the exact OTA and require byte-for-byte equality. |

For the inexpensive first check:

```sh
python3 src/ci/vold_local.py vendor --work-dir "$VOLD_LOCAL_WORK" --jobs 2
```

The verified vendor worktree content is 684,866,192 bytes (about 685 MB), plus
Git objects and metadata. This is measured vendor content, not a full build
space estimate. The 84-project source seed is still provisional. `doctor`
reports available disk and RAM; 100 GB free is an initial planning allowance,
not a proven minimum or maximum, and it does not block a resumed workspace.
Use `--min-free-gb 100` to impose an explicit floor. Full Android's upstream
64 GB RAM / 400 GB disk guidance concerns a full platform build, not a measured
requirement for this narrower target. Reduce `--jobs` if memory is tight.

Persistent files are arranged as follows:

| Path under the work directory | Purpose |
| --- | --- |
| `source/` | Manifest-pinned public projects and separately pinned vendor trees. |
| `source/out/` | Incremental Android build outputs. |
| `source-selection.json` | Current approved source selection, including discovered dependencies. |
| `cache/` | Exact OTA downloads and resumable partial downloads. |
| `host-tools/` | Private Repo launcher and authenticated avbroot installation. |
| `runs/<timestamp>-<stage>/` | Invocation status, logs, source/vendor diagnostics and comparison receipts. |

Failures retain the checkout, outputs and logs. Resolve the reported problem
and rerun the same command. Source revisions and inventories are rechecked;
modified or extra source files are rejected, never silently reset. Concurrent
commands against one work directory are prevented. There is no automatic
cleanup. A cached reference ELF or editable receipt is never accepted as
authentication: every comparison reextracts the reference from the verified
OTA. Large extracted partitions remain in that invocation's `reference/`
directory and may be removed after reviewing its results.

### Interrupted initial Repo setup

An interruption during the first `repo init` can leave `source/.repo/` before
the pinned manifest has been installed and selected. Resume reports the
specific manifest validation failure and leaves that metadata untouched.
If the failure followed an interrupted first initialization, retain only the
incomplete source directory under a new name:

```sh
VOLD_INCOMPLETE_SOURCE="$(mktemp -d "$VOLD_LOCAL_WORK/source.incomplete.XXXXXXXX")"
mv -T -- "$VOLD_LOCAL_WORK/source" "$VOLD_INCOMPLETE_SOURCE"
```

Then rerun the command that failed with the same `--work-dir`. Keep
`source-selection.json`, `cache/`, `host-tools/`, and `runs/` in place; the
new initialization preserves that selection and those retained files. For
a previously complete checkout, investigate any changed pinned manifest or
local override instead of treating the validation failure as interrupted setup.

## Exact pinned inputs

- Source: `lineage-24.0-20261016-nightly-pdx235-signed.zip`.
- Official source OTA SHA-256:
  `8af46661a1440e98018bb2abb155317d6d4ae54e051160f0786b75c1e6806c82`.
- Complete 1,243-project release manifest:
  `locks/lineage-pdx235-20261016-build-manifest.xml`.
- Source lock: `locks/lineage-vold-baseline-20261016.json`.
- Exact product/variant: `lineage_pdx235` / `userdebug`, release `cp2a`,
  then `m vold`, without applying a patch or introducing a replacement binary.

At the pinned `build/make` revision, the legacy two-component
`lunch lineage_pdx235-userdebug` syntax is rejected. The pinned
[`vendor/lineage/vars/aosp_target_release`](https://github.com/LineageOS/android_vendor_lineage/blob/87daf527b0288b60c9cc07c05a5f5aad1660c234/vars/aosp_target_release)
sets `cp2a`; its exact bytes are hash-checked before build setup. The helper
uses the unchanged `_lunch_meat lineage_pdx235 cp2a userdebug` configuration
function after `check_product`, avoiding the `lunch` wrapper's roomservice
downloads. Automatic kernel builds are blocked. Upstream shell setup runs
without nounset because its optional variables are intentionally unset.
The comparison receipt records the actual release and setup invocation.
The pinned setup and roomservice findings are retained in
[`vold-build-setup-review-20261022.json`](evidence/vold-build-setup-review-20261022.json).

These pins were recorded from signed ModOS source-verification runs
[#39632433468](https://github.com/0cwa/ModOS/actions/runs/39632433468)
and [#39678546023](https://github.com/0cwa/ModOS/actions/runs/39678546023).
The complete OTA is reauthenticated during each real baseline run:
downloaded bytes must match the digest, the embedded certificate's key must
match the independently pinned LineageOS official publisher key, the ZIP
certificate must be unique, and `avbroot ota verify` must succeed.

The original `system.img` is extracted read-only, then its
`/system/bin/vold` is recovered as the reference executable. A clean,
exact-revision, dependency-limited LineageOS checkout builds another unmodified
`vold` for the **same** pinned product. The resulting report includes
the reference and built SHA-256s, byte counts and first differing offset.

Only `byte_identical: true` is success. The workflow fails on a mismatch
and uploads a diagnostic JSON receipt, rather than bypassing the check.

## Dependency-closure strategy

The original workflow fetched **all 1,243 pinned projects** before issuing
`m vold`. That is unnecessary for a single module, and it made each trial
expensive. Instead, the dependency-analysis recipe in
[`locks/lineage-vold-source-probe-20261016.json`](../locks/lineage-vold-source-probe-20261016.json)
selects **84 initial projects** after source tracing corrected the original
70-project seed. The plan artifact records the exact
current selection and manifest digest. This is a conservative dependency
seed, NOT a complete or proven Soong build graph.

The additional 14 projects cover verified native/HIDL/AIDL providers,
suspend interfaces, Rust generated flag-library dependencies, and Sony USB
and dataservices namespaces. Three former hints pointed to the wrong project:

| Module | Verified pinned project |
| --- | --- |
| `libbase` | `system/libbase` |
| `libgsi` | `system/gsid` |
| `libhardware_legacy` | `hardware/libhardware_legacy` |

The less obvious chain is `vold_flags_c_lib` through Soong's
`cc_aconfig_library` generator to `libaconfig_storage_read_api_cc` and its
Rust bridge/crates. This requires `external/rust/cxx` and
`external/rust/android-crates-io`. Exact-revision evidence for these choices
and the remaining additions is retained in the seed's `source_evidence`
and `reviewed_dependency_chains` fields. This static research saves repeated
failed builds; it does not make the selection complete.

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

### Working vendor acquisition from pinned public mirrors

The official public manifest omits `vendor/sony/pdx235` and
`vendor/sony/sm6375-common`, but their inputs are available from TheMuppets.
The supplementary [vendor candidate lock](../locks/lineage-vold-vendor-candidate-20261016.json)
records these exact commits, Git trees, complete SHA-256 inventories, source
input hashes, and the eight generated declaration hashes:

| Vendor project | Immutable revision | Files / content bytes |
| --- | --- | ---: |
| [TheMuppets/proprietary_vendor_sony_pdx235](https://github.com/TheMuppets/proprietary_vendor_sony_pdx235/tree/1e503a56c0d16de1f19f2b011581908dac29065b) | `1e503a56c0d16de1f19f2b011581908dac29065b` | 232 / 380,859,981 |
| [TheMuppets/proprietary_vendor_sony_sm6375-common](https://github.com/TheMuppets/proprietary_vendor_sony_sm6375-common/tree/2114108d5adfe787503ad0c87508d78123551991) | `2114108d5adfe787503ad0c87508d78123551991` | 882 / 304,006,211 |

These are reproducible third-party mirror candidates. Their public availability,
immutable identities and source compatibility permit a genuine build probe;
they do not prove the official OTA builder used those exact vendor commits.
The receipts say `publisher_authenticated: false` and preserve the final
byte-identical comparison requirement.

The acquisition helper shallow-fetches those exact commits, verifies complete
file inventories, checks the three public prerequisite revisions, and runs the
unchanged pinned `extract-files.py --regenerate_makefiles`. It accepts success
only when regeneration preserves all eight locked declarations byte for byte.
Untracked/ignored files, changed revisions, unresolved LFS pointers, unsafe
links, different mirrors and changed inventories are rejected. A resumed run
verifies and reuses completed worktrees without fetching them again. The main
build verifier allows only these two independently verified supplemental roots,
and records their complete provenance in the comparison report.

Actual verification on 2026-10-22 established:

- All 1,106 proprietary-list destinations are present (228 device + 878 common).
- All 27 explicit common-list SHA-1 requirements match, including the two
  expected post-fixup hashes.
- The exact upstream generator succeeds and all eight generated declarations
  match the pinned mirrors byte for byte.
- The real local `vendor` command succeeds twice on a proper Repo checkout;
  both receipts match and all five projects' fetch records are unchanged.

The recorded evidence is
[vendor candidate compatibility](evidence/vold-vendor-candidate-20261022.json)
and [real local vendor integration](evidence/vold-local-vendor-integration-20261022.json).
These checks run real extraction Python code and Git operations, not Android
compilation.

The earlier [source-list-only failure](evidence/vold-vendor-preflight-20261022.json)
remains historical evidence: `--regenerate_makefiles` reads proprietary ELF
architecture and dependency metadata, so empty vendor trees do fail. Acquiring
the actual blobs resolves that failure; fabricating declarations is unnecessary.

LineageOS also officially documents
[extracting proprietary inputs directly from an installable ZIP](https://github.com/LineageOS/lineage_wiki/blob/main/pages/extracting_blobs_from_zips.md)
by passing the archive to the device's `extract-files.py`. The exact authenticated
October 16 OTA is an alternative source to investigate if a future mirror pin
is unavailable or produces a binary mismatch. This workflow currently uses the
verified mirror contract; a different extraction/fixup result needs its own
recorded inventory, rather than silently replacing these pinned inputs.

The on-disk dependency resolver
[`src/ci/vold_source_closure.py`](../src/ci/vold_source_closure.py)
verifies the complete original manifest and produces a pinned selection.
After vendor acquisition and a limited
`repo sync <project paths>` succeed, it scans available `Android.bp`
definitions for a **partial** transitive module graph. Up to three static
scans batch missing providers from reviewed hints before compilation,
syncing only newly selected pinned projects. Unknown, ambiguous and generated
module names remain visible in diagnostic reports. The real
Soong compiler, rather than the static scan, remains authoritative for
conditional/generated modules. Any missing path reported in the compiler
diagnostics is mapped to its **exact existing** project in the official
manifest. Known module names have reviewed initial provider hints; unknown
providers and unpinned vendor paths stop for manual investigation.

Once prerequisites have passed, in each local or CI invocation,
[`src/ci/probe_original_vold.sh`](../src/ci/probe_original_vold.sh)
automatically repeats up to **three** controlled passes. It retains already
synchronized projects and the expanded selection in the same working tree, fetches only newly identified
pinned dependencies, and saves small diagnostic JSON documents showing
unknown modules and other blockers. It never enables
`SOONG_ALLOW_MISSING_DEPENDENCIES`, changes the build target, or applies
the exFAT timeout patch. Any unexpected source revision or modified
checkout fails immediately.

The build verifier supports Repo's actual single-include `manifest.xml`
wrapper and verifies the included flattened manifest bytes. Local manifest
overrides are rejected. A resumed sync distinguishes actual Git worktrees
from parent or partially created directories. Before compiling, the helper
rejects unrecorded external source trees and inherited missing-dependency
bypasses, fixes the output directory to this workspace and removes any old
final `vold` executable.
It repeats source/workspace verification after a successful compile.
A real Repo integration against two tiny local Git projects confirmed the
include wrapper, selected-only sync, missing-only resume, and no sync when
the selection was unchanged. See
[`vold-repo-integration-20261022.json`](evidence/vold-repo-integration-20261022.json).
This exercises the real Repo tool, not an Android compilation.

You can test the graph on an ordinary GitHub-hosted runner with **Actions →
Qualify original LineageOS pdx235 vold (no patch) → Run workflow**,
choosing `plan` (the default). This needs **no self-hosted runner or
repository variable** and produces a
`vold-source-provisional-plan` artifact. It does **not** compile or
qualify a binary.

Ordinary PR CI and `main` CI also run this same plan command and retain the
same `vold-source-provisional-plan` artifact. This provides a low-cost plan
on the merged commit even when the GitHub connector cannot dispatch manual
workflows. These CI steps never sync Android sources or run the compiler.

## Optional GitHub Actions execution

The local commands above require no Actions configuration. The existing manual
`.github/workflows/vold-baseline.yml` remains available for an owner who wants
to use a trusted dedicated Linux x64 runner. `plan` runs on a standard hosted
runner and needs no repository variables; ordinary PR/main CI also uploads the
same provisional plan artifact.

The optional `probe` and `compare` jobs remain main-only, read-only,
nonpublishing, and gated by `MODOS_VOLD_BASELINE_RUNNER_READY=true` plus an
absolute private `MODOS_LINEAGE_LARGE_SCRATCH` directory on a runner labeled
`modos-lineage-large`. The workflow's 100 GB free-space check is an initial
allowance, not measured closure evidence. It uploads small diagnostics and
cleans its temporary checkout. Local runs retain their workspace instead.

No runner registration, repository setting change, or Actions dispatch is
needed for the documented local build.

## After the first authentic success

Review the local `runs/<timestamp>-compare/compare/baseline-*/baseline-report.json`
and its authenticated reference receipt, or the optional Actions diagnostics
artifact. Record the exact source, manifest, vendor lock and reference SHA-256,
plus the ModOS commit and local invocation identifier or CI run ID.
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
