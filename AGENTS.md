# ModOS ownership and module changes

- The pinned helper owns the canonical module catalog, IDs, dependency resolution, trust roots and artifact locks. Read the exact helper revision from `VERSION[AVBROOT_SETUP]` in `src/declarations.sh` and the checked helper contract, not a stale local checkout; its authoritative catalog is `lib/modules/manifests/` at that pinned revision (catalog loader/schema: `lib/modules/catalog.py`).
- ModOS owns selection policy, supported ROM/device rules, output identity, signing and publication. PixeneOS compatibility and helper compatibility are maintained as bounded soft forks; do not move product policy into the helper catalog.
- `tools/modules/selection.json` is the local selection index. It references helper IDs where used and describes ModOS delivery routes; it is not another helper catalog.
- For a module change: update typed config, its workflow toggle, ROM/profile rules, helper argument or delivery route, selection fingerprint, and health coverage where supported. Add a focused checker fixture and keep selection identity coverage for every output-affecting choice.
- Relevant checks: `python3 tools/modules/check_selection.py`, `python3 tests/module_selection_contract_test.py`, `bash tests/module_selection_test.sh`, `bash tests/typed_config_resolver_test.sh`, `bash tests/workflow_contract_test.sh`.
- Locked F-Droid and microG artifacts use helper catalog resolution. Other entries may use reviewed local adapters or signed ZIP paths. Never infer installer/runtime support from catalog metadata alone.

See [module development](docs/module-development.md) for delivery routes, evidence requirements, and the deferred avbcompose direction. Keep public helper changes additive and independently upstreamable; do not place ModOS ROM/default policy in generic helper mechanisms.
