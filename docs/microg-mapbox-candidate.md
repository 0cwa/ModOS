# microG Mapbox native-loader candidate

## What this prepares

This isolated build recipe backports microG's
[native-library fix, `dd9fe374bbd16c42877046dbab18baf48830ee4a`](https://github.com/microg/GmsCore/commit/dd9fe374bbd16c42877046dbab18baf48830ee4a)
onto the currently reviewed `v0.3.17.252432` release, exact commit
`32bc8954ff872d0e1a05ddfae81561b6dbecef69`. It produces an **unsigned GmsCore
candidate for review**, separately from ModOS's normal ROM builds.

Upstream [issue #3882](https://github.com/microg/GmsCore/issues/3882) describes
Android 17 rejecting writable native libraries loaded into apps targeting SDK
37. The subsequent Mapbox initialization fails with `UnsatisfiedLinkError`.
[A Skånetrafiken report](https://github.com/microg/GmsCore/issues/3882#issuecomment-6173209059)
explicitly identifies that app as affected. Upstream
[PR #3883](https://github.com/microg/GmsCore/pull/3883) merged on November 3, 2026.
The upstream [build at the fix commit](https://github.com/microg/GmsCore/actions/runs/41674953754)
succeeded. That result does not validate this backport or its behavior on a
ModOS device.

As checked on November 10, the latest published upstream release remains
[`v0.3.17.252432`](https://github.com/microg/GmsCore/releases/tag/v0.3.17.252432),
published September 29. ModOS already uses its official APKs. The native-loader
fix is 25 commits after that release; most intervening changes concern the
document scanner. The candidate applies only the exact six-line fix, preserving
upstream authorship, rather than upgrading the entire unreleased source tree.

The fix affects three loaders: Mapbox, VTM, and Conscrypt. Each deletes an old
cached library before replacing it and makes the result read-only before
`System.load`. The delete is needed because the previous cached file can now be
read-only. Calling `setReadOnly` outside the extraction branch also repairs
already cached writable libraries whose version stamp still matches.

## Build and evidence

The recipe lives in [`tools/build_microg_candidate.py`](../tools/build_microg_candidate.py).
Its [candidate lock](../locks/microg-mapbox-candidate.json) records the release
commit, original patch SHA-256, upstream fix commit, Gradle wrapper hashes,
Gradle distribution checksum, build target, and expected APK identity.
It does not read or update the production helper's microG artifact lock.

To prepare sources without an Android SDK:

```sh
python3 tools/build_microg_candidate.py \
  --prepare-only --output .tmp/microg-mapbox-prepared
```

An existing local GmsCore clone can supply Git objects using
`--source-repository /path/to/GmsCore`. Its release tag must still resolve to the
locked commit. The script clones into its own directory and does not modify the
supplied checkout. A moved tag, changed patch, or unexpected Gradle wrapper is
rejected. Existing output directories are never removed or overwritten.

For compilation, use **JDK 17**, Android platform **35**, and Android build-tools
**35.0.0**, with `ANDROID_HOME` set to that SDK. The builder uses `JAVA_HOME` when
set, matching the Gradle wrapper's selection. Install SDK packages with the
usual Android SDK manager, then run:

```sh
python3 tools/build_microg_candidate.py \
  --output .tmp/microg-mapbox-built
```

The exact Gradle target is
`:play-services-core:assembleMapboxDefaultRelease`. The script checks the APK's
`com.google.android.gms` package, expected versionCode `252432035`, and ARM64
Mapbox native library. That versionCode is upstream's automatic development
increment after the release; the recipe does not invent a replacement code.
Signed output is rejected. The result contains:

- `com.google.android.gms-unsigned.apk`, after successful compilation and checks;
- `candidate.json`, recording base, patch, prepared source commits/trees,
  toolchain identity, APK hash/size and nonproduction status;
- `gradle.log`, for build diagnosis;
- `src/`, the exact prepared checkout.

`--prepare-only` creates a source receipt with state `source-prepared`, without
claiming an APK was built. Build failures do not publish a success receipt or a
candidate APK under the requested output path. The final compiler diagnostics
are included in the error output.

Existing CI runs the offline preparation/acceptance tests. An additional,
nonpublishing job compiles this candidate on same-repository pull requests whose
branch begins `candidate/microg-mapbox-`. It uses no repository signing secrets
and retains only the unsigned APK, receipt and build log as a short-lived CI
artifact. It does not publish releases or call the normal OTA patching workflow.

The source commits are deterministic: the backport preserves the original
author date and fixes the committer identity/date; a separate, fixed metadata
commit adds Gradle's distribution checksum. The upstream wrapper JAR is pinned
by its actual repository hash; its version need not equal the downloaded Gradle
distribution version. The distribution checksum was retrieved from
[Gradle's official checksum endpoint](https://services.gradle.org/distributions/gradle-8.13-bin.zip.sha256).
This does **not** yet claim byte-for-byte reproducible APKs: Maven dependencies,
JDK patch version and the SDK/toolchain still require a complete reproducibility
review and an independent build comparison.

The candidate uses upstream's default service configuration. CI supplies no
Mapbox/Stadia service credentials. A later functional candidate must qualify
its map data configuration as well as the native-library fix; compiling alone
does not prove that map tiles, accounts, location, or other services work.

The initial [source preparation receipt](evidence/microg-mapbox-source-20261110.json)
records two identical preparations of the real pinned release and clean source
trees after applying this backport. Ten offline candidate tests and the existing
workflow contract checks passed. After provisioning a verified Temurin JDK 17,
actual Gradle `help` and `buildEnvironment` completed successfully. Although the
source declares Kotlin/Compose plugin `1.9.22`, Wire `6.4.6` brings Kotlin plugin
`2.2.21`, whose BOM also selects Compose plugin `2.2.21`. A missing POM for the
lower declared version is therefore not a configuration failure. No compiler
upgrade or compatibility patch was needed.

The [hosted candidate build](https://github.com/0cwa/ModOS/actions/runs/42765104829)
then completed successfully at recipe commit `ef169c54b8d0f0e56d047944ab1eeff57810682f`.
The unsigned APK is `107824512` bytes, SHA-256
`d6fc67452a1dbf43ce052ca88485be1bb828fab5662b944ee49b870491b602b4`, with the
expected package and versionCode. The builder accepted its release/unsigned
status and ARM64 Mapbox library, and emitted the same prepared commit/tree as
the source-only preparations. The [build receipt and review evidence](evidence/microg-mapbox-build-20261110.json)
record the exact workflow, source, toolchain, APK and retained artifact identities.
The artifact ZIP was downloaded and its checksum independently matched GitHub's
recorded digest. Physical-device and Skånetrafiken acceptance remain outstanding.

An independent review also found that Git index flags could hide a changed
tracked source file from ordinary status output. The follow-up rejects both
`assume-unchanged` and `skip-worktree` before source acceptance and after building.
Eleven candidate tests now pass, including real Git regressions for both cases.
That follow-up has its own hosted CI run; it does not change the prepared microG
source or the native-loader patch.

## Why this is not yet a production replacement

The current helper's
[`microg` adapter](https://github.com/0cwa/my-avbroot-setup/blob/b553f46a9b94fff3960617830b6072b0f8b054b5/lib/modules/microg.py)
and
[artifact lock](https://github.com/0cwa/my-avbroot-setup/blob/b553f46a9b94fff3960617830b6072b0f8b054b5/locks/microg-v0.3.17.252432.json)
require exact official APK bytes, package versions, and the official microG
certificate SHA-256:

```text
9bd06727e62796c0130eb6dab39b73157451582cbd138e86c468acc395d14165
```

The reviewed LineageOS
[`MicrogUtils.isMicrogSigned`](https://github.com/LineageOS/android_frameworks_base/blob/1df617a1093e41562fd1f43f27de0cfb4af817e5/core/java/com/android/internal/content/MicrogUtils.java)
also checks the exact official certificate and package identity before granting
its restricted signature-spoofing behavior. Signing a locally built GmsCore
with another key does not satisfy that contract. An APK with a different signer
also needs a deliberate installed-package/data migration design. This recipe
does not introduce another trusted certificate, re-sign an official APK, or
change framework spoofing rules.

PR [#173](https://github.com/0cwa/ModOS/pull/173) concerns a separately signed
Companion and may provide useful future build plumbing. Its Companion behavior
and framework policy should be reviewed independently; it does not by itself
qualify a locally signed GmsCore or this crash fix.

The smallest production path is a new **officially signed upstream release**
containing this commit. When available, verify the actual release artifacts and
their signer, update the helper lock/adapter version and permission review, then
advance ModOS's helper pin and release selector together. The normal
[upstream maintenance contract](upstream-maintenance.md) applies. If source-built
GmsCore is still desired first, a separate change must define its signer,
restricted framework acceptance, upgrade/data migration, artifact provenance,
and qualification before production selection changes.

## Device qualification before promotion

1. Record exact ROM/SDK, page size, Skånetrafiken version/target SDK, GmsCore
   version and real APK signer. Reproduce the reported native-load failure and
   distinguish it from the unrelated historical
   [2023 journey-detail crash](https://github.com/microg/GmsCore/issues/1886).
2. Use an approved install/signing path on a suitable test device. Verify
   restricted spoofing and the existing privileged-permission policy, including
   the explicit `DUMP` denial. Do not bypass those checks merely to install a
   candidate.
3. Open the map and journey-detail screens in Skånetrafiken. Check both an
   existing writable cache and a fresh extraction. Confirm map rendering and
   the absence of `Attempt to load writable file`/`NativeConnectivityListener`
   errors. Upgrade between candidate versions to exercise replacement of a
   cached read-only library.
4. Complete the existing microG self-check, account, push and location tests;
   check Conscrypt initialization and other map clients. Qualify VTM separately
   if it will be shipped; this recipe builds the Mapbox flavor.
5. Verify actual OTA product images and APK/native-library bytes, then check
   boot and later OTA persistence for each promoted root mode/device. Record
   physical observations separately from source preparation and CI compilation.

The current production APKs and helper remain unchanged while this candidate is
reviewed. No device acceptance is implied by the source receipt or CI artifact.
