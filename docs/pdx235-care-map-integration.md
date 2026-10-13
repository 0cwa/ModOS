# pdx235 care-map integration and acceptance

[Remaining work](remaining-work.md) records the current evidence, dependency
order, responsible code boundaries, and check consolidation. This document
specifies the acceptance boundary for
[ModOS #114](https://github.com/0cwa/ModOS/issues/114).

## Physical failure discovered October 13

The `b8b2a1e` rootless device candidate failed Android startup because compatible
SELinux patching duplicated MSD app selectors in vendor and ODM context files.
The phone recovered by booting its previous slot. The development helper now
keeps module app contexts in the platform file only; binary/CIL patches remain.
See [the failure and repair record](pdx235-bootloop-20261013.md). Earlier artifact
checks below do not establish that the original candidate can boot. Fresh device
acceptance is required for the corrected candidate; publication stays quarantined.

## Current state

The October 9 source OTA has been independently authenticated with the
published LineageOS signing key. Its filesystem fingerprints explain the
care-map identities; they differ from the AVB fingerprint descriptors.
The source is Android 16 despite the deliberately overridden Sony Android 15
fingerprint string. Original signed-image evidence covers `odm`, `product`,
`system`, `system_ext`, `vendor`, and `vendor_dlkm`.

The strict development verifier and helper postflight are implemented. Real
microG-off and microG-on rootless/Magisk pairs passed with disposable local keys;
all six images were freshly bound to each verified payload and their actual
filesystem properties checked. This closes the earlier local artifact-proof gap.
The complete scheduled-default profile plus custom boot animation, stock-updater
removal and microG has now also passed cryptographic/filesystem-property
postflight for both roots with compatible SELinux context handling and the corrected updater image-root adapter. Genuine legacy
module archives were verified against the pinned SSH signer. Independent detailed
module inspection passed: genuine module payloads, locked microG/scoped XML,
policy files/labels and shared filesystem/vendor_boot bytes were checked. Both
boot variants are regular normalized archives with source member contents
preserved; Magisk targets boot with `sda47`, while rootless has no Magisk. The
updater APK and selected permission XML are absent. Retained inert ODEX/VDEX
caches are informational; their presence adds no acceptance gate. The
[compact review receipt](evidence/pdx235-20261012.json) records the complete-profile
artifact digests, maps, budget and selected modules.

The explicit source-build/repository-signing workflow and retrieved repository-key
candidate have now also passed; see the receipt's `repository_candidate` section.
This establishes configured-key provenance, not the actual device's enrolled-key
match, physical pdx235 acceptance, production dependency promotion or publication.

The exact fixture was `lineage-23.2-20261009-nightly-pdx235-signed.zip`,
1,232,106,897 bytes, SHA-256
`b2b4269738df1d3092de49bde6c7d0ffcb62ae19b342213a819187c30b7ab1f8`.
The source-engine commit was
`6107d7e92c35b81cac0d28aec3806b45c8cb1f59`; its source patch/build provenance are
carried with the helper. The local proof binary SHA-256 was
`81187e3eb8ed32ea73ce680d757e291df15320a0fc1ab9e5f21c16d9a682c7fe`.
This measured digest records that experiment; it is not a production executable
trust pin.

The production source-exception digest remains empty and strict source
verification remains active. The LineageOS/pdx235 output policy permits only
`local-unpublished`; direct single and paired publication helpers enforce
the same quarantine. Pre-existing output ZIPs or associated evidence are
rejected rather than accepted through the historical existence shortcut.

Source publisher checks bind the extracted PEM certificate both to the
independently trusted SPKI and to the unique certificate member in the
current source archive. The helper must then authenticate the whole-file
OTA signature, payload, partition hashes, and applicable AVB chain.
A certificate identity match alone is not signature verification.

## Implemented generator and verifier boundary

The source-engine expected-map interface and helper use one semantic rule:

1. Obtain required partitions from the authenticated payload/AVB metadata;
   do not let an incomplete care map define which partitions must be checked.
2. Read each fingerprint using its intended filesystem/runtime property ID.
   A mismatching AVB descriptor is not an acceptable substitute.
3. Validate nonempty maps, unique required entries, range syntax and
   half-open bounds within AVB-protected data. Confirm deterministic payload
   writes cover any newly cared blocks. Source sparse ranges can be preserved
   when their reuse proof holds; new allocations require a supported rule.
4. Bind extracted image SHA-256 and sizes to the cryptographically verified
   payload manifest. Synthetic caller-supplied hashes are not authentication.
5. Reject unrelated source-integrity failures. Strict `--expected-care-map`
   equality replaces only the incompatible AVB-property comparison; all
   cryptographic checks remain active. Successful `--verification-report`
   receipts include verified image hashes/sizes, protected extents and AVB flags.
   Explicitly expected signing keys cannot be satisfied by unsigned headers.

Avoid duplicating the helper's map parser or verifier in ModOS. The consumer
passes reviewed inputs through its typed configuration and existing helper
boundary. A dependency change must update source revision, compatibility
manifest, executable trust policy as applicable, and selection identity
consistently so cached assets cannot conceal changed semantics.

## Nonpublishing output acceptance

The exact authenticated fixture was tested locally with microG off, then on,
using rootless/Magisk pairs, Magisk v30.7 and preinit device `sda47`. Each output
passed configured OTA/AVB signer verification, payload/partition checks, safe
runtime maps and fresh filesystem-property proof. A real wrong-signer secondary
failed before sidecar/report callbacks. Local evidence includes the paired
`acceptance.json` receipts and independent product/secondary-failure records.

| Disposable-key output | SHA-256 |
| --- | --- |
| microG off, rootless | `50d58cef94b429699572075769d6d2c9042a06b85a10a73a0df61a3c33e11d07` |
| microG off, Magisk | `e50e2947e2711b3f62046b9783362be58e2a7f5b7ffe413e18e8e67e57cdc3f9` |
| microG on, rootless | `f42cb6c36e4b6bc8c107850f5875c2d5c338ed8b792f63d1b5a62c6ab1e4fdd6` |
| microG on, Magisk | `c9942edfe6cf7587da3a26b29d4f150377f7f5a7b84eddb138243827f05d9e46` |

Independent product inspection found 24,419 changed protected blocks outside
source care ranges; deterministic final coverage accepted them safely. The
original WebView was unchanged, installed locked APK bytes matched, and XML
scope and modes/labels were checked. Final dynamic-image sizes fit the
unchanged authenticated group budget. Initial Code 28 failures came from an
8.8 GiB host `/tmp` tmpfs; workspace scratch and per-stage cleanup resolved them
without a new partition-growth policy.

Build-only jobs now request `contents: read` and ordinary runs use disposable
keys without inheriting production signing secrets. The separately selected
repository-signer candidate forwards only signing material; publishers remain
skipped and quarantine remains active. A source-build development profile and
an optional authenticated encrypted private handoff are prepared. Ordinary
production executable pins remain unchanged. The complete enabled-module profile
has passed actual disposable-key output postflight. The actual read-only repository-key workflow at `0db76b4` succeeded in
[run 38303462721](https://github.com/0cwa/ModOS/actions/runs/38303462721).
The exact encrypted ZIP was retrieved through read-only transport and checked
against SHA-256 `f51e4cf10cb32de8a714e052f1a826301c21e48beb66a5fe393838f79d0bb548`
and size 2,826,101,688 before per-OTA authenticated CMS decryption. Independent
local checks passed on both exact decrypted outputs: signatures, payload/AVB,
runtime maps and write coverage, every filesystem property, paired image bytes,
genuine module/microG content, root distinctions and unchanged group quotas.
The configured public PKMD SHA-256 is
`a7f6fd272a397c28451c131467d51cf1097d2e44c08579dab5784cfbaa3bf2bc`.
Actual device enrollment, boot and persistence remain unobserved.

Explicit `export-device-candidate=true` permits normal signed OTA ZIPs in the
`device-candidate` GitHub artifact when the optional recipient is empty. Extract
GitHub's outer artifact ZIP once and sideload the chosen inner OTA ZIP. A public
recipient certificate selects the existing encrypted private handoff instead.
Both are build-only device-acceptance transport; release assets and Custota
channels remain quarantined. The normal provider selects the latest nightly. The `care-map-pdx235` development
profile now selects the exact October 9 filename and authenticated SHA-256 from
the official API, failing closed if the fixture is absent or its digest changes.
This keeps the boot-failure repair separate from the newer LineageOS 24 / Android
17 source. Each output still requires fresh cryptographic and device acceptance.

For each final signed output, including secondary Magisk:

- Derive the expected AVB public key from the configured signing key with
  its passphrase, and verify against it and the configured OTA certificate.
- Verify whole-file/payload/partition/AVB integrity and correct care-map
  semantics; re-extract final signed images and repeat image/property/range
  proof against that specific output's verified manifest.
- Check the selected modules, rootless/Magisk distinction, Magisk boot target
  and preinit device, and expected equality of shared prepared partitions.
- Refuse malformed/missing/duplicate maps, wrong fingerprints/ranges,
  wrong signers, unsafe payload coverage, and any secondary-output failure
  before sidecars, reports, release writes or success markers are emitted.

Record source/output digests, tool/helper identities, selected options,
map identities/ranges, expected signer identities and verification results
in one reviewable receipt. Reusing outputs requires fresh verification of
those actual bytes; otherwise keep reuse disabled. Publication asset-name
and selection-sidecar checks do not verify an OTA's cryptography.

## Physical acceptance and release

Observe effective runtime fingerprints for all required partitions, verity
mode, snapuserd verification status and successful-slot marking on pdx235.
The pinned Android runtime can skip mismatching map entries and can mark
without verification when verity is disabled or care-map parsing fails.
It can also legitimately use snapuserd verification instead of individual
care-map block reads; recognize that supported path in acceptance criteria.

Test first boot and a Custota OTA/A-B persistence cycle for rootless and
Magisk. For Magisk, complete the matching manager's additional setup and
verify `su -c id` after reboot. For microG, test self-check, restricted
signature spoofing, account behavior, push, location and OTA persistence.
Physical evidence requires the actual device and cannot be generated by CI.

LineageOS's `--clear-vbmeta-flags` must apply to both outputs. Inspect resulting
root flags and chained descriptors. Clearing verification-disable flags does
not establish bootloader enforcement, trusted on-device keys, or first-boot
block verification; the source's public AVB test keys are not a publisher
identity. Check actual AVB/verity state and recovery behavior on-device.

After the completed configured-signer artifact evidence and applicable enrolled-key
and device acceptance are reviewed, promote the helper dependency before the
ModOS microG consumer. Remove the temporary quarantine explicitly in a
reviewed release change. At the owner's October 13 request, scheduled pdx235
builds now select microG by default; this does not lift publication quarantine
or establish the remaining device acceptance. Resolve #114 using that evidence, not test counts.

## Validation ownership

Existing source-policy, publisher, module-selection, quarantine, workflow,
executable-lock and helper-compatibility suites cover their own boundaries.
The removed static integration suite repeated literal tool pins, shell
spelling, workflow inventory and behavior already checked elsewhere.
Keep one authoritative test for each behavior and focused negative cases.
Independent helper conformance owns binary semantics; signed-output checks
own authenticity; device acceptance owns runtime results.
