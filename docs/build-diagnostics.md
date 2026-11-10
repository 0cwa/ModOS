# Build diagnostics and MSD qualification

A completed build and successful final OTA verification do not mean that the
build log is warning-free, or that every module works on a physical device.
ModOS keeps source authentication, final-output verification, diagnostic
review, and device acceptance separate.

## What the build reports

The common build path runs the pinned helper through
[`src/build_diagnostics.py`](../src/build_diagnostics.py). It streams the helper
log unchanged and writes a bounded JSON diagnostic report and Markdown summary.
Actions retains these reports even when the helper or diagnostic guard fails.
Local builds write them under `WORKDIR/diagnostics` by default. Full command
lines, signing material, and full logs are not copied into the reports.

| Observation | Behavior |
| --- | --- |
| Original source OTA uses public AOSP AVB keys or insecure vbmeta flags | Keep the warning visible in the source section; require the existing source-authentication checks to pass. |
| A finished OTA emits an AVB test-key or insecure-flags warning | Fail the build before downstream publication. |
| The same AVB warning cannot be assigned to a known verification phase | Fail rather than assume it belongs to the source. |
| MSD uses its fake-HAL / `su` debug fallback | Fail, including when the helper exits successfully. |
| Selected MSD has no observed compiled-policy injection, or uses the direct-CIL fallback | Fail until that alternate policy path is independently qualified. |
| Another warning appears | Record it with its phase; do not describe the run as warning-free. |
| A requested source/output verification phase is absent or incomplete | Fail diagnostic coverage and require review of the pinned helper's logging contract. |
| The helper returns a failure | Preserve that failure; a diagnostic summary cannot override it. |

The runner recognizes the pinned helper's `Verifying OTA:` markers and uses
avbroot's `INFO OK!` line to delimit their log scope. It compares the explicit
source and output paths, including both outputs in a paired build. Those log
markers are **not cryptographic evidence**. The helper still owns OTA signature,
payload, partition, AVB, recovery-certificate and care-map verification. The
guarded Lineage verifier also checks structured output AVB-header flags.

The guard's source digest is included in the common build-selection identity.
An earlier selection therefore cannot satisfy the existing-release preflight
after diagnostic policy changes. The MSD module archive and helper revision
remain the authenticated, pinned upstream inputs; no MSD binary is patched.
Local outputs are not reused solely because their files exist: move an earlier
output aside before retrying a build. This also prevents an output left by a
failed diagnostic guard from bypassing that guard on a second invocation.

## November 7, 2026 findings

The reviewed jobs establish the following historical observations:

| Job | Observed result |
| --- | --- |
| [GrapheneOS shiba production](https://github.com/0cwa/ModOS/actions/runs/42343970899/job/128317606949) | Both outputs completed verification and publication, with no comparable AVB or MSD fallback warnings found. |
| [LineageOS pdx235 production, attempt 2](https://github.com/0cwa/ModOS/actions/runs/42343102365/job/128318424426) | Source AVB warnings and the MSD debug fallback occurred. Both finished outputs completed verification without those AVB warnings and were published. |
| [Pre-integration LineageOS comparison](https://github.com/0cwa/ModOS/actions/runs/42341997718/job/128311600319) | The same source AVB warnings and MSD fallback were already present. |

In the Lineage production job, source verification reports the test keys for
`vbmeta` and `vbmeta_system`, plus `vbmeta` flags `0x3`, at approximately
06:02:20 UTC. The MSD fallback occurs at 06:04:23 UTC. Final rootless verification
ends at 06:09:05 UTC and Magisk at 06:10:00 UTC. The earlier fallback occurred
at 05:22:53 UTC. These observations establish the distinction between source
and output warnings; they do not establish correct on-device MSD behavior.

The Lineage source ZIP's independently checked publisher certificate is a
different trust boundary from its embedded AVB test keys. ModOS neither edits
the original OTA to hide its warnings nor weakens the source verifier. Both
Lineage output plans already request `--clear-vbmeta-flags`; final verification
continues to check the configured ModOS signing identity.

## MSD on pdx235

[MSD v2.6's policy code](https://github.com/chenxiaolong/MSD/blob/v2.6/msd-tool/src/sepatch.rs)
substitutes the `su` type if `hal_usb_gadget_impl` is missing. Its own comment
describes an emulator fake HAL. The substituted target receives the daemon's
process-inspection and signal rules. That is unsuitable as an implicit
physical-device compatibility decision.

MSD is therefore disabled in the scheduled pdx235 TOML preset and defaults off
in the manual LineageOS workflow. The reusable workflow and local config still
allow an explicit selection, which must pass the diagnostic guard. GrapheneOS
shiba retains MSD. A pdx235 build with MSD enabled and the known fallback will
fail; a build without MSD can still complete normal verification and release.

The pinned source is evidence for a **candidate**, not a final replacement
domain. In the [Qualcomm file-contexts revision recorded by the source manifest](https://github.com/LineageOS/android_device_qcom_sepolicy_vndr/blob/33df3b661808b86eb9cf91429daef2ab26a69fa4/legacy/vendor/common/file_contexts),
the AIDL `android.hardware.usb.gadget-service.qti` executable has the
`hal_usb_qti_exec` file label. The source also contains a separately named
`hal_usb_gadget_qti` domain. Choosing a type from its name alone would miss this
distinction. No runtime domain is approved by this change.

### Collect the missing observations

Use the existing read-only audit against an already authorized pdx235. To audit
an installed build that includes MSD, pass a build-selection TOML with
`ADDITIONALS[MSD] = true`, even though the current scheduled preset disables it:

```sh
python3 src/device/check_pdx235_modules.py \
  --config /path/to/installed-build-selection.toml \
  --root-mode rootless \
  --output /path/to/new-private-audit-directory
```

The MSD check observes the gadget init-service state and only the matching USB
gadget process labels from a bounded process query. It does not start the
daemon, invoke `su`, collect full logs, or treat process presence as successful
MSD operation. Missing, ambiguous or inaccessible observations remain explicit.
See [device evidence](pdx235-device-evidence.md) for the broader audit boundary.

Before re-enabling MSD for pdx235:

1. Bind the source OTA and installed build identity to the observations, and
   confirm the actual gadget HAL executable and SELinux process domain.
2. Check the matching compiled policy, including every policy copy the helper
   patches, and verify narrowly scoped MSD inspection and signal rules for the
   observed domain. Do not add aliases or blanket `su` permissions.
3. Prefer an upstream MSD option that accepts an explicitly validated HAL
   domain and rejects the debug fallback in production. Keep any temporary
   compatibility change small, reviewed, pinned and removable.
4. On the actual device, test MSD export/unexport, HAL pause/resume, USB recovery
   and reconnect, reboot persistence, and relevant SELinux AVC denials. Validate
   each supported root variant before describing it as working.
5. Re-enable the preset deliberately and run fresh paired OTA qualification.

Successful CI establishes only the checks it actually exercised. Physical MSD
acceptance remains pending until these observations exist.
