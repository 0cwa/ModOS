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

The existing delivery routes are distinct:

- Signed module ZIPs retain their upstream verification and helper CLI path.
- F-Droid and microG use helper catalog resolution and locked artifact bundles.
- Local adapters express reviewed image changes and target preconditions.
- AFSR is a locked host executable, not a catalog module ZIP.

Keep local adapter code and ROM/device defaults in ModOS. Add only reusable
mechanisms to the helper. Preserve existing helper CLI ordering and schema
migration when adding an optional interface. Keep a bounded, checked consumer
adaptation until an equivalent native interface is pinned and tested; ownership
of the fork alone is not evidence that a guard can be removed.

For a selectable module, update the index and the actual consumers together:
typed configuration, workflow defaults/forwarding, acquisition and preparation,
ROM applicability, helper arguments and output identity. Include payload,
adapter, lock and output-affecting policy identities where relevant. A module's
health coverage must be registered or its absence recorded explicitly; neither
is a claim of device acceptance.

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
