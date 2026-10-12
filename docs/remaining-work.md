# Remaining work and execution order

Evaluated 2026-10-12 against ModOS main `6572a6e`, feature PRs
[#96](https://github.com/0cwa/ModOS/pull/96) and
[#97](https://github.com/0cwa/ModOS/pull/97), quarantine draft
[#121](https://github.com/0cwa/ModOS/pull/121), and helper drafts
[#7](https://github.com/0cwa/my-avbroot-setup/pull/7),
[#10](https://github.com/0cwa/my-avbroot-setup/pull/10),
[#11](https://github.com/0cwa/my-avbroot-setup/pull/11), and
[#12](https://github.com/0cwa/my-avbroot-setup/pull/12).

## Established evidence

The latest comments in [#114](https://github.com/0cwa/ModOS/issues/114)
supersede its original source-authenticity gap. Read-only forensic runs
[38180526270](https://github.com/0cwa/ModOS/actions/runs/38180526270),
[38180766827](https://github.com/0cwa/ModOS/actions/runs/38180766827), and
[38181164127](https://github.com/0cwa/ModOS/actions/runs/38181164127)
report independent LineageOS publisher-signature verification, official
API digest/size matching, build-manifest revisions, and signed-image
fingerprints. This audit reviewed their recorded evidence; it did not
repeat the gigabyte-scale source acquisition.

The Sony Android 15 fingerprint is a deliberate build-property override
inside authentic Android 16 images. All six source care-map fingerprints
match the filesystem properties. AVB descriptors use the other LineageOS
fingerprint. avbroot 3.34.1 reconstructs from AVB descriptors and contiguous
ranges; Android selects care-map entries using runtime property IDs.
Therefore neither bypassing the discrepancy nor rewriting fingerprints to
make avbroot happy resolves the runtime problem.

Source authenticity and causal provenance are complete for the October 9
fixture. Correct final-output care-map verification and physical acceptance
remain incomplete. Keep #114 open for those actual blockers. Do not repeat
completed investigations as new prerequisites.

Helper #12 independently exercises #11's candidate against #10's two
required-mode suites. This audit reran that wrapper successfully: 11 nested
candidate tests, zero skips. It establishes synthetic conformance only.

## Execution queue

| Order | Owning boundary | Concrete next delivery | Completion criterion |
| --- | --- | --- | --- |
| 1 | ModOS integration / #121 | Refresh against main, retain its boot-animation default, remove redundant static contract checks. | Source/publisher/quarantine/module-selection behavior passes; strict helper compatibility and executable trust still pass. Prepared on `lineage-integration`. |
| 2 | avbroot care-map generator/verifier, with helper #11/#10 | Implement one consistent rule for source and final output using filesystem/runtime fingerprint IDs and validated block ranges. Review a deterministic-write alternative for modified partitions. | Correct October 9 source is accepted with publisher/payload/AVB authentication intact; wrong identities, unsafe ranges and corrupt signatures are rejected. Independent candidate suite passes without skips. |
| 3 | helper `patch.py` output postflight | Integrate the reviewed verifier once; re-extract each signed rootless/Magisk output and bind image hashes and ranges to that output's verified payload. | Expected configured AVB signer and OTA certificate are verified for both outputs; secondary failure emits no sidecars/report/success. |
| 4 | ModOS build/publication workflow | Prepare a build-only runner with read-only GitHub permissions and test signing material; resolve #97/main integration using the prepared branch. | Nonpublishing fixture run isolates care maps with microG off, then exercises microG on; each actual output passes step 3. Existing quarantine and empty source exception stay in place during development. |
| 5 | pdx235 device acceptance | Observe runtime properties, effective verity/snapuserd verification and successful slot marking; test both root modes and a Custota OTA cycle. | No unintended partition skip; root runtime setup works; microG self-check, restricted spoofing, account/push/location behavior and OTA persistence work. Requires the actual device. |
| 6 | Release integration | Review evidence and promote dependencies in order: helper feature, ModOS microG, then explicit publication/schedule changes. | #114's output/runtime blockers are resolved; quarantined publication is deliberately lifted in a reviewed change. microG schedule remains off until device acceptance. |

Steps 2 and 4's runner preparation can proceed independently. Step 3 depends
on step 2; actual builds depend on steps 2–4; device acceptance depends on
verified outputs. Keep research #10/#11 and ephemeral validation #12 outside
production until their relevant implementation and tests are consolidated.
Do not repeatedly create new integration branches just to recount passing
synthetic tests.

## Avoid making range policy unnecessarily restrictive

The research `D ⊆ S` rule is a sufficient condition for preserving the original
care ranges, not a general requirement that Android imposes on all patched
OTAs. New microG APKs/XMLs may allocate blocks outside the source's cared
ranges. Treat rejection under that rule as an unsupported case for the
prototype, not proof the module cannot be integrated safely.

Code inspection of avbroot v3.34.1 (source commit
`e805505affb042ed5ecb2e74cf5da3b465fb4e5d`) found:

- `format/payload.rs::compress_image` emits `REPLACE_XZ` operations covering
  the entire modified raw image, with per-operation hashes and a partition
  hash. `cli/ota.rs` uses it for modified images.
- Unmodified image operations can be copied from the original payload;
  their sparse/discard behavior must be considered separately.

A candidate simpler rule is to preserve authenticated sparse care ranges
for unchanged partitions and generate ranges within the AVB-protected data
extent for modified partitions only where the final signed payload proves
deterministic writes. Confirm complete operation coverage, exclude unsafe
`DISCARD` gaps, read fingerprints from final filesystem properties, and
verify the actual reconstructed output. Contiguous coverage for every
partition must not be assumed from the modified-image code path. This is a
design direction, not implemented or approved production verification.

The manifest-pinned Android `update_verifier.cpp` at
`acd99f7c48e0229eaedd40aa3e2adcbabe2dc283` confirms: fingerprint mismatches skip
entries; an empty accepted map can skip verification; disabled/missing
verity mode can mark without verification; a successful snapuserd status
can satisfy verification without explicit `ReadBlocks` calls. Device
acceptance should recognize the supported snapuserd path rather than demand
one particular log or per-partition read trace in every configuration.

## Check ownership and consolidation

Keep checks at the boundary that actually enforces the invariant:

- Executable lock/bootstrap tests own tool hashes, signer identity and
  authenticated execution. Helper compatibility tests own source revision,
  pristine/transformed source hash and CLI compatibility. Do not repeat
  these literal pins in a care-map integration test.
- Source policy and publisher tests own the disabled immutable exception,
  independently trusted certificate, archive-member correspondence and
  hostile input rejection. Test actual policy behavior, not quoting style
  or source text mutations.
- Module-selection tests own caller arguments, root pairing and failure
  before sidecars. Quarantine tests own scoped refusal at early and direct
  publisher boundaries. Two representative requests replace a Cartesian
  product of variables the gate never reads.
- Existing workflow tests own the remaining workflow-only credential and
  publication conditions. Keep these small: do not freeze step names,
  exact source snippets, assertion counts or complete workflow inventories
  again in a second suite.
- Independent helper conformance tests own map parsing, fingerprint/range
  semantics and negative candidates. Real signed-output verification owns
  cryptographic authenticity. Device acceptance owns runtime behavior.

The duplicate 220-line `pdx235_integration_contract_test.py` is removed in
this integration. Behavioral coverage and the authoritative compatibility
and lock suites remain. Quarantine cases shrink from 65 to 35; coverage of
publisher rejection, stale primary/secondary output, malformed paths,
sidecars, symlinks and direct publishers is retained.

## Other outstanding work

- **Stock updater removal (#96):** separate feature; refreshing against main
  has the same boot-animation-default test conflict. Its path-based removal
  and optional XML policy are already tested. Keep its selection identity
  independent. LineageOS/pdx235 acceptance depends on the same care-map
  fix; GrapheneOS review can proceed separately. Do not pile it into the
  first microG acceptance run.
- **Helper execution trust:** `docs/executable-tool-trust.md` still records
  the helper's PATH execution boundary and planned trusted `ToolRunner`.
  Close that host-execution gap through the helper boundary; adding more
  file-existence or source-text checks will not close it.
- **Build-only credentials:** #121 withholds the separate `GH_TOKEN`, but
  its shared job still requests `contents: write` and production signing
  secrets. Do not call that least-privilege isolation. Separating the runner
  needs a deliberate workflow change, preserving GrapheneOS publication.
- **F-Droid:** default-off wiring exists but no reviewed production lock or
  profile/source-delivery publication path exists. Keep it separate from
  microG, since the helper currently accepts one locked bundle per call.
- **ROM-neutral names:** the `GRAPHENEOS` configuration arrays remain a
  documented naming cleanup. It is not a release blocker and should not be
  mixed into care-map work.
- **Evidence housekeeping:** after integration review, reconcile #113 and
  #115 with the feature-branch fixes already implemented. Their remaining
  issue state should distinguish code completion from main promotion and
  real-output/device acceptance. Do not close #114 based on synthetic CI.

No OTA was built, signed, flashed or published during this evaluation.
