---
name: modos-module-authoring
description: Create or change local in-image Python adapter modules for ModOS, including their build selection and integration. Use when implementing a module that injects files or policy into patched images; third-party modules and locked artifacts follow their own acquisition path.
---

# ModOS module authoring

This skill covers local Python adapters copied into the pinned `my-avbroot-setup` helper for image injection. It does not cover acquiring, updating, or packaging third-party modules or locked artifacts; follow their dedicated lock and verification paths instead.

## Adapter contract

Use the current `LegacyCliModule` API: define `NAME`, `add_args`, `from_args`, `requirements`, and `inject`. Keep helper imports inside the adapter factory or another lazy boundary so the module remains importable on its own before the helper is fetched. Declare every touched boot and ext partition in `ModuleRequirements`, and set `selinux_patching` to match whether the adapter changes SELinux policy.

Follow current helper registration in [`src/util_functions.sh`](../../../src/util_functions.sh), including its safe-path checks, idempotent registry updates, and dummy module artifacts. The pinned helper uses `lib.modules.registry.legacy_cli_module_types`; do not copy the older `src/debug_module_setup.sh` / `src/debugmod.py` registration pattern or rely on the older debug module API.

Use [`src/boot_animation.py`](../../../src/boot_animation.py) and [`src/disable_system_updater.py`](../../../src/disable_system_updater.py) as current adapter examples. Keep image-path validation and transformation logic testable without importing helper-only modules.

## Selectable modules

When a module is selectable, update the full selection contract together: typed key in [`src/config_schema.sh`](../../../src/config_schema.sh), default/declaration behavior in [`src/declarations.sh`](../../../src/declarations.sh), CLI/config resolution and module setup in [`src/util_functions.sh`](../../../src/util_functions.sh) (and the relevant entrypoint in [`src/main.sh`](../../../src/main.sh)), ROM-profile behavior in [`src/rom_profiles.sh`](../../../src/rom_profiles.sh), and the module-selection identity in [`src/ci/selection_variant.sh`](../../../src/ci/selection_variant.sh). If GitHub Actions or scheduled builds expose the toggle, update their relevant workflow input/environment and schedule configuration too. Keep the example configuration [`env.toml`](../../../env.toml), targeted tests under `tests/`, and user-facing documentation in sync. A toggle omitted from the selection fingerprint can make distinct builds appear identical.

For focused examples of existing contracts, see [`tests/boot_animation_registration_test.sh`](../../../tests/boot_animation_registration_test.sh), [`tests/module_selection_test.sh`](../../../tests/module_selection_test.sh), [`tests/config_schema_test.sh`](../../../tests/config_schema_test.sh), and [`tests/rom_contract_test.sh`](../../../tests/rom_contract_test.sh).
