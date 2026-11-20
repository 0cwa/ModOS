# Magisk compatibility

ModOS selects official **Magisk v31.1** explicitly. Official v30.7 remains a
digest-locked rollback option. This selection is separate from target-device
runtime acceptance: a successfully patched and verified image cannot show that
the installed manager initialized `/data/adb/magisk`, that a user granted root,
or that root persists after a later OTA.

## Upstream contracts

- [Magisk v31.1](https://github.com/topjohnwu/Magisk/releases/tag/v31.1) has
  version code `31100`. It removes the app stub and automatic manager
  installation, reactive root prompts, and app repackaging. Android 7.0/API 24
  is its minimum. Install the matching official APK as an ordinary user app,
  complete runtime setup when appropriate, then grant each intended client
  access through the Superuser tab. The [README](../README.md#root) gives the
  device procedure, including the explicit Shell grant for ADB checks.
- [avbroot 3.37.1](https://github.com/chenxiaolong/avbroot/releases/tag/v3.37.1)
  added Magisk `31100` support. ModOS's official 3.39.0 executable and the
  maintained helper's guarded 3.37.1 source engine both include this support.
  No engine upgrade or weakening of AVB/care-map verification is needed for
  this Magisk release.
- The [upstream boot patcher](https://github.com/topjohnwu/Magisk/blob/v31.1/scripts/boot_patch.sh)
  uses `libmagiskinit.so` as ramdisk `init`, with `magisk.xz` and `init-ld.xz`
  under `overlay.d/sbin`. The older official v30.7 APK also supplies `stub.xz`.
  Verification derives the expected payload set from the selected APK;
  a missing v31.1 stub is intentional.
- Follow [avbroot's update procedure](https://github.com/chenxiaolong/avbroot#updating-magisk):
  repatch and sign the OTA for Magisk updates. Direct install or inactive-slot
  installation in the manager is outside the ModOS update path.

## What the build verifies

`src/magisk_artifact.py` resolves the repository/version against the committed
[`locks/magisk-v1.json`](../locks/magisk-v1.json), or requires an explicit
`MAGISK_APK_SHA256` for an unlisted version/fork. A caller cannot override a
known official release with different bytes under the same name. The build
identity includes the resolved APK digest only for rooted outputs.

Acquisition revalidates cached APKs, bounds downloads, checks the digest before
parsing, and atomically replaces the cache only after validation. ZIP version
and version-code metadata must agree with `assets/util_functions.sh`; required
native payloads must be readable and unambiguous. A failed acquisition stops
the build even if an older APK remains in the cache.

After output verification, ModOS extracts `init_boot` when present, otherwise
`boot`. It requires the configured `PREINITDEVICE`, compares `init` and the
decompressed native/stub payloads to the selected APK, and checks their CPIO
types, permissions and ownership. It rejects stale or unexpected Magisk
payloads. In a paired build, the rootless target must remain unpatched and
differ from the Magisk target. Failure removes the rooted OTA and its Custota
signature before publication.

CI also verifies both locked official APK signatures against the
[published upstream certificate](https://github.com/topjohnwu/Magisk/blob/v31.1/scripts/release.sh),
SHA-256 `b4cb83b4dad99f997dbe872f013aa16c14eec41d167021f371f7e1330f273ee6`.
The exact APK digests bind production downloads to those reviewed bytes.

## Repeatable host qualification

CI runs the artifact regression suite and genuine APKs through the existing
authenticated, sealed avbroot executable runner. The tiny synthetic OTA pairs
cover v3 `boot`, v4 `boot`, and v4 `init_boot` preference when both targets are
present. They use disposable keys and an impossible device name. Each source
and output passes OTA signature, payload, partition and AVB checks. The test
also checks exact Magisk payloads, preinit retention, rootless separation and
rejection of a deliberately changed `init`.

On a host that supports the existing executable runner:

```shell
python3 tests/magisk_artifact_test.py
python3 src/magisk_artifact.py \
  --repository topjohnwu/Magisk --version v31.1 \
  download --apk /path/to/Magisk-v31.1.apk
python3 src/ci/check_magisk_compatibility.py \
  --workdir /path/to/new-private-qualification-directory \
  --apk /path/to/Magisk-v31.1.apk --version v31.1
```

The qualification directory must not already exist. Its `report.json`
contains artifact identities and per-fixture results; synthetic ZIPs and
disposable keys are never release candidates. The script has no unsealed
execution fallback. Same-repository `integration/` PRs additionally use the
existing nonpublishing GrapheneOS/shiba and LineageOS/pdx235 paired OTA jobs.

Magisk integration branches also run an independent, narrow paired ROM profile
with AFSR, Custota, boot animation and stock updater removal. This uses the same
build workflow, disposable keys and verification gates, with optional modules
disabled. It produces no release or update-feed changes. The ordinary full
test job and full-profile qualification remain separate gates.

On November 20 the existing full test job is blocked because Termux removed the
locked `exfatprogs_1.4.3_aarch64.deb` from its rolling package repository (HTTP
404). No byte-identical replacement was found on the checked official mirrors
or retained upstream artifacts. The exfatprogs lock remains unchanged; passing
Magisk-specific checks does not resolve or waive that full-profile blocker.

## Updating the supported release

1. Review the official stable GitHub release and its source changes. Renovate
   proposes releases; normal builds never float to the greatest Git tag.
2. Download the official APK. Independently verify its signature using the
   existing upstream signer, record the size/SHA-256 and ZIP/script version
   code, and add the reviewed entry to the lock. Do not fetch an expected hash
   dynamically during a production build.
3. Confirm both selected avbroot engines support the version and payload
   layout. Keep the helper's AVB and care-map protections intact.
4. Update the typed default and regression expectations together. Run the
   genuine APK/patcher fixtures and nonpublishing paired ROM qualification.
5. Review manager setup, authorization and upgrade instructions against that
   exact release. Record actual device boot, runtime version, explicit root
   authorization, reboot persistence and persistence after a subsequent OTA.
   Record Zygisk/module acceptance separately when those features are used.

The [November 20 evidence](evidence/magisk-20261120.json) separates source and
host observations from device work. Do not interpret fixture success as
physical-device acceptance.
