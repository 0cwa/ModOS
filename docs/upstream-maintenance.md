# Keeping ModOS close to upstream

ModOS follows [PixeneOS](https://github.com/pixincreate/PixeneOS) for the common
build structure and integrates pinned components from their maintained upstream
projects. Local code should express a ModOS requirement at its owning boundary,
with a clear reason to remove it when upstream provides the same behavior.

## Current boundaries

| Boundary | Local requirement | Path toward a smaller fork |
| --- | --- | --- |
| ROM profiles and release entry points | Device selection, publisher trust, signing and publication policy for GrapheneOS and LineageOS. | Keep the shared build workflow and typed profile configuration; consume upstream build fixes there instead of copying the pipeline per ROM. |
| Helper compatibility transform | Custota update metadata needs the actual release URL. | Use an upstream helper option for that URL when available, then remove the source transform. |
| Build diagnostics | Distinguish source-OTA warnings from output verification, and reject MSD's emulator/debug HAL fallback on production inputs. | Keep reporting in the consumer. Retire the MSD-specific fallback guard once a reviewed upstream implementation supports explicit HAL-domain selection, rejects unsupported production policies, and is validated for each supported device. |
| Local image adapters | Optional ModOS additions such as boot animation, updater removal, Shizuku and exFAT support. | Use the helper's module API; contribute generally useful extensions upstream rather than maintaining another patch engine. |
| Guarded avbroot source engine | Signed AVB-header enforcement and expected care-map verification. | Retain the measured checks until an upstream release satisfies those same authentication and map invariants. |
| Vendored sepatch closure | A read-only query checks the exact allow bit after policy serialization. | Replace the local delta when upstream supplies equivalent read-only validation. Retain source provenance and licenses while it is vendored. |

The current consumer-side helper transform changes only the release-URL argument
in `patch.py`. The old module `Path` import workaround is retired. Production and
the explicit care-map development profiles use one
[compatibility manifest](../tools/compat/avbroot_setup_compat.json), so a helper
update cannot leave a duplicate development manifest behind. Development still
requires its existing device and unpublished-output scope, and refuses missing,
uncommitted or modified manifest bytes.

The [compatibility transformer](../tools/compat/avbroot_setup_compat.py) checks the
repository, exact revision, original blob hash, working-tree state and source
shape before applying its bounded change. It compiles staged Python and preserves
idempotence and rollback. A new upstream version is a deliberate contract update,
not permission to apply the old patch to an unknown source tree.

## Updating dependencies

1. Compare the upstream change with the local requirement it might replace.
   Review the actual source or release artifacts; a higher version is not proof
   that a local compatibility or authentication check is obsolete.
2. Update the immutable helper contract and consumer pin together. Keep the
   explicit pdx235 rootless helper qualification, executable locks, signer
   bindings and module locks coherent with the selected inputs.
3. Remove a local workaround when the replacement is qualified. Avoid keeping
   both implementations or creating another copied manifest for an identical
   contract.
4. Include any new output-affecting local policy or adapter in the build
   selection identity. A cached successful artifact must not conceal a changed
   compatibility rule.
5. Exercise behavior at the owning boundary: compatibility refusal and
   idempotence, source authentication, output verification, or publication
   refusal. Prefer those checks over repeated pin inventories and source-text
   assertions across unrelated tests.
6. Record what was validated and what still needs a device. Update current
   documentation without rewriting dated evidence as if later fixes were
   already present in an earlier build.

## MSD qualification remains a device task

A source-policy mapping can identify a candidate USB gadget HAL domain. It does
not establish the running process domain or demonstrate that MSD can coordinate
with the HAL on a physical device. Qualification needs the actual process label,
policy evidence and functional MSD behavior on the supported ROM/device pair.
Do not replace a missing `hal_usb_gadget_impl` with `su`, another guessed domain,
or a broad type attribute merely to obtain a passing build.

MSD acquisition and policy generation remain upstream responsibilities. ModOS's
diagnostic check prevents a recognized unsupported fallback from being accepted
as a successful production result; it does not repair or certify that device's
MSD compatibility. Source AVB warnings, final output verification and hardware
acceptance remain separate results.

See [executable-tool trust](executable-tool-trust.md),
[care-map acceptance](pdx235-care-map-integration.md),
[remaining work](remaining-work.md), and the
[sepatch delta](../src/exfatprogs_sepolicy/vendor/sepatch/MODOS_PATCH.md)
for the existing evidence and the checks that a future upstream replacement
must preserve.
