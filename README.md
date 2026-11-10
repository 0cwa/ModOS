# ModOS (based off PixeneOS)

## Description

ModOS patches supported Android ROM OTA images with a selected set of modules while preserving AVB/OTA signing and update metadata. The maintained ROM profiles currently cover GrapheneOS and LineageOS. The project relies on upstream components from chenxiaolong and other projects, but keeps fork-specific release, trust, and compatibility policy in this repository.

## Soft-fork maintenance

ModOS is maintained as a soft fork of `pixincreate/PixeneOS`: upstream structure and fixes are preferred unless a fork-specific requirement needs a small, isolated extension. Fork-specific behavior should live behind typed configuration, ROM profiles, pinned helper contracts, or thin workflow triggers rather than copied build pipelines.

The Actions inventory is intentionally small: the upstream-style CI, release, multi-device release, and Renovate workflows, plus the reusable shared ROM build and the thin LineageOS release trigger. CI enforces this inventory so temporary acceptance or one-shot workflows cannot be committed accidentally.

The [upstream maintenance guide](docs/upstream-maintenance.md) records the local
compatibility boundaries and the upstream changes that would let us remove them.

## Features

- [BCR](https://github.com/chenxiaolong/BCR) (LineageOS builds)
- [Custota](https://github.com/chenxiaolong/Custota)
- [MSD](https://github.com/chenxiaolong/MSD) (disabled by default for LineageOS pdx235 pending USB HAL qualification)
- [OEMUnlockOnBoot](https://github.com/chenxiaolong/OEMUnlockOnBoot)
- [AlterInstaller](https://github.com/chenxiaolong/AlterInstaller)
- Optional Magisk using the repository selected by the build
- Custom boot animation enabled by default (opt-out available)
- Optional locked F-Droid Privileged Extension integration (default-off)
- Optional Shizuku with an init-managed rootless boot service (no wireless debugging)

> [!NOTE]
>
> 1. ModOS is not affiliated with GrapheneOS, LineageOS, or the upstream projects it integrates.
> 2. Linux is the supported host platform for the complete patching workflow.

## Requirements

Host prerequisites include Git, Python 3, `curl`, `jq`, `unzip`, `xxd`, `e2fsprogs`, and `pkg-config`. A Linux host is recommended; WSL or a Linux VM can also be used.

Executable tools are **not** expected to be preinstalled in `PATH`. PixeneOS authenticates and installs the exact AFSR, AVBRoot, and Custota-tool archives declared in [`locks/executable-tools-v1.json`](locks/executable-tools-v1.json), including archive/member hashes, modes, and the reviewed upstream signer binding. See [Executable tool trust](docs/executable-tool-trust.md).

The maintained `0cwa/my-avbroot-setup` helper is pinned to an exact Git revision and checked by the compatibility manifest in [`tools/compat/avbroot_setup_compat.json`](tools/compat/avbroot_setup_compat.json). Its Python dependencies are installed from its `pyproject.toml` with `uv`.

Module versions are selected in `src/declarations.sh`; disabled modules are neither acquired nor passed to the patch helper.

### Build diagnostics and MSD compatibility

The build summary separates warnings from the original source OTA, warnings
from each finished output, and module-injection warnings. Source LineageOS AVB
test-key/flag warnings remain visible; final-output AVB warnings and MSD's
emulator `su` fallback fail the build before publication. Passing verification
does not establish physical-device behavior. See [diagnostic policy and MSD
qualification](docs/build-diagnostics.md).

MSD remains enabled in the GrapheneOS shiba preset. It is temporarily disabled
in the LineageOS pdx235 preset and manual LineageOS workflow defaults. Explicit
MSD selections still undergo the fallback guard; an alternative HAL domain is
never guessed from its name. The read-only pdx235 module audit can observe the
running gadget HAL label when MSD is selected, while export/unexport and USB
recovery remain on-device acceptance checks.

## BCR selection

BCR 3.11 remains enabled by default for LineageOS, including the scheduled
pdx235 builds and both rootless and Magisk outputs. Manual LineageOS builds
can turn it off with the `bcr` workflow input or
`'ADDITIONALS[BCR]' = false` in `env.toml`. Its signed archive, verification
path, and helper version remain pinned as before.

GrapheneOS builds exclude BCR. The shared ROM policy resolves BCR to `false`
before acquisition, helper arguments, and build-selection fingerprinting,
including for older configurations that still request `true`. GrapheneOS
release defaults and its scheduled preset also select `false`. Selection
metadata records `module.bcr=false` and `module.bcr.version=disabled`, so
previous BCR-enabled GrapheneOS builds cannot satisfy the new selection.

## Working

This repository acts as a server.

1. [release.yml](.github/workflows/release.yml) resolves the ROM version and module-selection fingerprint, then verifies whether the exact device/flavor/selection asset triplet already exists.
2. The reusable build workflow authenticates locked executable tools, verifies the pinned helper contract, downloads the selected modules, and patches/signs the OTA.
3. Published builds upload the OTA, its Custota signature, and selection metadata to the repository release.
4. After the exact assets are verified, the `gh-pages` OTA metadata is updated. Older assets for the same selection fingerprint are pruned without deleting other valid variants.

## Usage

### Build the original pdx235 vold locally

The unmodified LineageOS `vold` reproduction workflow can run on a local Linux
x86_64 computer. It acquires pinned Sony vendor inputs from TheMuppets, retains
source downloads and incremental output, and checks the result against the
authenticated original OTA. See [local build setup and commands](docs/vold-baseline-ci.md).
This qualification workflow does not require a GitHub Actions runner.

### Getting Started

Reading the [AVBRoot docs](https://github.com/chenxiaolong/AVBRoot) is essential before proceeding with PixeneOS.

1. Ensure the device has an unpatched version of the selected supported ROM (GrapheneOS or LineageOS) installed. The version must match the one from PixeneOS. It is important to make sure that the version installed matches the version on PixeneOS
2. Start with a version before the latest to ensure OTA functionality.

> [!IMPORTANT]
>
> `Factory image` and `OTA image` are different. AVBRoot is meant to deal with **OTA images**. So does PixeneOS.

### Detailed Instructions

> [!IMPORTANT]
> In case you run into an issue that throws `Device is corrupt. It can't be trusted` soon after first install, try sideloading the OTA once before proceeding with flashing the custom AVB public key. This suggestion is based on the experience of users who faced this issue. See [#89](https://github.com/schnatterer/rooted-graphene/issues/89).
> Also, check the FAQ section for more information on [this](docs/FAQ.md#im-getting-an-error-on-boot-saying-device-is-corrupt-it-cant-be-trusted-what-can-i-do-what-are-my-options) issue.

> [!CAUTION]
> If flashing fails, [**do not switch the slot**](https://github.com/schnatterer/rooted-graphene/issues/96#issuecomment-3128121844).

#### Web Install

It is easier to use the web installer to flash GrapheneOS. However, it is recommended to use the manual method since it makes it possible to install an older version of GrapheneOS unlike the web installer which always installs the latest version.

- Use the [web installer](https://grapheneos.org/install/web) to install GrapheneOS
- Once installed, **do not** re-lock the bootloader by clicking `Lock bootloader` under the `Locking the bootloader` section
- Proceed to the [patching section](#patching-the-selected-rom-cooking-pixeneos)

#### Manual Install

1. Ensure Fastboot version is `34` or newer. `35` or above is recommended as older versions are known to have bugs that prevent commands like `fastboot flashall` from running.

   ```shell
   fastboot --version
   ```

2. Reboot into `fastboot` mode and unlock the bootloader if not already. **This will trigger a data wipe.** Ensure data is backed up.

   ```shell
   fastboot flashing unlock
   ```

3. When setting PixeneOS up for the first time, the device must already be running the correct OS. Flash the original unpatched OTA or factory image if needed.

   ```shell
   bsdtar xvf DEVICE_NAME-factory-VERSION.zip # tar on Windows and macOS
   ./flash-all.sh # or .bat on Windows
   ```

4. Proceed to the [patching section](#patching-the-selected-rom-cooking-pixeneos)

#### Patching the selected ROM (cooking PixeneOS)

1. Download the [OTA from the current repository's releases](../../releases). Ensure the version matches the installed version.

   Extract the partition images from the patched OTA that are different from the original.

   ```shell
   avbroot ota extract \
       --input /path/to/ota.zip.patched \
       --directory extracted \
       --fastboot
   ```

   To extract and flash all OS partitions, pass `--all`.

2. Set the `ANDROID_PRODUCT_OUT` environment variable to the directory containing the extracted files.

   For `sh`/`bash`/`zsh` (Linux, macOS, WSL):

   ```shell
   export ANDROID_PRODUCT_OUT=extracted
   ```

   For PowerShell (Windows):

   ```powershell
   $env:ANDROID_PRODUCT_OUT = "extracted"
   ```

   For cmd (Windows):

   ```bat
   set ANDROID_PRODUCT_OUT=extracted
   ```

3. Flash the partition images.

   ```shell
   fastboot flashall --skip-reboot
   ```

   Note: This only flashes the OS partitions. The bootloader and modem/radio partitions are left untouched due to fastboot limitations. If they are not already up to date or if unsure, after fastboot completes, follow the steps in the [updates section](#updates) to sideload the patched OTA once. Sideloading OTAs always ensures that all partitions are up to date.

   Alternatively, for Pixel devices, running `flash-base.sh` from the factory image will also update the bootloader and modem.

4. Set up the custom AVB public key in the bootloader after rebooting from fastbootd to bootloader.

   ```shell
   fastboot reboot-bootloader
   fastboot erase avb_custom_key
   fastboot flash avb_custom_key /path/to/avb_pkmd.bin
   ```

5. Sideload the OTA (This helps avoid or reduce the possibility of running into the `Device is corrupt. It can't be trusted` error).
   - Run `fastboot reboot recovery` to get into recovery mode
   - An Android icon lying down with the text `No command` should be visible on the screen
   - Hold the power button and press the volume up button a single time to get into the recovery UI
   - Using the volume buttons, navigate to `Apply update from ADB` and select it with the power button
   - As the recovery prompt says, use `adb sideload /path/to/ota.zip.patched` to sideload the patched OTA
   - After the sideload completes, select 'Reboot to bootloader'

6. Reboot into fastboot and lock the bootloader. This will trigger a data wipe.

   ```shell
   fastboot flashing lock
   ```

   Confirm by pressing volume down and then power. Then reboot.

> [!CAUTION]
>
> **Do not uncheck `OEM unlocking`!**

7. For future updates, see the [updates section](#updates).

### Using Root

Root changes the device security model and can introduce compatibility breakage across ROM updates. Use it only when you understand the trade-offs for your device and selected ROM.

ModOS defaults to the official `topjohnwu/Magisk` repository. The source remains configurable through `MAGISK[REPOSITORY]` in `env.toml`, but alternate Magisk forks are opt-in rather than part of the GrapheneOS build contract. If you use Zygisk Next for GrapheneOS compatibility, install and manage it separately from the OTA build. Magisk/Zygisk behavior can change across releases, so rooted builds should be revalidated after ROM, Magisk, or Zygisk changes.

ModOS retains **official Magisk v30.7** as its reviewed runtime baseline. The avbroot executable is pinned separately in `locks/executable-tools-v1.json`. Advancing avbroot and its supported Magisk patch formats does not establish target-device runtime compatibility, so ModOS does not automatically advance to the highest Magisk tag. Override `MAGISK_VERSION` only for an intentional compatibility test.

With avbroot, Magisk is not updated using the app's normal **Direct install** path. Magisk version changes must be made by repatching/re-signing the OTA and installing that OTA. The manager's **Additional setup / Environment fix** is a separate runtime-environment step and does not replace OTA repatching.

A Magisk-patched OTA is only the boot-image half of a working Magisk installation. On a clean install, after a data wipe, or whenever Magisk reports that additional setup is required, install/open the **matching Magisk manager APK**, accept **Additional setup / Environment fix**, and allow the device to reboot. That step provisions the runtime binaries (including `su`) under `/data/adb/magisk`; those files are device state and cannot be embedded in or verified from an OTA image.

After that reboot, verify runtime root on the actual device:

```shell
adb shell su -c id
```

A working setup should report `uid=0(root)`. ModOS CI verifies that the Magisk OTA has a distinct Magisk-patched boot target with the configured preinit device; it deliberately does **not** call that static check proof of runtime root.

For one build flavor, the existing boolean `ROOT` remains supported (`false` = rootless, `true` = Magisk). `ROOT_MODE` is an optional string override with `rootless`, `magisk`, or `both`. `both` prepares the OTA and shared modules once, then emits the normal rootless and Magisk variants from the same prepared image set. Each output keeps its own module-selection fingerprint, Custota signature, update metadata, and `/rootless/` or `/magisk/` publication pointer.

KernelSU is not integrated by this repository. Adding another root implementation would require an explicit compatibility and signature-verification design rather than treating it as interchangeable with Magisk.

> [!NOTE]
> For Magisk preinit, see [Magisk preinit](#magisk-preinit)

### Magisk Preinit

Magisk versions 25211 and newer require a writable partition for storing custom SELinux rules that need to be accessed during early boot stages. This can only be determined on a real device, so avbroot requires the partition to be explicitly specified via `--magisk-preinit-device <name>`. To find the partition name:

1. Extract the boot image from the original/unpatched OTA:

   ```shell
   avbroot ota extract \
       --input /path/to/ota.zip \
       --directory . \
       --boot-only
   ```

2. Patch the boot image via the Magisk app on the target device.

   The Magisk app will print out a line like:

   ```shell
   Pre-init storage partition device ID: <name>
   ```

   Alternatively, run:

   ```shell
   avbroot boot magisk-info \
       --image magisk_patched-*.img
   ```

   The partition name will be shown as `PREINITDEVICE=<name>`.

   Now that the partition name is known, it can be passed to avbroot when patching via `--magisk-preinit-device <name>`. The partition name should be saved somewhere for future reference since it's unlikely to change across Magisk updates.

   If the device is unbootable, patch and flash the OTA once using `--ignore-magisk-warnings`, then repatch and reflash the OTA with `--magisk-preinit-device <name>`.

### Updates

Updates can be done by patching (or re-patching) the OTA using `adb sideload`:

1. Reboot to recovery mode. If stuck at `No command`, press Volume up while holding Power button.
2. Sideload the patched OTA with `adb sideload` by using volume buttons to toggle to `Apply update from ADB` which can be confirmed by pressing the power button

PixeneOS leverages Custota:

1. For builds with `ADDITIONALS[DISABLE_SYSTEM_UPDATER] = true`, PixeneOS removes the stock ROM updater from the patched system image, so there is no separate manual disable step. The checked-in scheduled `shiba` and `pdx235` definitions enable this option. If you leave the option disabled, disable the stock updater manually before relying on Custota.
2. Open Custota and set the OTA server URL to the repository's GitHub Pages publication URL, using the form `https://<owner>.github.io/<repository>/<rootless/magisk>/`. For pdx235 rootless builds from this repository, use `https://0cwa.github.io/ModOS/rootless/`. The phone reports `XQ-DC72` as its OTA device, so Custota fetches `rootless/XQ-DC72.json`. The publisher keeps that file byte-identical to the verified `rootless/pdx235.json` pointer (likewise for the Magisk flavor). Do **not** configure Custota to a historical PixeneOS release feed.

For more info, refer to the current repository's [server](../../tree/gh-pages) branch.

## Tool Usage

PixeneOS can be run locally on Linux.

1. Clone or fork the repository.
2. Review the checked-in `env.toml` example and set the device, ROM family/update channel, root settings, and module toggles you need. Configuration is typed and validated by `src/config_schema.sh`.
3. Run the patch pipeline:

   ```shell
   . src/main.sh
   ```

Local runs generate the patched OTA but do not publish release assets or update `gh-pages`. Configuration precedence is: declaration defaults, then `env.toml`, then explicit caller/workflow inputs. Invalid or unknown TOML keys fail closed.

### exFAT checker on LineageOS pdx235

The optional `exfatprogs-fsck` build input defaults to false. When selected for
LineageOS `pdx235`, ModOS fetches the locked official Termux `exfatprogs`
1.4.3 aarch64 package, extracts only `fsck.exfat` without running package
scripts, checks its Android ELF ABI and hashes, and replaces the existing
`/system/bin/fsck.exfat` file in the system image. The package's Termux-only
RUNPATH is rewritten to `/system/lib64`. The selection identity includes the
adapter source, lock, provenance document, and transformed payload hashes. The
adapter fails if the target file is missing, is not a regular executable,
lacks its SELinux label, or has an unsafe parent path; its existing image
metadata is preserved.

The binary was built for Android API 24 and has only `libc.so` as a dynamic
dependency. The SELinux label is preserved, while execution-domain compatibility
and device runtime behavior remain unverified. We have not established whether
the replacement completes SD card verification within vold's timeout. The option
is rejected for other ROM families and devices and remains disabled in scheduled
builds. Published builds with this option attach a deterministic,
content-addressed corresponding-source bundle for the GPL-2.0 binary before
uploading OTA assets. The bundle includes the exact locked upstream archive, pinned Termux
recipe, the pinned build-control script closure, ModOS adapter, provenance and
lock records, applicable license texts, and a SHA-256 manifest. It does not
include the full Termux repository, build toolchain, or dependency archives,
so it does not claim full binary reproducibility. New releases remain drafts until the uploaded source bundle is
downloaded and hash-verified; existing releases receive the source asset before
any new OTA assets. Local-unpublished builds do not require source publication.
The recorded signed APT metadata is audit provenance; builds verify the lock's
package URL, size, and SHA-256, but do not repeat the APT signature check.

### Custom boot animation (enabled by default)

All builds use the checked-in `custom/boot-animation/bootanimation.zip` by
default, including local, manual, reusable, and scheduled builds. To keep the
ROM's stock animation instead, set `ADDITIONALS_BOOT_ANIMATION=false`, add
`'ADDITIONALS[BOOT_ANIMATION]' = false` under `[build]` in `env.toml`, or
uncheck the `boot-animation` input in GitHub Actions. Explicit opt-outs take
precedence over the default (including in scheduled build definitions).

When enabled, the animation is validated before patching, and its exact payload
digest participates in the module-selection fingerprint. A separate
`custom/boot-animation/bootanimation-dark.zip` is optional; when absent, the
light payload is installed for both themes. The payload is not read or
required while the option is disabled.

### microG on LineageOS

Set `'ADDITIONALS[MICROG]' = true` for an opt-in LineageOS build. The pinned
helper supplies the reviewed microG `v0.3.17.252432` lock and adapter: Services
(`com.google.android.gms`, versionCode `252432034`) plus Companion
(`com.android.vending`, versionCode `84022634`).

Before OTA unpacking, the helper's generic locked-artifact path verifies exact
size, SHA-256, package/version identity, and the official microG APK signer.
The adapter installs the APKs as privileged `product` apps with the reviewed
permission/default-permission/sysconfig integration. No microG installer,
framework patch, unrestricted signature spoofing, or Magisk/LSPosed hook runs.

This relies on current LineageOS API 36 restricted microG signature spoofing.
GrapheneOS is intentionally rejected. GsfProxy, F-Droid, Aurora Store, and
opinionated microG preference defaults are not bundled.

microG remains default-off for manual builds; scheduled pdx235 builds select it
explicitly at the owner's request. Publication remains quarantined pending physical
the remaining self-check/account, FCM, location, and Custota OTA-persistence
validation. The same prepared microG product image works for both
rootless and Magisk outputs when selected.

Renovate proposes microG release updates without automerge. Update and verify
the helper lock and adapter identities together, then advance the immutable
helper pin and compatibility manifests. CI rejects a release selector without
its matching helper lock. See the helper
[lock update procedure](https://github.com/0cwa/my-avbroot-setup/blob/master/docs/microg.md).

### microG Companion signer patch (LineageOS)

LineageOS only spoofs signatures for `com.android.vending` and
`com.google.android.gms` when they carry the official microG certificate, a
constant compiled into `framework.jar` (`MicrogUtils.REAL_SIGNATURE`). Nothing
in a ROM overlay or config can add another signer. A Companion APK built from
source and signed with your own key therefore loses spoofing.

Set `'ADDITIONALS[MICROG_SPOOF_PATCH]' = true` (LineageOS only, default off) to
patch `/system/framework/services.jar` in the system image so that
`com.android.vending` signed with **one additional certificate** is accepted as
well. The official microG certificate stays accepted and `com.google.android.gms`
is not extended. The patch redirects the two services-side call sites
(`ComputerEngine.generatePackageInfo` and `AppsFilterImpl`) to a small helper
class that first asks the stock `MicrogUtils` and only then checks your
certificate. `framework.jar` is left alone: it is part of the boot image, so
editing it would invalidate that image.

* Put the **public** signer certificate (PEM or DER) at
  `custom/microg-companion-signer/cert.pem`, or point `MICROG_COMPANION_CERT`
  at it. Never place a private key or keystore there. Its SHA-256 and the adapter
  bytes are part of the build identity.
* Java is required. baksmali and smali 3.0.10 are downloaded from the official
  release and verified against pinned SHA-256 digests.
* The build fails closed if either call site is not found exactly once, so a
  Lineage release that changes the code is refused rather than half-patched.
* The stock `services.odex`/`vdex`/`art` no longer match and are removed; ART
  recompiles from the jar, so the first boot after an update can be slower.
* It requires `ADDITIONALS[MICROG]`. With both enabled, the locked microG module
  installs ModOS's own Companion build (the helper's
  `locks/microg-<release>-modos.json`: the same release plus the billing fix
  below, signed with the ModOS key, hosted as a release asset of the helper
  repository) instead of the official one. Services stays the official APK, and
  the helper accepts exactly two Companion identities (official, or the ModOS
  signer with its own version code).
* The scheduled pdx235 build enables both.

#### Building the patched Companion

`patches/microg/` holds ModOS's patches for the Companion. The first one fixes
Signal's "Signal Login" purchase showing "Unavailable": the Play Billing Library
passes list-valued flags such as `PRODUCT_TYPES_TO_RETURN_MULTIPLE_OFFERS`, and
microG sent them as the single string `"[subs, inapp]"` instead of a list.

`tools/build-microg-companion.sh` clones the reviewed microG release tag (from
`VERSION[MICROG]`), applies those patches, builds the Companion and signs it with
your key. The password is read only from a file you pass with `--pass-file` and
is never accepted on the command line:

```sh
agent-heavy -- tools/build-microg-companion.sh \
  --keystore .keys/microg-test/keystore.p12 --alias modos-microg-test \
  --pass-file .keys/microg-test/keystore.pass
```

It needs git, JDK 17+ and an Android SDK (`ANDROID_HOME`, platform 35 and
build-tools 35.0.0 with licenses you have accepted). Output goes to
`<git dir>/tmp/microg-companion` with a `build-record.txt` naming the source
commit, patch digests and signer. The result is signed with your key, not the
official microG key, so it is only useful on a ROM built with
`MICROG_SPOOF_PATCH`. Keep keystores in `.keys/` (gitignored), never in the tree.

The patch has been verified to disassemble, reassemble and pass ART's dex2oat
verification (including soft failures) against a real LineageOS 24.0 (Android 17)
`services.jar`. It has not been booted from an OTA built with it, so treat the
first flash as untested.

### Shizuku module

To patch Shizuku into the system image with automatic startup and no USB or
wireless debugging requirement, select it in the existing `[build]` section
of `env.toml`:

```toml
[build]
ROOT_MODE = "rootless"
'ADDITIONALS[SHIZUKU]' = true
```

The GrapheneOS single-device/multi-device and LineageOS manual workflows also
expose the `shizuku` input. Select `root-mode: rootless` for the system-managed
service. Shizuku remains default-off, including in scheduled builds.

#### Startup and permissions

The rootless module installs the official Shizuku manager and server from the
[locked v13.6.0 APK](locks/shizuku-v13.6.0.json). Android init starts the server
after `sys.boot_completed=1` on every boot and supervises it with a ten-second
restart interval. The launcher executes `app_process` in the foreground using
the official server entry point and the in-image APK/native library paths.
It does not use the upstream native starter's fork-and-exit behavior, because
Android init cleans up a service's child processes when its main process exits.
See the [official starter](https://github.com/RikkaApps/Shizuku/blob/v13.6.0/manager/src/main/jni/starter.cpp)
and [Android init lifecycle](https://android.googlesource.com/platform/system/core/+/refs/heads/main/init/service.cpp).

The server runs as **shell UID/GID 2000**, with ADB's supplementary groups and
the explicit SELinux label `u:r:shell:s0`. It uses the ROM's existing permitted
init-to-shell transition through `/system/bin/sh`; it does not add SELinux
rules or make SELinux permissive. This provides Shizuku's ordinary ADB-level
capabilities, not unrestricted root or system UID 1000. The manager remains a
regular product app, and clients still need the user's normal per-app Shizuku
approval. The manager may call this mode “ADB” because of the server's UID;
the service does not depend on an ADB connection or enabled debugging.

For this rootless mode, `/product/etc/sysconfig/modos-shizuku.xml` disables the
official manager's boot receiver by default, using its fully qualified
component name.
That receiver can otherwise [turn USB and wireless debugging on](https://github.com/RikkaApps/Shizuku/blob/v13.6.0/manager/src/main/java/moe/shizuku/manager/receiver/BootCompleteReceiver.kt)
when its last launch used shell permissions. The platform component override
preserves the signed APK while leaving startup to init. No wireless pairing
or manual server-start step is required. Open the manager to manage client
permissions; its Stop action is temporary because init restarts the server.
To remove automatic startup, rebuild the OTA with the module disabled.

**When upgrading an existing Shizuku installation, turn off Shizuku's own
Start on boot setting and leave it off in rootless mode.** Android preserves
explicit per-user component settings ahead of the image's default override.
The official manager's switch may also appear on when Android is using the
disabled default; explicitly switching it off records the intended state.
The init service still starts automatically with this switch off.

Both rootless and Magisk builds install the APK's complete native library set
(`libadb.so`, `librish.so`, and `libshizuku.so`) beneath
`/product/app/Shizuku/lib/<ISA>/`. The manager needs `libadb.so` even when
debugging is unused; Android does not extract compressed native libraries
for an unupdated system app. The server and native payload stay pinned to the
image version, so update them together through the APK lock and a rebuilt OTA.
The adapter's SHA-256 also participates in Shizuku-enabled build fingerprints,
so boot-service fixes cannot reuse an older selection's published OTA.

`ROOT_MODE = "magisk"` installs the same complete manager payload and retains
Shizuku's normal ROOT startup after the user's one-time setup and root approval.
It does not install the shell init service or disable the manager's boot
receiver. `ROOT_MODE = "both"` remains unsupported with Shizuku because the
helper injects one shared product image before splitting output modes.

#### Compatibility and device acceptance

Selection follows ModOS's GrapheneOS/LineageOS profiles and OTA providers. The
adapter fails the build if the required product image, rootless system shell
and `shell_exec` label, complete supported native payload, or product file
contexts are unavailable. The launcher rejects missing or unsupported device
ABIs. Host tests exercise payload installation, foreground process identity,
startup arguments, receiver overrides, build selection, and the acceptance
script. They do not establish target-device runtime compatibility.

For rootless acceptance on each ROM/device combination, keep Shizuku's own
**Start on boot** setting off, boot the patched image with **both USB and
wireless debugging disabled**, open Shizuku, and confirm that an approved
client works. Check that an unapproved client still requires approval, then reboot
again with debugging disabled and repeat. This verifies the actual no-debugging
boot path. For additional diagnostics, enable USB debugging afterwards, keep
wireless debugging off, connect by USB, and run the read-only check:

```shell
bash tests/shizuku_post_boot_check.sh rootless
# or
bash tests/shizuku_post_boot_check.sh magisk
```

The script uses `adb -d` throughout. It checks the installed manager, exactly
one server with the expected UID, and wireless debugging off. In rootless
mode it also checks the running init service, enforcing SELinux, the server's
actual shell SELinux domain, and the boot receiver's effective disabled state
for user 0. A saved explicit enablement fails the check with instructions to
turn off Start on boot. It probes the manager's Binder provider
using `getBinder`; an unsupported response or inability to unpack its custom
Parcelable is inconclusive. It does not reboot, change settings, install
anything, or grant permissions. Exit status is 0 when the automated checks
pass, 1 for a required-check failure, and 2 when a required check is unavailable
or inconclusive without another failure. The manual client and cold-boot
checks remain necessary even when the script passes.

### Disable the stock ROM updater

Set `'ADDITIONALS[DISABLE_SYSTEM_UPDATER]' = true` under `[build]` to remove the stock OTA updater from the patched ROM. For the supported profiles, PixeneOS removes GrapheneOS's `app.seamlessupdate.client` updater from the system partition or LineageOS's `org.lineageos.updater` from `system_ext`, along with updater-specific permission/default-permission configuration files when present.

The updater APK itself is required to match the reviewed ROM-specific path; if it has moved or the unpacked filesystem metadata disagrees with the tree, the build fails instead of silently producing an OTA that still contains the updater. Only enable this when another update path such as Custota is configured and maintained.

### Release URL and source overrides

By default, generated Custota metadata points patched OTA downloads at GitHub Releases for the current repository. These environment variables can override that behavior:

- `PIXENEOS_RELEASE_OWNER`: GitHub release asset owner. Defaults to the owner from `GITHUB_REPOSITORY`, then `0cwa`.
- `PROJECT_NAME`: Project/release display name (default `ModOS`). Set it under `[github]` in `env.toml` for local runs, or as the GitHub Actions repository variable `PROJECT_NAME`. Use letters, digits, dots, underscores or hyphens, starting with a letter or digit. It changes display names, not production signing keys or certificate identity.
- `PIXENEOS_RELEASE_REPOSITORY`: GitHub release asset repository. Defaults to the repository from `GITHUB_REPOSITORY`, then `PROJECT_NAME`. Explicit repository overrides remain independent of the display name.
- `PIXENEOS_RELEASE_BASE_URL`: Full release asset URL prefix, excluding the OTA filename. When set, generated metadata appends the patched OTA filename to this prefix instead of using the default GitHub Releases URL. If you use alternate hosting, publish both the patched OTA and its `.csig` signature at the same prefix.
- `PIXENEOS_AVBROOT_SETUP_SOURCE`: Custom clone URL for the `my-avbroot-setup` helper repository. Defaults to `https://github.com/0cwa/my-avbroot-setup`.

To make the patched OTA available to the device, it needs to be hosted on the server. PixeneOS uses GitHub for pushing updates, handled by [release.yml](.github/workflows/release.yml).

To set up automated release, add the following variables in GitHub secrets:

- `EMAIL`: Email address associated with the GitHub account.
- Base64 encoded keys:
  - `AVB_KEY`
  - `CERT_OTA`
  - `OTA_KEY`
- Passphrases used to generate the keys:
  - `PASSPHRASE_AVB`
  - `PASSPHRASE_OTA`

### Force update

Scheduled runs normally skip an exact selection that is already published. Set `FORCE_UPDATE = true` under `[build]` in `env.toml` to rebuild the current ROM version; manual runs can use `release-type: force-publish`. Superseded assets are cleaned only when their selection metadata matches the same device, ROM family, and module-selection fingerprint.

### Multiple devices

Run [multi-release.yml](.github/workflows/multi-release.yml) manually to build multiple GrapheneOS devices through the same release preflight. Enter a comma-separated list such as `bramble, shiba`; rooted builds use `device:MAGISK_PREINIT`, for example `bramble:sda10, shiba:sda10`. When the workflow input is empty it reads `DEVICES` from `env.toml`.

### Hop Between Root and Rootless

- To remove root, set the repository's GitHub Pages publication URL ending in `/rootless/` in Custota.
- To add root, set the repository's GitHub Pages publication URL ending in `/magisk/` in Custota.

### Commands

- To see the list of available commands:

  ```shell
  . src/util_functions.sh && help
  ```

  `help` command will display the help message.

- To see the list of supported tools:

  ```shell
  . src/util_functions.sh && supported_tools
  ```

  `supported_tools` command will display the list of tools that are supported.

- To generate AVB keys:

  ```shell
  . src/util_functions.sh && generate_keys
  ```

  This command will generate the AVB/OTA signing files under the local `.keys/` directory (`avb.key`, `ota.key`, `ota.crt`, and `avb_pkmd.bin`).

> [!WARNING]
> Treat `.keys/` and the generated signing files as private local material. The repository ignores `.keys/` and common key filenames; execute `src/setup_hooks.sh` to install the pre-commit hook. The hook preserves the `.keys/` guard and runs the PixeneOS secrets scanner. Install `gitleaks` as well for full standard scanner coverage matching CI.

- To create and make the release:

  ```shell
  . src/util_functions.sh && create_and_make_release
  ```

- To call individual functions/commands:

  ```shell
  . src/<file>.sh && <function_name>
  ```

## Reverting Back to Stock

To revert to stock GrapheneOS, LineageOS, or firmware:

1. Reboot into fastboot mode and unlock the bootloader. **This will trigger a data wipe**. Ensure data is backed up.

2. Erase the custom AVB public key:

   ```shell
   fastboot erase avb_custom_key
   ```

3. Flash the stock firmware.

## More information

To know more about the projects used in this repository, refer to the following links:

- [AFSR](https://github.com/chenxiaolong/AFSR)
- [AlterInstaller](https://github.com/chenxiaolong/AlterInstaller)
- [AVBRoot](https://github.com/chenxiaolong/AVBRoot)
- [BCR](https://github.com/chenxiaolong/BCR)
- [Custota](https://github.com/chenxiaolong/Custota)
- [GrapheneOS](https://grapheneos.org)
- [Magisk](https://github.com/topjohnwu/Magisk) (or the repository configured in `env.toml`)
- [MSD](https://github.com/chenxiaolong/MSD)
- [OEMUnlockOnBoot](https://github.com/chenxiaolong/OEMUnlockOnBoot)
- [Rooted Graphene](https://github.com/schnatterer/rooted-graphene)

## FAQs

Check the [FAQs](docs/FAQ.md) to learn about common issues faced by users and their solutions.

## License

Per [ADR-0003](docs/planning/decisions/ADR-0003-license-agpl.md), new project-authored PixeneOS code is licensed under `AGPL-3.0-or-later`. Project-authored source files marked with AGPL SPDX identifiers follow that notice; see the root [LICENSE](LICENSE) file for the license text.

Third-party-derived code, dependencies, modules, tools, release artifacts, and downloaded components retain their upstream licenses and copyright notices. This repository previously used MIT-oriented root license wording with `Copyright (c) 2024 Pa1NarK`; that historical notice is preserved in `LICENSE` for provenance rather than silently erased.

## Credits

- [GrapheneOS](https://grapheneos.org) -- for the OS
- [Chenxiaolong](https://github.com/chenxiaolong) -- for additional features and tools
  - [afsr](https://github.com/chenxiaolong/afsr)
  - [avbroot](https://github.com/chenxiaolong/avbroot)
  - [BCR](https://github.com/chenxiaolong/BCR)
  - [Custota](https://github.com/chenxiaolong/Custota)
  - [MSD](https://github.com/chenxiaolong/MSD)
  - [my-avbroot-setup](https://github.com/0cwa/my-avbroot-setup)
  - [OEMUnlockOnBoot](https://github.com/chenxiaolong/OEMUnlockOnBoot)
- [Rooted-Graphene](https://github.com/schnatterer/rooted-graphene) -- for motivation and inspiration

## Disclaimer

THIS SOFTWARE IS PROVIDED "AS IS" AND ANY EXPRESSED OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
