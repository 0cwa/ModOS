# pdx235 October 9 care-map: ModOS integration and nonpublishing acceptance

**Status: BLOCKED — design and hermetic CI only.** Tracking issue:
[ModOS #114](https://github.com/0cwa/ModOS/issues/114).
This document is Worklane 4's ModOS-side contract, not authorization to
generate, sign, flash, distribute, or publish an OTA. Nothing here removes the
existing production source-policy quarantine or turns on the source exception.

**Baseline:** ModOS branch `feature/microg-pdx235-20260919` at
`b38377c6fd8803f8722e834e68076aa181e5d41b`, plus an isolated draft PR.
The fix to the actual care-map generator/verifier belongs to Worklane 1, the
Android runtime interpretation to Worklane 2, and binary conformance fixtures
to Worklane 3. Keep the existing soft-fork boundary: ModOS orchestrates and
enforces selection/release policy; the helper owns patching; avbroot owns OTA
parsing, generation and verification. Do not port helper/avbroot internals into
ModOS or introduce a temporary release workflow.

## Baseline trust and exact dependency contract

| Dependency/evidence | Immutable identity or existing gate | What it does **not** establish |
| --- | --- | --- |
| Original LineageOS OTA | `lineage-23.2-20261009-nightly-pdx235-signed.zip`, SHA-256 `b2b4269738df1d3092de49bde6c7d0ffcb62ae19b342213a819187c30b7ab1f8`; independently authenticated publisher signature and release metadata | That the original care-map fingerprints/ranges work at runtime |
| Trusted LineageOS signing identity | OTA certificate SPKI SHA-256 `0848ca38cb604887cea216a7f39d7dd62bad20b513cfdeff357ece4e731d6372`, from independently versioned `LineageOS/update_verifier` public key | A trust anchor for AVB; this is an OTA signing key |
| Helper | Git commit `70819893ff91488b4b40a8c6f45fde39854c6c68` in `0cwa/my-avbroot-setup`; independently check clean checkout, repository origin, commit, and compatibility manifest | Permission to trust arbitrary future helper commits |
| Helper compatibility transform | `tools/compat/avbroot_setup_compat.json` binds original `patch.py` SHA-256 `3405142ea7d056a37ac5f7ff9301fff5d4fef8519ab63f32333ff2574fd387c5` to the exact helper commit; strict checkout test exercises it | A general helper patching license or trust in unreviewed source changes |
| avbroot binary | Version 3.34.1; exact signed release ZIP SHA-256 `b1740ebf92d503cf2e81ca443afa4b615fb97ec365e170b71791ed72d9e559f8`; executable member SHA-256 `de0ed1b439175c8c358e2b2de289adc1a8947c655e3cce1598de374ab71d316b` | Correctness of current 3.34.1 care-map semantics |
| Tool publisher | Detached OpenSSH `file`-namespace signature, identity `chenxiaolong`, trusted key fingerprint `SHA256:Ct0HoRyrFLrnF9W+A/BKEiJmwx7yWkgaW/JvghKrboA`; `locks/executable-tools-v1.json`, `trust/chenxiaolong.allowed_signers` | Authenticity of an unsigned custom replacement binary |
| Other executables | afsr and custota-tool remain locked by the same immutable signature + archive/member inspection mechanism | Permission to alter their pins as part of this worklane |
| Production exception | `readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256=''` in `src/source_care_map_policy.sh` | No compatibility waiver is currently approved or active |

Any proposed replacement avbroot must have: reviewed *source* commit/tag and
patch diff; independent build/reproducibility evidence or a documented
binary-to-source provenance attestation; exact architecture and executable
bytes; archive size/SHA-256; exact contained member sizes, types, modes and
SHA-256; a **separately trusted** publisher signature/attestation and its
verification method; and deterministic failing tests for wrong identity,
wrong signature, wrong version and substituted bytes. An unsigned development
binary is not covered by the existing chenxiaolong signature, even if its
archive SHA-256 is pinned. A custom signing identity requires independent
approval and a discrete trust-policy change; do not impersonate upstream
signatures or weaken the executable bootstrap. A source Git SHA alone is
not sufficient to authenticate a tool *executable*.

Before any dependency promotion, pin the exact reviewed helper revision in
`src/declarations.sh` and its compatibility manifest together. Update the
tool lock, signed executable provenance and validator-policy review in a
separate atomic commit, including its negative tests. The normal ModOS
selection fingerprint already includes the helper Git commit and
`clear_vbmeta_flags`, but **does not currently include the avbroot
executable archive digest, a care-map semantic contract version or the
source OTA digest**. That is a remaining cache/selection risk: extend the
canonical selection schema (and downstream preflight/sidecar consumers, with
versioned migration tests) before a tool/semantic promotion so an old release
cannot be mistaken for a compatible new output. An executable lock SHA-256
must be derived from checked-in bytes, never from the latest GitHub release
API or an environment override.

## Immediate release quarantine (safe to test now)

`src/ci/pdx235_release_quarantine.sh` is a small shared, explicit temporary
policy: when `ROM_FAMILY=lineageos` and `DEVICE_NAME=pdx235`, **only**
`OUTPUT_SCOPE=local-unpublished` is acceptable. It deliberately applies to
all nightly dates, root modes and microG selections. Calls from
`enforce_output_policy` reject releases before patching. Calls from
`src/ci/publish_ota.sh` and `src/ci/publish_ota_pair.sh` reject direct
publication even if early checks were somehow bypassed. This quarantine
is **not** governed by a workflow input, source digest, `FORCE_UPDATE`,
`force-publish`, or `--allow-source-care-map-mismatch`. It can be lifted
only by a later explicitly approved and independently reviewed code change
after the gates below pass.

The original `_SOURCE_CARE_MAP_PDX235_20261009_SHA256` must stay a literal
empty readonly assignment. No unreviewed URL, release API, workflow
parameter, build environment variable, artifact name or downloaded metadata
may set it. An independently reviewed digest is only a byte identity
allowlist, not source authentication and not care-map runtime proof. Prefer
fixing the generator/verifier and passing strict source checks without an
exception. If the original source requires a narrowly scoped waiver, review
the precise meaning and residual first-boot risk in a **separate** security
change. No catch-all mismatch ignore is acceptable.

Existing `.github/workflows/build-rom.yml` uses a `local-unpublished`
output scope for `release-type=build-only`, and release steps are conditioned
on `publish && release-type != 'build-only'`. The dedicated `GH_TOKEN`
secret is withheld from build-only runs by this PR. **Residual risk:** the
shared job still has `permissions: contents: write` and inherits signing
secrets; workflow conditions are not a replacement for least privilege or a
dedicated nonpublishing build runner. Do not treat a draft PR or CI test as
permission to dispatch that workflow. A future further hardening can split
write-scoped publication into a separate job with its own explicit
read-only/build-only permissions without adding a long-lived parallel build
workflow. Current PR CI runs tests only.

## Required semantic and cryptographic acceptance (Worklanes 1–3)

The safe generator/verifier contract for the exact **authenticated** source
must satisfy the following, independently for the source, rootless output
and Magisk output:

1. Parse `care_map.pb` strictly as a single nonempty, correctly framed
   protobuf in the expected ZIP member. Reject zero/multiple/empty,
   malformed/unknown unsafe structure, duplicate/conflicting partition
   entries, impossible or empty ranges, integer overflow, out-of-bounds
   offsets and any required partition silently missing.
2. Prove the required partition **names, property IDs, fingerprints and
   block ranges** correspond to the exact target build and Android runtime
   first-boot `update_verifier` selection rules; not simply equality to
   an arbitrary reconstructed map. Handle AOSP meaningful-block/sparse
   ranges correctly, including zero-filled/unused blocks and expected
   parity between inputs and outputs. Verify the dynamic partitions
   `odm`, `product`, `system`, `system_ext`, `vendor`,
   `vendor_dlkm`. A silently skipped required partition is a failure.
3. Detect the original Sony stock Android 15 versus LineageOS Android 16
   fingerprint discrepancy. Do not silently substitute, preserve stale
   fingerprints as assumed-good, or change signed source fingerprint
   identities solely to satisfy a test. Worklane 2 must establish the
   correct runtime property mapping; Worklane 1 must implement that rule
   narrowly; Worklane 3 must prove it with authenticated and hostile
   fixtures. Any intended identity rewrite needs separate forensic proof
   that it makes the target runtime verify the intended blocks.
4. Validate each source OTA's whole-file signature with the independently
   pinned LineageOS publisher key and verify payload integrity, partition
   hashes and AVB chain as applicable. Do not infer source trust from
   keys/certificates taken from the same ZIP without independently checking
   the publisher's signing key. Source verification must fail on **any**
   non-care-map error.
5. For each output, derive/encode the expected AVB public key from the
   configured private signing key (respecting passphrase configuration),
   then run strict `avbroot ota verify` against **that key** and the
   configured output OTA certificate. Check payload/AVB integrity,
   required single nonempty `care_map.pb`, and semantic metadata.
   Never accept the output's own embedded cert/key as independent proof.
   Check rootless and Magisk **separately**; a secondary failure must
   abort the pair before creating sidecars, marking success or publishing.
6. Verify Magisk's actual boot/init_boot patch, declared
   `PREINITDEVICE=sda47` for pdx235, distinct boot images, and no Magisk
   evidence in the rootless variant. Paired outputs must share the intended
   prepared dynamic partitions, preserve selected modules (microG only
   after its own functional validation), and be individually signed and
   verified. A Magisk label, `Ramdisk: yes`, or patch-process exit code
   is not runtime-root acceptance.

All fixture and live-evidence reports must record *exact* source bytes,
helper commit, executable hash, semantic rule version, selection identity,
root mode, input/output ZIP SHA-256, map SHA-256, expected/actual partition
fingerprints and ranges, verifier invocation/result, output AVB key
identity and output OTA cert identity. Worklane 3 owns the binary/protobuf
negative conformance suite; these ModOS tests freeze the calling boundary
and quarantine, not the actual verifier implementation.

## Future **build-only** acceptance procedure — NOT RUN OR AUTHORIZED

**Prerequisites:** Worklanes 1–3 have delivered and reviewed the semantic
fix, authenticated tool provenance, negative-fixture suite and Android runtime
interpretation. Keep the production exception literal empty and this
quarantine active. Use an isolated clean checkout of the reviewed exact
dependency commits, with only independently authorized test signing
material. Never use `force-publish`, `OUTPUT_SCOPE=published`,
`publish=true`, a release workflow's scheduled trigger, or the `main`
branch. Build artifacts must remain in a protected local workspace and must
not be uploaded to a GitHub release, Custota channel, public CI artifact,
or any externally accessible endpoint.

1. Confirm the **exact October 9 source ZIP** independently matches the
   pinned SHA-256 and publisher signature. The normal LineageOS provider
   follows the *latest* nightly, so a normal manual `build-only` dispatch
   does **not** pin the October 9 source. A separate reviewed,
   input-immutable, read-only source-fixture mechanism is necessary. Never
   replace the source digest pin by using an environment override.
2. After explicit future approval to exercise the corrected toolchain,
   choose `ROM_FAMILY=lineageos`, `DEVICE_NAME=pdx235`,
   `release-type=build-only`, `publish=false`,
   `OUTPUT_SCOPE=local-unpublished`, `root-mode=both`,
   `magisk-preinit-device=sda47`, `allow-unauthorized-adb=false`.
   Start with `microg=false` to isolate the care-map and AVB boundary.
   Optionally repeat with `microg=true` *only after* the independent
   module lock, signer/package/version and runtime acceptance remain valid.
   Separate rootless and Magisk builds are also acceptable provided both
   are checked and compared.
3. Record immutable receipts and SHA-256 for both generated local ZIPs.
   Execute the Worklane 3 semantic conformance checks and strict output
   `avbroot ota verify` with the configured output key/cert for each ZIP.
   Confirm one correct `care_map.pb`, correct partition fingerprints and
   block ranges, AVB flag state, selected modules, distinct Magisk boot
   patch, and valid rootless/Magisk output pair. Compare expected versus
   observed verification results for every partition.
4. Negative-inject an unreviewed source, wrong/changed fingerprint,
   corrupt/missing/duplicated map, changed signer, failed output verifier,
   and failed Magisk secondary output. Each must **block** before any
   sidecars, release writes, update pointers or success markers. Recheck
   that `GH_TOKEN` is absent for build-only, and no asset/tag/gh-pages
   updates or artifact uploads happened.
5. Preserve CI logs, SHA-256 receipts, both verifier results and the
   nonpublishing run's exact commit/parameters for review. A passing local
   build is only static acceptance; it is **not** permission to flash or
   publish. Physical testing requires its own authorization.

Only the deterministic CI/policy tests can be run under the current
Worklane 4 authorization. The procedure above is a *future* gated
specification, not a command to start an OTA build today.

## `--clear-vbmeta-flags`: precise scope and residual risk

`src/rom_profiles.sh` sets `ROM_PROFILE[CLEAR_VBMETA_FLAGS]=true` for
LineageOS and `false` for GrapheneOS. The common patch path forwards
`--patch-arg=--clear-vbmeta-flags`; `ROOT_MODE=both` forwards
`--secondary-patch-arg=--clear-vbmeta-flags` for the Magisk ZIP too.
This clears root vbmeta header flags disabling AVB hash-tree / verification
(for a typical source header value 0x3), so the resigner can request those
checks rather than carrying an explicitly verification-disabled root
header forward. It does **not** by itself prove verified boot is enforced
by a pdx235 bootloader, prove which keys are trusted on-device, fix the
original care-map metadata, or prove first-boot `update_verifier` read
the intended partitions. The source may include public AVB test keys;
their presence must never be portrayed as production AVB authenticity.

Future acceptance must inspect each output root vbmeta flags and all
chained/hashtree descriptors, verify keys on the actual device, and gather
AVB/verity and `update_verifier` first-boot evidence from both root
modes. An unlocked bootloader and an internally consistent signed output
do not alone establish hardware-enforced verified boot. Explicitly test
both slots and OTA update persistence without bypassing first-boot
verification.

## Release gate — all required before any later exception or pin promotion

- [ ] Worklane 1: Reviewed fixed generator and verifier semantics, isolated
      commit, precise CLI/compatibility interface, test evidence for the
      exact October 9 source, output signature and first-boot behavior.
- [ ] Worklane 2: Independent pdx235 first-boot `update_verifier` runtime
      requirement analysis, correct fingerprint-property IDs, sparse-range
      rules, verification failure/skip behavior, recovery/update constraints.
- [ ] Worklane 3: Fixture manifests with independent source provenance and
      coverage for all six partitions; deterministic positive/negative
      conformance suite; wrong fingerprint, malformed map, signed-but-wrong
      output and failed second-output cases fail closed.
- [ ] Independent source publisher verification evidence is linked to
      issue #114; original ZIP SHA-256, official release identity and
      certificate public-key hash are independently reviewed.
- [ ] Replacement helper/tool has reviewed exact source commits, executable
      cryptographic provenance, artifact hash/member layout, immutable lock,
      signature validation and strict compatibility tests. Existing pins
      remain unchanged until review. No mutable URL/env digests.
- [ ] Selection identity/sidecar schema is versioned to distinguish the
      reviewed semantic toolchain and input source; cache collision or
      skip-existing-release cannot conceal a changed binary or care map.
- [ ] The ModOS and Worklane 3 CI suites pass, including negative cases.
      No test green status is represented as OTA-source/runtime proof.
- [ ] A separately approved **local-unpublished** build-only acceptance
      successfully produces and strictly verifies *both* outputs using
      authenticated inputs without publishing anything.
- [ ] With separate device-test authorization, physical pdx235 rootless and
      Magisk first boot demonstrates correct verified block reads, maps,
      successful OS boot and full feature integrity; no silent skip of
      required partitions. Verify expected AVB state and recovery behavior.
- [ ] Physical-device OTA persistence / A/B slot switch and Custota
      update cycle succeeds; Magisk manager additional setup and `su -c id`
      confirm actual root after reboot. If microG enabled, verify
      self-check, signature spoofing restriction, account behavior,
      push/location, and survival across OTA.
- [ ] Independent security review resolves ModOS #114, explicitly approves
      a separate production-gate removal/pin-promotion PR and production
      signing/release procedure. Never silently auto-promote or merge #97
      into `main` based only on Worklane 4 tests.

Until **every applicable gate** is met, the correct outcome is **no
production source exception, no trusted tool revision change, no signed
OTA release, and no publication**.

## Cross-worklane interface to return

- **Worklane 1 → 4:** versioned CLI/function contract, exact source commit
  and executable identity, strict verifier invocations, output fingerprint
  and map guarantees, handling of missing/invalid maps, secondary-output
  fail-closed result.
- **Worklane 2 → 1/3/4:** authoritative Android runtime property/range
  semantics and trace-based acceptance assertions for all six partitions;
  AVB/vbmeta flag and recovery verification requirements.
- **Worklane 3 → 4:** deterministic fixture/test command, byte-identical
  source and expected map evidence, failing mutations, both-output
  negative test results, hashed test fixtures and CI status.
- **Worklane 4 → reviewers:** permanent CI quarantine and version-pinning
  interface, nonpublishing acceptance specification, updated selection
  schema requirement and completed release-gate evidence.

References: [ModOS #114](https://github.com/0cwa/ModOS/issues/114),
[ModOS PR #97](https://github.com/0cwa/ModOS/pull/97),
[avbroot care-map issue #639](https://github.com/chenxiaolong/avbroot/issues/639),
[AOSP `update_verifier` source](https://android.googlesource.com/platform/bootable/recovery/+/master/update_verifier/update_verifier.cpp),
and [ModOS executable trust policy](./executable-tool-trust.md).
