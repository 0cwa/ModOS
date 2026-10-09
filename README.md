# ModOS (based off PixeneOS)

## Description

ModOS patches supported Android ROM OTA images with a selected set of modules while preserving AVB/OTA signing and update metadata. The maintained ROM profiles currently cover GrapheneOS and LineageOS. The project relies on upstream components from chenxiaolong and other projects, but keeps fork-specific release, trust, and compatibility policy in this repository.

## Soft-fork maintenance

ModOS is maintained as a soft fork of `pixincreate/PixeneOS`: upstream structure and fixes are preferred unless a fork-specific requirement needs a small, isolated extension. Fork-specific behavior should live behind typed configuration, ROM profiles, pinned helper contracts, or thin workflow triggers rather than copied build pipelines.

The Actions inventory is intentionally small: the upstream-style CI, release, multi-device release, and Renovate workflows, plus the reusable shared ROM build and the thin LineageOS release trigger. CI enforces this inventory so temporary acceptance or one-shot workflows cannot be committed accidentally.

## Features

- [BCR](https://github.com/chenxiaolong/BCR)
- [Custota](https://github.com/chenxiaolong/Custota)
- [MSD](https://github.com/chenxiaolong/MSD)
- [OEMUnlockOnBoot](https://github.com/chenxiaolong/OEMUnlockOnBoot)
- [AlterInstaller](https://github.com/chenxiaolong/AlterInstaller)
- Optional Magisk using the repository selected by the build
- Optional local boot animation
- Optional locked F-Droid Privileged Extension integration (default-off)

> [!NOTE]
>
> 1. ModOS is not affiliated with GrapheneOS, LineageOS, or the upstream projects it integrates.
> 2. Linux is the supported host platform for the complete patching workflow.

## Requirements

Host prerequisites include Git, Python 3, `curl`, `jq`, `unzip`, `xxd`, `e2fsprogs`, and `pkg-config`. A Linux host is recommended; WSL or a Linux VM can also be used.

Executable tools are **not** expected to be preinstalled in `PATH`. PixeneOS authenticates and installs the exact AFSR, AVBRoot, and Custota-tool archives declared in [`locks/executable-tools-v1.json`](locks/executable-tools-v1.json), including archive/member hashes, modes, and the reviewed upstream signer binding. See [Executable tool trust](docs/executable-tool-trust.md).

The maintained `0cwa/my-avbroot-setup` helper is pinned to an exact Git revision and checked by the compatibility manifest in [`tools/compat/avbroot_setup_compat.json`](tools/compat/avbroot_setup_compat.json). Its Python dependencies are installed from its `pyproject.toml` with `uv`.

Module versions are selected in `src/declarations.sh`; disabled modules are neither acquired nor passed to the patch helper.

## Working

This repository acts as a server.

1. [release.yml](.github/workflows/release.yml) resolves the ROM version and module-selection fingerprint, then verifies whether the exact device/flavor/selection asset triplet already exists.
2. The reusable build workflow authenticates locked executable tools, verifies the pinned helper contract, downloads the selected modules, and patches/signs the OTA.
3. Published builds upload the OTA, its Custota signature, and selection metadata to the repository release.
4. After the exact assets are verified, the `gh-pages` OTA metadata is updated. Older assets for the same selection fingerprint are pruned without deleting other valid variants.

## Usage

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

ModOS currently pins **official Magisk v30.7** as its avbroot compatibility baseline. This mirrors the current rooted-GrapheneOS reference pairing with avbroot 3.34.1. avbroot 3.34.0 and newer explicitly recognize Magisk 31000, but parser/patch-format support is not the same as target-device runtime validation, so ModOS does not automatically advance to the highest Magisk tag. Override `MAGISK_VERSION` only for an intentional compatibility test.

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
2. Open Custota and set the OTA server URL to the repository's GitHub Pages publication URL, using the form `https://<owner>.github.io/<repository>/<rootless/magisk>`.

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

### Optional custom boot animation

To use a local Android boot animation, place the ZIP at exactly
`custom/boot-animation/bootanimation.zip`. Builds remain unchanged by default;
enable the feature explicitly with `ADDITIONALS_BOOT_ANIMATION=true`, or add
`'ADDITIONALS[BOOT_ANIMATION]' = true` to `env.toml`. The archive is validated
before patching, and its exact SHA-256 is included in the module-selection
fingerprint. The payload is not read or required while the option is disabled.

### Release URL and source overrides

### Disable the stock ROM updater

Set `'ADDITIONALS[DISABLE_SYSTEM_UPDATER]' = true` under `[build]` to remove the stock OTA updater from the patched ROM. For the supported profiles, PixeneOS removes GrapheneOS's `app.seamlessupdate.client` updater from the system partition or LineageOS's `org.lineageos.updater` from `system_ext`, along with updater-specific permission/default-permission configuration files when present.

The updater APK itself is required to match the reviewed ROM-specific path; if it has moved or the unpacked filesystem metadata disagrees with the tree, the build fails instead of silently producing an OTA that still contains the updater. Only enable this when another update path such as Custota is configured and maintained.

By default, generated Custota metadata points patched OTA downloads at GitHub Releases for the current repository. These environment variables can override that behavior:

- `PIXENEOS_RELEASE_OWNER`: GitHub release asset owner. Defaults to the owner from `GITHUB_REPOSITORY`, then `0cwa`.
- `PIXENEOS_RELEASE_REPOSITORY`: GitHub release asset repository. Defaults to the repository from `GITHUB_REPOSITORY`, then `PixeneOS`.
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
