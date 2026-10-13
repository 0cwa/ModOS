# Remaining work and execution order

Updated 2026-10-12 for the goal integration branch. This follows the initial audit
of ModOS main `6572a6e`, feature PRs #96/#97, quarantine #121 and helper research
#7/#10/#11/#12. Implemented branch work, measured local artifact evidence, main
promotion and physical acceptance are separate states.

## Completed work and evidence

The earlier #114 forensic runs established publisher authenticity and the Sony
Android 15 fingerprint override inside authentic Android 16 images. The current
work reacquired the exact October 9 fixture for actual source/output validation:

- `lineage-23.2-20261009-nightly-pdx235-signed.zip`, 1,232,106,897 bytes;
  SHA-256 `b2b4269738df1d3092de49bde6c7d0ffcb62ae19b342213a819187c30b7ab1f8`.
- Independent LineageOS publisher verification, official API digest/size,
  whole-file/payload/partition/AVB checks and all six filesystem fingerprints
  under their intended runtime property IDs passed. Wrong expected OTA
  certificates and AVB keys were rejected.
- The source-engine implementation `6107d7e92c35b81cac0d28aec3806b45c8cb1f59`
  adds strict expected-map verification, deterministic range coverage, successful
  authenticated image receipts and actual signature requirements for explicitly
  trusted AVB keys. The reviewed patch and build provenance accompany the helper.
- Real rootless/Magisk pairs with microG off, then on, passed per-output
  cryptographic verification and fresh image/property proof using disposable
  local signing keys. Both final verified vbmeta headers had flags zero. A real
  wrong-signer secondary failed before success assets were created.
- Independent microG output inspection confirmed the locked APK bytes, scoped
  permission XML, modes/labels, shared product equality and unchanged original
  WebView. It measured **24,419 changed protected blocks outside the source care
  ranges**, demonstrating why permanent `D ⊆ S` rejection is inappropriate.
- The initial packing failure was host scratch exhaustion: `/tmp` was an 8.8 GiB
  tmpfs. Workspace scratch and staged cleanup resolved it. Product's protected
  extent compacted to 628,125,696 bytes; final full dynamic images totalled
  3,184,078,848 bytes within the authenticated 6,169,821,184-byte group. No new
  partition-growth restriction was needed.

[Acceptance details](pdx235-care-map-integration.md) records the precise boundary.
The complete scheduled-default profile plus custom boot animation, updater
removal and microG has now also passed cryptographic and filesystem-property
postflight for rootless and Magisk with compatible SELinux-context handling. Its genuine legacy module archives were authenticated
through the existing pinned SSH-signature boundary. Actual execution exercised
the updater image-root normalization and compatible-context fix. Independent
inspection passed for genuine app/native/init payloads, microG permissions,
compatible policy files/labels, root distinctions and shared image bytes. Both
boot variants are regular normalized archives with preserved member contents;
updater APK and selected XML are absent. Retained inert updater ODEX/VDEX caches
are informational and do not justify another acceptance gate. The compact
[review receipt](evidence/pdx235-20261012.json) binds its exact artifacts, maps,
group budget and selected module IDs.

