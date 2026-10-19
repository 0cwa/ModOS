# LineageOS 24.0 / pdx235 build qualification

This document records the Android 17 qualification path. Publication is enabled
for verified official LineageOS 24.0 pdx235 nightlies; other versions, devices,
and private/shared output scopes remain unsupported. The existing six-hour
release schedule, signing keys, and executable lock are unchanged.

## Why a separate profile exists

The existing `care-map-pdx235` development profile selects the
publisher-authenticated 2026-10-09 LineageOS 23.2 OTA by **exact filename and
SHA-256**. The normal LineageOS provider instead chooses the newest nightly,
which can change Android major versions. Neither is suitable for a controlled
LineageOS 24-specific test.

`care-map-pdx235-lineage24` chooses the newest **24.0 nightly for pdx235**
returned by the official LineageOS builds API. It refuses absent or different
series (23.2, 24.1, 25.x), channel, or device. Each downloaded ZIP must match
the API-supplied SHA-256. The independently pinned LineageOS signing key must
also match the OTA's actual signer; the source-built guarded verifier performs
the whole-file OTA/payload/AVB check and reads the care map from partition
filesystems. The future OTA's hash is **not hardcoded** because each nightly has
different bytes. Version series is checked before download/patching; signer,
content and output care ranges are checked by the normal pipeline.

Both care-map profiles select the reviewed immutable helper revision and
the same authenticated avbroot source-build manifest. This is not a fallback to
the vendor tool's historical AVB-derived care-map semantics. All checks remain
fail-closed. The explicit development profiles remain restricted to
`OUTPUT_SCOPE=local-unpublished`.

## First Android 17 candidate

Use the existing **Patch and Release LineageOS OTA** workflow with:

- `device-id=pdx235`, `update-channel=nightly`.
- `development-profile=care-map-pdx235-lineage24`.
- `release-type=build-only`.
- `root-mode=both` (primary rootless plus secondary Magisk, reusing shared
  image preparation and repacking).
- `signing-mode=repository` and `export-device-candidate=true` only when
  privately preparing a candidate for a device with the matching configured
  keys. Otherwise use disposable signing keys and do not install that output.
- Enable microG and the required existing module selections deliberately. The
  scheduled preset is **not automatically applied** to manual workflow inputs.

Use `release-type=default` or `force-publish` only with the supported
LineageOS 24.0 pdx235 nightly source and after source-identity and post-sign
verification gates pass. The explicit development profile remains build-only.
Build-only candidate artifacts do not appear in public GitHub Releases or
Custota's OTA channel.

Before any physical Android 17 installation, retain the precise source filename,
API SHA-256, source/engine/helper receipts, both final OTA digests, AVB signature
and care-map verification receipts, vbmeta flags, partition image proofs, and
the prior device rollback/recovery plan. The existing code does not itself prove
Android 17 compatibility on the phone. Rootless first-boot and a Custota update
cycle are separate runtime acceptance from successful artifact verification.
The user has confirmed microG and Custota work on the current installed build;
these are positive observations, **not** evidence that the new Android 17 OTA
has already booted. Magisk device testing is deferred and should not block
rootless delivery; generating both variants still requires the helper to
verify both signed outputs before success.

The normal `development-profile=none` build path now selects the guarded
source-built expected-map verifier for an API-digest-identified pdx235
LineageOS **24.0 nightly**. That automatic selection is scoped to exactly the
reviewed version series; it does not silently accept future 24.1 or 25.x
images. The PR-only CI build exercises this normal route with disposable keys.
The explicit `care-map-pdx235-lineage24` profile is still required to export
a repository-signed private device-acceptance candidate.

Publication is enabled for the supported LineageOS 24.0 pdx235 nightly series.
The ordinary build still has to pass the official API digest and publisher-key
checks plus strict signed-output and runtime care-map postflight. Physical
Android 17 upgrade and rootless/Magisk behavior remain separate acceptance
work; successful publication checks do not prove device behavior. Do not fill
the October 9 exception digest with an unrelated LineageOS 24 digest.
