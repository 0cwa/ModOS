# Keeping ModOS close to upstream

ModOS follows [PixeneOS](https://github.com/pixincreate/PixeneOS) for the common
build structure and integrates pinned components from their maintained upstream
projects. Local code should express a ModOS requirement at its owning boundary,
with a clear reason to remove it when upstream provides the same behavior.

## November 10, 2026 upstream sync

The five PixeneOS commits through `7b8a012f8a86f5076bd43ac986ccd00a4e6f0415`
are integrated with AFSR 2.1.0, official avbroot 3.39.0, Custota/tool 6.9,
MSD 2.6, and the immutable Renovate 46.3.13 action. The executable lock and
validator were updated together after verifying the unchanged publisher signer,
archive digests and complete executable layouts. See the
[source reviews and host qualification receipt](evidence/upstream-tools-20261110.json).

The maintained helper already contains its official upstream head `394725b`.
Its existing guarded avbroot 3.37.1 source engine remains in use on paths that
require expected-care-map and AVB authentication checks. Updating the official
host executable does not retire that local verification boundary.

MSD 2.6 adds a MediaProvider FUSE permission, but retains the same USB HAL lookup
and unsupported `su` fallback. The production fallback guard and the disabled
pdx235 selection therefore remain necessary. Host and synthetic tests do not
establish physical-device compatibility.

## Current boundaries

| Boundary | Local requirement | Path toward a smaller fork |
| --- | --- | --- |
| ROM profiles and release entry points | Device selection, publisher trust, signing and publication policy for GrapheneOS and LineageOS. | Keep the shared build workflow and typed profile configuration; consume upstream build fixes there instead of copying the pipeline per ROM. |
| Native helper options and extension | Custota metadata needs the release URL; ModOS needs explicit hash-bound external adapter registration. | Keep the additive options pinned and tested. Consider each generic helper change for upstream separately; preserve the consumer boundary until an equivalent interface is reviewed. |
| Build diagnostics | Distinguish source-OTA warnings from output verification, and reject MSD's emulator/debug HAL fallback on production inputs. | Keep reporting in the consumer. Retire the MSD-specific fallback guard once a reviewed upstream implementation supports explicit HAL-domain selection, rejects unsupported production policies, and is validated for each supported device. |
| Local image adapters | Optional ModOS additions such as boot animation, updater removal, Shizuku and exFAT support. | Use the helper's module API; contribute generally useful extensions upstream rather than maintaining another patch engine. |
| Guarded avbroot source engine | Signed AVB-header enforcement and expected care-map verification. | Retain the measured checks until an upstream release satisfies those same authentication and map invariants. |
| Vendored sepatch closure | A read-only query checks the exact allow bit after policy serialization. | Replace the local delta when upstream supplies equivalent read-only validation. Retain source provenance and licenses while it is vendored. |

The native contract pins helper
`21b8318ab43908dbc6d9c797cffabf99a86f9d5b`, a reviewed descendant of the exact
repaired helper commit `b553f46a9b94fff3960617830b6072b0f8b054b5`. The intervening
helper changes add the extension interface, native release URL, source-byte
loading hardening, and structured locked-adapter results; legacy module argument
order and default update-info behavior remain covered by helper tests. The helper
comparison is documented against immutable upstream base
`394725b276a585bc93443dd49438b5ad3ae93588` in the helper's dated
[`upstream-maintenance.md`](https://github.com/0cwa/my-avbroot-setup/blob/21b8318ab43908dbc6d9c797cffabf99a86f9d5b/docs/upstream-maintenance.md);
ModOS does not duplicate its delta-report script.

Production and the explicit care-map development profiles use one
[compatibility manifest](../tools/compat/avbroot_setup_compat.json), so a helper
update cannot leave a duplicate development manifest behind. Development still
requires its existing device and unpublished-output scope, and refuses missing,
uncommitted or modified manifest bytes.

The [native helper validator](../tools/compat/avbroot_setup_compat.py) checks the
effective repository origin, exact revision, `patch.py` blob hash, current source
bytes, and a clean checkout including ignored files. It does not edit helper
source. `patch.py --help` must expose both native options before OTA patching.
ModOS keeps the Python environment in `WORKDIR/venvs/my-avbroot-setup` and sets
`PYTHONDONTWRITEBYTECODE=1` on helper invocations so repeated setup does not
dirty the pinned source checkout. The validator still rejects all untracked or
ignored helper files; generated runtime state belongs outside that checkout.

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
