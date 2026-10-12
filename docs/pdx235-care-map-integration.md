# pdx235 care-map integration and acceptance

[Remaining work](remaining-work.md) records the current evidence, dependency
order, responsible code boundaries, and check consolidation. This document
specifies the acceptance boundary for
[ModOS #114](https://github.com/0cwa/ModOS/issues/114).

## Current state

The October 9 source OTA has been independently authenticated with the
published LineageOS signing key. Its filesystem fingerprints explain the
care-map identities; they differ from the AVB fingerprint descriptors.
The source is Android 16 despite the deliberately overridden Sony Android 15
fingerprint string. Original signed-image evidence covers `odm`, `product`,
`system`, `system_ext`, `vendor`, and `vendor_dlkm`.

The research candidate and independent synthetic conformance suites agree.
No corrected production strict verifier, final signed-output proof, or
physical pdx235 acceptance has been established. A green synthetic test
cannot substitute for those results.

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

## Generator and verifier requirements

Use one reviewed semantic rule for generation and independent verification:

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
5. Reject unrelated source-integrity failures. A reviewed expected-map
   verification boundary must replace the current incompatible AVB-only
   equality rule; a blanket mismatch waiver is insufficient.

Avoid duplicating the helper's map parser or verifier in ModOS. The consumer
passes reviewed inputs through its typed configuration and existing helper
boundary. A dependency change must update source revision, compatibility
manifest, executable trust policy as applicable, and selection identity
consistently so cached assets cannot conceal changed semantics.

## Nonpublishing output acceptance

Prepare a clean fixture runner that can use the exact authenticated October 9
source; the normal provider selects the latest nightly and does not establish
that fixture identity. Use read-only GitHub credentials and test signing
material. The existing reusable release job still has write-scoped
`GITHUB_TOKEN` permissions and production signing inputs, even when its
separate `GH_TOKEN` is withheld for build-only. Correct that runner boundary
before using it for this acceptance.

Start with microG off to isolate the care-map fix, then exercise microG on.
Use LineageOS/pdx235, `local-unpublished`, no publishing, and rootless/Magisk
outputs with the configured preinit device. Do not upload OTA images to
release assets, Custota channels, or public CI artifacts during this stage.

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

After semantic implementation, signed-output verification and applicable
device acceptance are reviewed, promote the helper dependency before the
ModOS microG consumer. Remove the temporary quarantine explicitly in a
reviewed release change; keep microG scheduled builds off until its device
acceptance is complete. Resolve #114 using that evidence, not test counts.

## Validation ownership

Existing source-policy, publisher, module-selection, quarantine, workflow,
executable-lock and helper-compatibility suites cover their own boundaries.
The removed static integration suite repeated literal tool pins, shell
spelling, workflow inventory and behavior already checked elsewhere.
Keep one authoritative test for each behavior and focused negative cases.
Independent helper conformance owns binary semantics; signed-output checks
own authenticity; device acceptance owns runtime results.
