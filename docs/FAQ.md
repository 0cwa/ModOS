# FAQ

### Why did my settings get reset after updating to Custota 5.12?

- Custota `v5.12` contained a major regression where settings failed to migrate properly which resulted in a reset
- Unfortunately, the app now requires the user to set the OTA URL again, to get the next update
- This issue has been fixed [Custota 5.13](https://github.com/chenxiaolong/Custota/blob/v5.13/CHANGELOG.md), `6dc6c4f` on July 18, 2025
  - This version will automatically restore the old settings without any manual intervention
  - If the settings are configured again in Custota `v5.12`, the new settings will not be touched and will be restored in `v5.13` as usual
  
### I'm getting an error on boot saying `Device is corrupt. It can't be trusted`. What can I do? What are my options?

- Flash the OTA, switch partitions, flash it again. Then sideload the OTA on both partitions *before* flashing custom keys (see farewelltospring's [comment](https://github.com/schnatterer/rooted-graphene/issues/96#issuecomment-2986363782) in [#96](https://github.com/schnatterer/rooted-graphene/issues/96) for details).
- See [#89](https://github.com/schnatterer/rooted-graphene/issues/89) and [#96](https://github.com/schnatterer/rooted-graphene/issues/96) for details.

### Root does not seem to exist after update

First distinguish a boot-patch problem from manager setup or authorization:

1. Check that the installed OTA is the **Magisk** variant and note its Magisk version. Install/open the matching official APK as an ordinary user app. Magisk v31.1 no longer installs a missing manager automatically, and a system-only APK does not satisfy its manager lookup.
2. Compare the installed Magisk version shown by the manager with the OTA's version. Complete the runtime **Additional setup / Environment fix** if requested and let the device reboot. If setup instead asks for a full Magisk reinstall/reflash, stop: a version mismatch or missing preinit partition needs investigation and, if necessary, a newly patched/signed OTA. Do not proceed with **Direct install** or **Install to inactive slot**.
3. On v31.1, open **Superuser → + / Grant Superuser**, find **Shell** (`com.android.shell`, UID 2000; search also accepts **adb**), and grant access before testing over ADB. An unapproved application or shell no longer triggers a permission prompt.
4. Run the root check after that grant:

   ```shell
   adb shell su -c id
   ```

   The expected result includes `uid=0(root)`. Repeat after reboot. A denied request with no Shell grant is an authorization issue; it does not identify a failed boot patch.

If setup still fails, preserve the exact error, ROM and Magisk versions, and the installed OTA identity for diagnosis. Follow the [ModOS root procedure](../README.md#using-root), [Magisk v31.1 changes](https://github.com/topjohnwu/Magisk/releases/tag/v31.1), and [avbroot update guidance](https://github.com/chenxiaolong/avbroot#updating-magisk). Rebuild and install the correctly signed OTA when the boot-side Magisk version must change. Deleting `/data/adb/magisk*` discards settings and evidence and is not a general repair procedure.

Historical environment-setup cases are discussed in [avbroot#455](https://github.com/chenxiaolong/avbroot/issues/455#issuecomment-2955973508) and [rooted-graphene#5](https://github.com/rooted-graphene/ota/issues/5); their older authorization flow should not be assumed to apply to v31.1.

