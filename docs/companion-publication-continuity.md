# Companion identity on the pdx235 update feeds

The canonical rootless and Magisk feeds already advertise OTAs containing the
ModOS-signed microG Companion. Returning those feeds to an official-signed
Companion would change the installed package's signer and lower its version
code from 84022635 to 84022634. Publication must not silently perform that
transition.

## Observed published pair

On November 10, 2026, the read-only review found both `gh-pages` canonical files
(`rootless/pdx235.json` and `magisk/pdx235.json`) pointing to the `d349d72` assets
in [release 20261106](https://github.com/0cwa/ModOS/releases/tag/20261106).
[Workflow run 42754348737](https://github.com/0cwa/ModOS/actions/runs/42754348737)
built, verified and published this pair with `MICROG_SPOOF_PATCH=true` and helper
`902ed62e63f6d7d7d332a07619d75409d17fe514`. Both output cryptographic verification
phases completed successfully. The retained diagnostic report explicitly says
runtime validation was not tested.

The [legacy receipt](../locks/companion-publication.json) binds each exact OTA
and selection asset by URL, GitHub asset ID and SHA-256, with the build commit,
locked Companion APK digest and signer provenance. These receipts establish
the previous publication identity. They do not establish Android migration,
physical-device boot, billing or source-build reproducibility.

## Publication check

The existing pinned helper patch-report API binds its locked module
report to the exact primary and secondary OTA filenames and SHA-256 values
only after every output has passed verification. New LineageOS pdx235 selection
metadata takes the Companion identity from that report and requires its output
binding. The guard reopens the report and recomputes these fields, so a stale
report cannot relabel a different OTA. An absent module is recorded as `null`.

Before release upload, the guard resolves one immutable `gh-pages` commit,
reads both canonical feeds and their `XQ-DC72` aliases, and requires each alias
to match its canonical file. It resolves the exact published selection asset
through GitHub's release API, checks its digest and its binding to the OTA,
then compares the old and incoming Companion signers. Older metadata is
accepted only for the exact pair in the checked-in legacy receipt.

The actual upload path and release tag must match the guarded selection,
and the proposed feed must point to that exact OTA and signature. Both feed
publishers pass their validated paths explicitly and require uploaded OTA,
selection and signature digests to match the local files. Direct alias
publication and non-Lineage publication to the protected pdx235 feed fail.

Before touching either pointer, the publishers fetch the branch and refuse
a changed snapshot or a stale local branch. The existing `gh-pages` workflow
concurrency group continues to serialize publication jobs. The source-first
exfatprogs publisher also checks before any release mutation when called directly.

Same-signer updates remain possible. A signer change, removal, unknown legacy
identity, malformed evidence, missing feed or failed GitHub request stops
publication. This guard has no environment flag, command-line switch or
`force-publish` bypass for migration. Any future signer migration needs a
separate reviewed contract and Android migration evidence. The guard does not
edit existing releases, feeds or the schedule, and does not qualify device
behavior merely because a publication passes.
