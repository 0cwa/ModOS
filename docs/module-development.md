# Module development and fork boundaries

ModOS selects and qualifies features for supported ROMs. The pinned
`0cwa/my-avbroot-setup` helper owns the canonical catalog and resolver. Read
`VERSION[AVBROOT_SETUP]` in `src/declarations.sh` and the helper contract before
working on its source; an older local helper checkout is not authoritative.

The existing helper catalog lives in `lib/modules/manifests/`, with its schema,
loader and migration in `lib/modules/catalog.py`. Two existing manifests are
schema v2; older v1 manifests migrate to the same canonical model. Trust roots,
artifact locks, dependencies and capability declarations belong to that model.
Do not introduce another ModOS catalog format for the same fields.

## Selection policy

[`tools/modules/selection.json`](../tools/modules/selection.json) indexes local
selection policy and names the delivery route, helper identity, configuration,
workflow, health and fingerprint consumers. It is a checked index, not a new
resolver or permission to execute an adapter. The helper catalog remains the
owner of module semantics and artifact trust.

The current delivery routes are distinct:

- Signed module ZIPs retain their upstream verification and helper CLI path.
- F-Droid and microG use helper catalog resolution and locked artifact bundles.
- Shizuku and exfatprogs use ModOS schema-v2 catalog manifests, reviewed lock
  records, and `catalog-legacy` adapters. The helper validates the selection
  through its normal catalog, compatibility, lock, and artifact paths; both
  adapters consume payloads through the verified artifact context. Their API
  24 minimums are build/install evidence: Shizuku's comes from the locked
  [v13.6.0 source tag's `minSdk`](https://github.com/RikkaApps/Shizuku/blob/v13.6.0/build.gradle),
  and exfatprogs' from the locked executable's Android ELF note recorded in
  [`locks/exfatprogs-termux-v1.4.3-provenance.json`](../locks/exfatprogs-termux-v1.4.3-provenance.json).
  Device startup, filesystem behavior, and OTA persistence remain separate
  acceptance checks and stay marked experimental.
- Boot animation, updater removal, and fsck policy use explicitly registered
  `legacy-cli` adapters from a digest-pinned extension staged outside the helper
  source checkout. The local files remain in ModOS.
- AFSR is a locked host executable, not a catalog module ZIP.

Keep local adapter code and ROM/device defaults in ModOS. Add only reusable
mechanisms to the helper. The extension contract is checked in
[`tools/modules/adapter_extension.json`](../tools/modules/adapter_extension.json)
and built by [`build_adapter_extension.py`](../tools/modules/build_adapter_extension.py).
It pins each exact source and manifest digest, rejects modified/untracked inputs,
and writes only to `WORKDIR` outside the helper checkout. The helper's native
`--adapter-extension` and `--release-url` options are covered by the exact
revision in the compatibility contract; the consumer validator checks pristine
source and never rewrites it. Preserve existing legacy CLI argument order and
catalog schema migration.

For a selectable module, update the index and the actual consumers together:
typed configuration, workflow defaults/forwarding, acquisition and preparation,
ROM applicability, helper arguments and output identity. Include payload,
adapter, lock and output-affecting policy identities where relevant. A module's
health coverage must be registered or its absence recorded explicitly; neither
is a claim of device acceptance.

The output identity includes the extension builder, registration index and
locked-profile composer when any extension adapter or catalog module is enabled. This is an intentional fingerprint migration:
the new identity binds registration policy and source digests that were
previously absent from some local-adapter selections.

The consistency check intentionally understands the current explicit shell and
workflow declarations. If those declarations change shape, update the checker
and its mutation tests together. It does not parse arbitrary shell semantics or
replace behavioral policy tests.

## Validation and evidence

Before committing selection changes, run:

```sh
python3 tools/modules/check_selection.py
python3 tests/module_selection_contract_test.py
agent-heavy -- bash tests/module_selection_test.sh
agent-heavy -- bash tests/typed_config_resolver_test.sh
agent-heavy -- bash tests/workflow_contract_test.sh
```

Also run the affected adapter, acquisition, profile and output-verification
checks. For local Flatpak/host tool combinations, set `TMPDIR` to a task scratch
directory visible to both interpreters and host tools. Do not treat a path
translation failure as proof that the relevant test passed.

Record the commit, exact command, input digest, tool identity and concise result
when validation informs a maintained compatibility decision. Retain text
receipts; do not retain copied build trees or transient `/tmp` paths as the only
evidence. Distinguish fixture/host validation, built output and device runtime
acceptance. A module's presence in a catalog proves none of the latter.

## Root modules and Android build manifests

Keep the broader catalog direction: explicit adapters for image effects, APKs,
root-framework runtime packages and source-built Android components. Installing
a runtime module and translating it into an offline image change are separate
capabilities. Inspect module archives without running their installers. Declare
unsupported hooks rather than dropping their behavior.

A Repo manifest pins source projects; the configured Android build determines
dependencies and outputs; reviewed integration rules determine what ships.
Reuse bounded source-build recipes where possible, without presenting source
manifest parsing as generic build or integration support.

## avbcompose orientation

Defer avbcompose implementation during this cleanup. Its source/build/installed/
compatibility distinctions and canonical-model ownership remain useful design
references. Its own [roadmap](https://github.com/0cwa/avbcompose/issues/1) and
[schema issue](https://github.com/0cwa/avbcompose/issues/5) remain authoritative;
ModOS does not claim interoperability with an unimplemented contract. Reconsider
a shared composer after concrete consumers establish a reusable interface.

See [upstream maintenance](upstream-maintenance.md) for exact pins, fork seams
and conditions for retiring local changes.