The explicit source-build/repository-signing workflow has now also succeeded
at `0db76b4edab40386d147fff49a5c6683e8b4551c` in
[run 38303462721](https://github.com/0cwa/ModOS/actions/runs/38303462721).
Read-only [transport run 38306959045](https://github.com/0cwa/ModOS/actions/runs/38306959045)
delivered the exact encrypted archive; per-OTA authenticated decryption and
independent verification of both retrieved repository-key outputs passed.
Actual signatures, payload/AVB/maps/operation coverage, all six filesystem
properties, genuine module/microG content, paired image equality and root
variants were verified again on those bytes. Public PKMD provenance comes from
the configured-key workflow. The device's enrolled key and boot behavior have
not been observed; this proves candidate generation, not installed-key matching
or device acceptance.

Integration code now retains main's boot-animation default, merges stock-updater
selection independently, routes helper tool calls through authenticated sealed
execution, and separates build-only jobs with read-only GitHub permissions and
disposable signing. The explicit repository-signer/private encrypted handoff has
passed the real workflow and retrieved-candidate verification. The source exception remains empty; LineageOS/pdx235
publication remains quarantined and microG schedules remain off.

## Remaining execution queue

| Order | Boundary | Next delivery | Completion evidence |
| --- | --- | --- | --- |
| 1 | pdx235 device | Confirm enrolled-key compatibility, install through the reviewed device procedure, collect first-boot observations and complete rootless/Magisk and Custota persistence acceptance. | Runtime property agreement, effective verity/snapuserd verification, successful-slot state, Magisk setup and microG self-check/spoofing/account/push/location behavior. Requires the actual device. |
| 2 | Dependency/main promotion | Review consolidated source, helper compatibility/trust metadata and measured profile/device evidence; promote helper before its ModOS consumers. | Explicit reviewed dependency changes, coherent selection identities and no stale cached outputs. A source-built development artifact is not a vendor-signed executable-lock promotion. |
| 3 | Release policy | Deliberately lift the pdx235 quarantine and enable any microG publication/schedules only after the applicable acceptance. | Reviewed publication change and real acceptance evidence; #114 closes on those results, not synthetic test counts. |

The read-only [device collector](pdx235-device-evidence.md) is prepared. CI cannot
supply the physical observations or prove the installed signing identity matches
an existing device merely by generating a new test signer.

## Range policy and check ownership

The implemented rule preserves authenticated sparse ranges for unchanged images
and uses final AVB-protected extents for modified images only when the final
signed payload proves deterministic writes. Fingerprints come from actual
filesystem properties. Cared DISCARD gaps, overlapping cared writes, unsafe
bounds, missing entries and wrong identities fail; uncared sparse holes remain
supported. The implicit system OTA-certificate modification is included. Do not
assume every unchanged partition has contiguous payload coverage.

The manifest-pinned Android `update_verifier.cpp` at
`acd99f7c48e0229eaedd40aa3e2adcbabe2dc283` can skip mismatching identities and can
accept the supported snapuserd path without individual `ReadBlocks` calls.
Device acceptance must observe effective behavior rather than require one log
spelling or a particular read trace.

Keep each invariant at its owning boundary: executable bootstrap owns authenticated
execution; helper compatibility owns source/adapters; publisher policy owns the
source trust anchors; module/quarantine tests own selection and publication
refusal; helper semantics and actual signed outputs own care-map integrity;
device observations own runtime acceptance. The duplicate 220-line static suite
was removed and the quarantine Cartesian cases reduced while retaining behavior.
Do not reintroduce repeated literal pins, complete workflow inventories or
source-snippet checks as integration prerequisites.

## Separate follow-ups

- Stock updater removal is integrated as an independent selection and exercised
  in the real complete-profile artifact pair; device acceptance remains separate.
- F-Droid stays default-off pending a reviewed production lock/profile and source
  delivery. Keep it separate from the helper's one locked-bundle-per-call boundary.
- ROM-neutral naming of the remaining `GRAPHENEOS` configuration array is a
  nonblocking cleanup.
- Reconcile #113/#115 after integration review to distinguish implemented fixes
  from main promotion and remaining device acceptance. Keep #114 open for its
  actual enrolled-key/device/release blockers.

Disposable fixture pairs and the exact retrieved repository-key complete-profile
pair passed strict verification. No OTA was flashed or release-published, and
physical acceptance and reviewed production promotion remain incomplete.

The October 13 upstream dependency qualification merged Custota 6.6/MSD 2.5 and
qualified the guarded avbroot 3.35 source engine. The helper consumer is pinned
to the reviewed source-build revision; official prebuilt avbroot stays at 3.34.1.
See [qualification evidence](evidence/avbroot-20261013.json). Fresh firmware/device
acceptance and the publication quarantine remain separate requirements.
