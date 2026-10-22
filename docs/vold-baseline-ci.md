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

### Verified proprietary-input blocker

The exact public manifest does **not** contain `vendor/sony/pdx235` or
`vendor/sony/sm6375-common`. The original assumption that
`--regenerate_makefiles` can generate their declarations using only public
file lists is incorrect at the pinned revisions. This option skips
extraction, but the subsequent
[`write_elfs_package`](https://github.com/LineageOS/android_tools_extract-utils/blob/ff6589c6b0a4a00c592e98940e9e6b6a50c76569/extract_utils/makefiles.py#L256-L271)
still calls
[`get_file_machine_bits_libs`](https://github.com/LineageOS/android_tools_extract-utils/blob/ff6589c6b0a4a00c592e98940e9e6b6a50c76569/extract_utils/elf.py#L38-L57),
which opens each proprietary ELF to determine its architecture and dependencies.

An actual replay of the unchanged pinned Python sources on 2026-10-22
failed with `FileNotFoundError` for
`vendor/sony/pdx235/proprietary/vendor/lib64/camera/com.qti.eeprom.hi846_back_eeprom.so`.
Upstream had already written four partial vendor declaration files. Those
files are not successful or qualified preparation. The reproducible command,
source revisions, input hashes and normalized traceback are retained in
[`docs/evidence/vold-vendor-preflight-20261022.json`](evidence/vold-vendor-preflight-20261022.json).
This replay ran Python extraction code only; it did **not** compile Android.

To avoid downloading toolchains before this known failure, the probe first
syncs exactly three pinned source projects: both Sony device trees and
`tools/extract-utils`. The
[`prepare_vold_vendor_makefiles.py`](../src/ci/prepare_vold_vendor_makefiles.py)
preflight verifies those revisions and writes `sony-vendor-input-report.json`
with `status: blocked` and `makefiles_generated: false`, then exits before
invoking the generator or syncing the remaining seed projects. It rejects
the former synthetic-success receipts; arbitrary existing vendor files
also remain unqualified.

**Both real build modes are currently blocked here.** A reviewed acquisition
and provenance contract for the required proprietary inputs is needed before
vendor preparation can be enabled. Authenticating the pinned original OTA
may provide a source for some of these bytes, but extraction, fixups, original
build inputs and every required file must be accounted for. The present code
does not claim that this acquisition is implemented. Do not fabricate files,
disable ELF checks or enable missing-dependency flags to bypass this gate.

The on-disk dependency resolver
[`src/ci/vold_source_closure.py`](../src/ci/vold_source_closure.py)
verifies the complete original manifest and produces a pinned selection.
After the vendor-input gate is resolved and a limited
`repo sync <project paths>` succeeds, it scans available `Android.bp`
definitions for a **partial** transitive module graph. Up to three static
scans batch missing providers from reviewed hints before compilation,
syncing only newly selected pinned projects. Unknown, ambiguous and generated
module names remain visible in diagnostic reports. The real
Soong compiler, rather than the static scan, remains authoritative for
conditional/generated modules. Any missing path reported in the compiler
diagnostics is mapped to its **exact existing** project in the official
manifest. Known module names have reviewed initial provider hints; unknown
providers and unpinned vendor paths stop for manual investigation.

Once prerequisites have passed, within a single CI run,
[`src/ci/probe_original_vold.sh`](../src/ci/probe_original_vold.sh)
automatically repeats up to **three** controlled passes. It caches already
synchronized projects in the same working tree, fetches only newly identified
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

## Runner provisioning for source compilation

For `probe` or `compare`, the workflow still requires a trusted,
isolated self-hosted Linux x64 runner labeled `modos-lineage-large`.
Provide a dedicated scratch filesystem with **at least 100 GB free as an
experimental starting allowance**, sufficient CPU/RAM, the Android 17
build prerequisites, and `repo`, `git`, `python3`, and `realpath`.
The authenticated `compare` mode additionally needs `avbroot`, `debugfs`,
`unzip`, `curl`, `openssl`, and `sha256sum`. One iteration may require
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
At the current reviewed revision it stops at the known vendor-input gate
before downloading the large seed or invoking `m vold`.

Only after a successful, reviewed `probe`, use `compare` for a real proof.
The compare workflow downloads the OTA before the source probe; running it
while the known vendor gate is blocked would waste that download.
It first reauthenticates the exact
publisher-signed official 2026-10-16 OTA, extracts the original
`/system/bin/vold`, and invokes the same bounded dependency solver.
**Only** a real `byte_identical: true` report with equal original and
built hashes qualifies this baseline. If the binaries differ, it emits a
diagnostic comparison receipt and fails.

### Hosted-runner alternatives and observed access

Checked on 2026-10-22: GitHub documents larger Ubuntu x64 runners with
4 CPUs / 16 GB RAM / 150 GB storage and 8 CPUs / 32 GB RAM / 300 GB storage.
Those are plausible experimental build hosts, not measured requirements
for this incomplete dependency closure. The 100 GB free-space gate does not
prove either machine has enough RAM or working space after tool installation.
See GitHub's [larger runner specifications](https://docs.github.com/en/actions/reference/runners/larger-runners).

GitHub's managed larger runners require an organization or enterprise on
Team or Enterprise Cloud; `0cwa/ModOS` is currently owned by a user account.
They cannot be assumed available merely by changing `runs-on` to an invented
label. An actual organization runner with repository access, or a dedicated
trusted self-hosted Linux x64 machine, must be provisioned and checked first.
See [GitHub-hosted runner availability](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
and [managing larger runners](https://docs.github.com/en/actions/how-tos/manage-runners/larger-runners/manage-larger-runners).
Standard Ubuntu runners advertise only 14 GB storage and remain suitable
for this plan/unit-test workload, not the current Android build preflight.

The last original-baseline attempt
[#39699955476](https://github.com/0cwa/ModOS/actions/runs/39699955476)
failed its hosted prerequisites job with both readiness variables empty;
the self-hosted build job was skipped. This is evidence about that run,
not proof of the current runner inventory. The connected GitHub actions
expose repository changes, CI inspection and reruns, but no workflow dispatch,
runner inventory or variable mutation. No runner or configuration change
has been claimed on that basis.

For an owner with the GitHub CLI configured, the explicit manual plan is:

```sh
gh workflow run vold-baseline.yml --repo 0cwa/ModOS --ref main -f mode=plan
```

After resolving the authenticated vendor-input contract and verifying the
trusted runner, set the two documented repository variables and dispatch
`-f mode=probe`. Inspect its source selection and diagnostics. Dispatch
`-f mode=compare` only once that actual compile succeeds. The connector's
missing dispatch capability does not justify changing any qualification gate.

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
`vold-probe-compare-diagnostics` artifact. Record the exact
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
