# Dependency updates without fragile minor pins

ModOS uses its existing self-hosted **Renovate** workflow. Do not add overlapping
Dependabot managers: that would open competing PRs for the same dependencies.
Dependabot could handle Actions and Cargo, but neither bot understands ModOS's
custom Termux binary/source/adapter contract without a dedicated updater.

## Compatible versions and reproducible inputs

- Most Actions already use major tags (`@v7`, `@v3`). Keep those major selectors.
  Existing SHA-pinned Actions stay immutable; Renovate can propose a newer SHA
  and release comment without making the executed code a moving target.
- Cargo's `"1.0.99"` syntax is already a compatible range, not an exact pin.
  Keep its minimum supported version and let Renovate update `Cargo.lock` within
  that range. Do not remove the lockfile, its checksums, or `cargo --locked`.
  Pre-1.0 ranges are narrower because minor versions may be breaking changes.
- The exact Rust toolchain, vendored source, helper commit/compatibility manifest,
  executable archives/members/signers, APK signers, Magisk runtime baseline and
  Termux binary/source locks remain deliberate review boundaries. Never replace
  them with `latest`, a major-only URL, or a version edit without matching hashes.

## Routine update PRs and guarded auto-merge

Renovate groups minor/patch changes for the five ordinary Actions named in
`.github/renovate.json5` and separately groups stable (1.0 or later) Cargo
dependencies. Only these groups are eligible for PR auto-merge. Major updates,
pre-1.0 Cargo updates, digest-only/pin changes, the Renovate runner Action,
Rust toolchain Action and all custom/OTA updates remain review-only. In
particular, the existing upstream-lead and helper-lock review rules still apply.

The read-only preflight in `src/ci/renovate_automerge_policy.py` checks the
repository at the start of each self-hosted Renovate run. It permits the narrow
rules only when all of the following are already configured:

1. GitHub native auto-merge and squash merging are enabled, with `main` the
   default branch.
2. Classic branch protection requires the `test` check from the **GitHub Actions**
   app (ID 15368), and requires branches to be up to date before merging.
3. At least one approving review is required and new commits dismiss stale
   reviews. Review-bypass allowances are empty.
4. Protection also applies to administrators, and force pushes are disabled.

Otherwise, the workflow forces `automerge: false` **after all package rules**.
Renovate continues proposing updates. A missing token, 403/404, network error,
unknown protection response, or ruleset-only setup also stays proposal-only.
This matters because Renovate's `platformAutomerge` can fall back to its own
merge implementation when native auto-merge is unavailable. Merely enabling a
package rule is not a safe replacement for enforced checks and review.

The preflight uses the existing Renovate credential only for GET requests. It
does not change repository settings or token permissions, approve PRs, or bypass
protection. Do not widen a credential just to make the probe pass; leave
auto-merge off if the configured credential cannot inspect the protection.
These rules are intended for the checked-in self-hosted workflow, not an
additional Renovate App that does not run the preflight.

On November 11, 2026, GitHub reported auto-merge disabled, `main` unprotected,
and no rulesets. This change therefore prepares guarded automation but **does
not enable live auto-merging**. The maintainer must configure the prerequisites
before the next scheduled Renovate run can enable it. No repository setting is
changed by this PR.

## Termux exfatprogs: an explicit recovery path

The locked 1.4.3 package is a snapshot in a rolling APT repository, not durable
storage. Its URL returned 404 after Termux advanced to 1.4.4 on November 10.
Major-only tracking cannot make an old filename remain available. This policy
change does **not** repair that download; artifact CI and selected OTA builds
remain blocked until recovery or qualification is completed.

Choose one coherent repair:

- Recover the exact 1.4.3 package from a retained copy and verify its existing
  size, package SHA-256, extracted binary hash and transformed binary hash.
  Review a durable mirror, allowed origin, downloader host restrictions and
  associated evidence together. Keep the existing source bundle and GPL source
  publication guarantee. Do not accept a new download merely because its name
  says 1.4.3, and do not broaden the downloader to arbitrary mirrors.
- Qualify 1.4.4 (or a later compatible candidate) using signed Termux repository
  metadata and the trusted signing key. Atomically update the package version,
  size/hash, `fsck.exfat` hashes, Android ABI/RUNPATH validation, exact Termux
  recipe/build-control commit, upstream source archive and evidence. Rebuild and
  verify the deterministic corresponding-source bundle. Run the module-tool
  lock check, adapter/source tests and nonpublishing rootless/Magisk qualification
  before production promotion; retain separate device/runtime qualification.

Relevant checks include `src/ci/check_exfatprogs_artifact.py`,
`src/ci/build_exfatprogs_source_bundle.py --check-deterministic`, the pinned
helper's `module-tool.py lock verify`, and the exFAT tests in `tests/`.
Changing only the URL, omitting checks, or disabling the module is not recovery.

See [upstream maintenance](upstream-maintenance.md) and
[executable-tool trust](executable-tool-trust.md). Renovate documents
[compatible range updates](https://docs.renovatebot.com/configuration-options/#rangestrategy),
[platform auto-merge requirements and fallback](https://docs.renovatebot.com/configuration-options/#platformautomerge)
and the [force override](https://docs.renovatebot.com/self-hosted-configuration/#force).
