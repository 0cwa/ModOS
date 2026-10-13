# Private pdx235 device observations

Once the verified rootless or Magisk output has been installed through your
reviewed device procedure, capture observations promptly after first boot.
The collector requires an already authorized ADB connection. It makes no
root, authorization, slot, flashing, account, or publication changes.

From the repository root, with a new local destination directory:

```sh
mkdir -p work/device-evidence
python3 src/device/collect_pdx235_evidence.py \
  --serial YOUR_DEVICE_SERIAL \
  --output work/device-evidence/rootless-first-boot \
  --artifact /path/to/verified-rootless.zip \
  --microg-state
```

Omit `--microg-state` for the initial microG-off run. Use `--root-state` for
Magisk package and ADB-shell identity observations; the collector deliberately
does not invoke `su` or complete manager setup. If ADB has only one connected
device, `--serial` is optional. Repeat with a fresh destination after each
required reboot and the Custota OTA cycle, using the artifact relevant to that
boot. Record the actual installation and boot-to-artifact association separately;
an artifact digest in the receipt does not prove which firmware booted.

The private directory contains a JSON receipt and each command's stdout,
stderr, timestamp, and result. It records runtime properties (including the
partition fingerprints and `ro.boot` AVB/verity/slot properties), boot IDs,
uptime, slot success when `bootctl` is accessible, snapshot/snapuserd state,
update-engine state, mounts, device-mapper observations, and recent system/kernel
logs. Optional package dumps expose installed microG or Magisk permissions
and package state. Missing tools and permission failures are recorded as
unavailable. Device/system logs can contain personal information; review these
local files before sharing any extracts.

This is an evidence collector, not an acceptance verdict. Compare the runtime
fingerprints against the verified output's care-map property IDs; check effective
verity and the supported snapuserd or block-read verification path. A successful
slot alone does not establish verification, and absent log lines do not establish
that verification was skipped. Boot IDs at the start and end must match to
interpret the capture as one boot; rebooted captures require a fresh collection.
The logs are limited to the most recent 20,000 entries.

Physical acceptance still requires the observed rootless/Magisk behavior,
Magisk setup and `su -c id` after reboot when applicable, microG self-check and
restricted spoofing, account/push/location checks, and OTA persistence. Follow
[pdx235 acceptance](pdx235-care-map-integration.md) and record these human/device
results alongside the private receipt. Disposable-key fixture OTAs establish
artifact verification and are not upgrade candidates for an existing installation.

The explicit LineageOS workflow now supports `development-profile=care-map-pdx235`,
`release-type=build-only` and `signing-mode=repository` to retain the configured
repository signing identity. Optional `export-device-candidate` with a public
recipient certificate produces an authenticated encrypted private handoff; it
keeps publication quarantined. The actual repository-key workflow, exact encrypted
transport/decryption and independent local candidate verification have passed;
[the review receipt](evidence/pdx235-20261012.json) records their identities.
Physical acceptance and enrolled-key matching remain pending. Confirm that the
public AVB key and OTA certificate match the installation's required identity, then follow the reviewed
installation/OTA procedure and collect these observations. The workflow path
makes stable candidates possible; it does not prove which firmware booted.
