# Executable tool trust lock

`locks/executable-tools-v1.json` records the immutable release archives and
authenticated layouts for the three executable tools currently pinned by
PixeneOS on `x86_64-unknown-linux-gnu`. The lock is repository data only. It is
validated offline by `src/validate_executable_tool_lock.py`.

## Trust root

The sole allowed signer is the Ed25519 key published in chenxiaolong's
[SSH signature verification guide][signing-guide]:

```text
identity: chenxiaolong
namespace: file
key type: ssh-ed25519
fingerprint: SHA256:Ct0HoRyrFLrnF9W+A/BKEiJmwx7yWkgaW/JvghKrboA
```

The exact allowed-signers binding is checked in at
`trust/chenxiaolong.allowed_signers`. The validator parses the OpenSSH key blob,
requires an Ed25519 key with exactly 32 public-key bytes, recomputes the OpenSSH
SHA-256 fingerprint with Python's standard library, and requires every lock
entry to name that fingerprint, identity, and namespace.

## Recorded release artifacts

The following archive sizes and SHA-256 digests were independently recomputed
from downloads made on 2026-07-20 for afsr v1.0.4, on 2026-09-18 for avbroot
v3.34.1 (published 2026-09-06), and on 2026-08-08 for Custota v6.4. They matched
the sizes and SHA-256 asset digests exposed by GitHub's release API. Each
detached signature was exactly 294 bytes and was accepted by
`ssh-keygen -Y verify` using identity `chenxiaolong` and namespace `file` only
after the archive digest and size had been checked.

| Tool | Release archive | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| afsr 1.0.4 | `afsr-1.0.4-x86_64-unknown-linux-gnu.zip` | 1,548,868 | `8fdbc9aa6c31b4e6530388ffc5adc42652ec6bbd753aef0815d27d8c3a4b9687` |
| avbroot 3.34.1 | `avbroot-3.34.1-x86_64-unknown-linux-gnu.zip` | 4,254,252 | `b1740ebf92d503cf2e81ca443afa4b615fb97ec365e170b71791ed72d9e559f8` |
| custota-tool 6.4 | `custota-tool-6.4-x86_64-unknown-linux-gnu.zip` | 2,357,231 | `7d45c0200839f4527b9cdae45ee53bc6579944a85eafb5016fc10e252e120911` |

Detached-signature SHA-256 digests observed during that verification were:

| Signature | SHA-256 |
| --- | --- |
| `afsr-1.0.4-x86_64-unknown-linux-gnu.zip.sig` | `354bd28d0c1cf20a9ca76dfb958451ad17fa1f34f125fec1ddae58fffc315616` |
| `avbroot-3.34.1-x86_64-unknown-linux-gnu.zip.sig` | `b94ed3c697509aa98b0fec55999694ef18a33f1b83a0d82d9d9321632072da01` |
| `custota-tool-6.4-x86_64-unknown-linux-gnu.zip.sig` | `ecf07da6094f8d9c407a48641badcab88b3f813b1944092469c315a3b34db632` |

GitHub does not publish separate publisher-signed checksum files for these
releases. GitHub's asset digest is discovery metadata, not a replacement for the
publisher's detached SSH signature. Builds must consume the reviewed digest in
the checked-in lock and must not obtain mutable digest policy from the API.

Official releases:

- [afsr v1.0.4][afsr-release]
- [avbroot v3.34.1][avbroot-release]
- [Custota v6.4][custota-release]

## Verification and archive inspection

The release archives were downloaded to a private temporary directory. Before
any extraction, each archive was checked for its exact byte size and SHA-256,
verified with the detached OpenSSH signature, and inspected through Python's ZIP
central-directory parser. Inspection rejected duplicate names, absolute paths,
backslashes, empty or dot components, non-normalized paths, encrypted members,
and non-file/non-directory Unix entry types. The reviewed archives contained:

| Tool | Member | Type | Mode | Bytes | SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| afsr | `afsr` | file | `0755` | 3,469,744 | `923fa7caaac8b5e3b15b3f0f2e9a08ca34b226cbbcee3f80f40ee5afc735c6d7` |
| avbroot | `LICENSE` | file | `0644` | 35,149 | `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986` |
| avbroot | `README.md` | file | `0644` | 33,128 | `36f29f6c9142be36a1895ff1f1b8675b9f29f1d2949d9680da8100ed47555d29` |
| avbroot | `avbroot` | file | `0755` | 10,931,888 | `de0ed1b439175c8c358e2b2de289adc1a8947c655e3cce1598de374ab71d316b` |
| custota-tool | `custota-tool` | file | `0755` | 5,552,592 | `eb3fc5a31a955d74222ec00c7349e9148719a3ccd6b25ede12e24e34030767ee` |

Only after those checks passed were the archives extracted into separate private
temporary directories. The extracted member types, modes, sizes, and SHA-256
digests matched the central-directory inspection and the committed lock.

The verification environment was:

- curl 8.18.0 with OpenSSL 3.5.5
- OpenSSH 10.2p1 with OpenSSL 3.5.5
- Python 3.14.5 standard-library `zipfile`
- GNU coreutils `sha256sum` 9.10
- Info-ZIP `unzip` 6.00, used only after authentication and inspection

## Offline validation policy

Run:

```sh
python3 src/validate_executable_tool_lock.py
```

For fixtures or independent copies, use `--lock PATH --trust PATH`. Validation
does not access the network. It fails closed on duplicate or unknown JSON fields,
duplicate tools or members, noncanonical JSON, uppercase or malformed hashes,
unbounded sizes, unsafe or unsorted layout paths, unknown members or modes,
unreviewed tool IDs, versions, architectures, URLs, signature parameters, trust
keys, and fingerprints. Both input paths must resolve directly to bounded regular
files; symlinks are not followed.

The current schema deliberately recognizes only these three releases on
`x86_64-unknown-linux-gnu`. Adding another version, platform, tool, member, or
mode requires a reviewed validator-policy update and regenerated canonical lock.

## Trust rotation

Never fetch or replace signing keys automatically. A signer rotation requires an
authoritative publisher statement binding the replacement key, independent
review of that provenance, and a single-purpose change that updates the trust
file, validator binding, and affected lock entries together. Rotation tests must
demonstrate acceptance of the new key and rejection of the retired key, wrong
identities, and wrong namespaces. Re-verify all retained release artifacts after
rotation; do not infer that a new key authenticates old assets without valid new
signatures or an authoritative cross-binding.

## Runtime bootstrap enforcement

`src/bootstrap_executable_tools.py` consumes this lock directly. The shell
runtime does not reconstruct executable release versions, asset URLs, or
layouts. It selects enabled tool IDs and submits the entire set in one batch.
Every selected archive must pass its locked byte size, SHA-256, detached
OpenSSH signature, exact hostile-archive inspection, and full member digest
check before extraction of any selected archive begins.

Downloads use bounded streaming into exclusive, no-follow files in a private
mode-`0700` transaction directory. Verified bytes are fsynced and atomically
published under `bootstrap-cache/objects`; a canonical receipt binds the
archive digest to its verified signature digest. Cache hits repeat size,
digest, signature, and archive inspection when both referenced objects exist.
If either cached object is missing, the receipt is treated as a cache miss and
the normal download and validation flow is used. A corrupt, linked, or
noncanonical cache entry fails closed.

Members are extracted without `unzip` into private transaction directories.
The extractor accepts only the exact locked top-level regular-file layout and
rejects traversal, absolute paths, backslashes, aliases, duplicates, links,
special files, directories, extras, encryption, unsupported ZIP flags or
compression, overlapping compressed ranges, and excessive expansion. It
rechecks extracted types, modes, sizes, and digests before atomically publishing
the directory at `tools/by-sha256/<archive-sha256>`.

Existing digest-addressed installations are fully revalidated. Legacy
`tools/<id>` directories are never trusted as an installation bypass. Direct
PixeneOS invocations use the runtime `run` command, which opens the exact
digest-addressed installation through a held directory descriptor, revalidates
the complete locked layout, copies the verified executable bytes into an
anonymous file descriptor, sets the locked mode, seals the snapshot against
content changes, and rechecks its digest and mode after sealing. It executes
that sealed descriptor without resolving the executable pathname again.

The `resolve` command and digest-addressed executable paths remain for
compatibility and enabled-tool setup validation. A successful resolution does
not itself authorize execution. PixeneOS supplies the helper's
`--tool-runner-prefix-json` with an exact argument array containing the absolute
Python interpreter, flags disabling Python environment overrides and user
site packages, absolute bootstrap script path, absolute work directory, and
`run` subcommand. The helper appends the allowlisted
tool ID, `--`, and tool arguments without shell parsing. Its tool invocations
therefore pass through the same post-seal-verified descriptor execution as
direct PixeneOS calls, even when the helper changes its working directory or
an executable with the same name exists on `PATH`. Compatibility directories
on `PATH` do not authorize helper execution.

The canonical JSON report contains stable tool identity, version, architecture,
archive and member sizes/digests, and signer verification results. It omits
URLs, timestamps, cache-hit state, temporary names, and all cache/report paths.
The report is written atomically only after the complete selected transaction
succeeds.

The offline lock validator by itself still does **not** authorize extraction,
permission changes, or execution. A successful runtime transaction authorizes
authenticated installation, and the `run` command authorizes direct PixeneOS
and helper execution only through its post-seal-verified anonymous file
descriptor. Run acquisition through `check_and_download_dependencies`; do not call the legacy downloader
for these three executable tools. Real OTA integration remains subject to
the other documented host-code supply-chain gates.

## Current integration evidence

The earlier helper PATH execution gap is closed in the goal integration:
direct ModOS and helper calls use the exact authenticated runner prefix and
post-seal-verified descriptor execution described above. Ordinary build-only
jobs enforce read-only repository permissions and use disposable signing material;
the separately selected repository-signer device candidate forwards only its
signing inputs, with publishing still disabled.

The strict care-map source engine at
`6107d7e92c35b81cac0d28aec3806b45c8cb1f59` and helper passed actual October 9
source verification and disposable-key rootless/Magisk output pairs with microG
off and on. The genuine complete enabled-module profile has also passed
rootless/Magisk postflight, with module archives verified through the pinned SSH signer.
[The review receipt](evidence/pdx235-20261012.json) records exact complete-profile
outputs and selected modules. Independent inspection passed for genuine module
payloads, compatible policy labels, both regular boot variants, updater removal
and shared images/root distinctions. Retained inert updater caches are recorded
without adding another gate. These proofs establish artifact semantics, not the
repository-signed installed-identity candidate, device acceptance or production
lock promotion. [Acceptance](pdx235-care-map-integration.md) records the remaining
development-workflow and physical evidence.

## Explicit source-build development profile

The manual LineageOS workflow may select `development-profile=care-map-pdx235`
for `build-only`. This profile requires LineageOS/pdx235 and
`local-unpublished` output scope. It uses the common acquisition, module,
root-mode, signing, and output-verification pipeline, with one checked-in
candidate helper contract at
`tools/compat/avbroot_setup_care_map_pdx235.json`. Missing or modified candidate
metadata rejects activation. Production helper and executable pins remain
unchanged; ordinary build-only signing remains disposable.

The prepared candidate helper's `tools/care-map/build_and_run.py` reads tracked,
clean source inputs from the captured helper commit, verifies its fixed patch
and Cargo.lock hashes, obtains a fresh exact upstream checkout and builds with
`cargo build --release --locked`. Signing secrets and GitHub credentials are
excluded from compiler processes. The parent retains the newly compiled binary
digest/size in memory and supplies its own sealed runner to the helper with
`--runtime-care-map`; a saved receipt never authorizes execution. ModOS's
existing `open_sealed_executable` binds each execution to those measured bytes.
This development path assumes a trusted compiler/host/dependency cache and is
repository-reviewed source authorization, not a chenxiaolong-signed executable
release. Historical measured binary digests are evidence, not mutable replacement
trust pins. afsr and Custota continue through the official executable lock. The pdx235 publication quarantine
remains unchanged. Plaintext outputs stay local. A separate, default-off `export-device-candidate` request permits opaque,
authenticated encryption for private device acceptance only with this profile,
`signing-mode=repository`, `release-type=build-only`, and no publication. The
public recipient certificate is validated before signing. The encrypted artifact upload contains per-OTA CMS AES-256-GCM envelopes
and a nonsecret transport manifest;
each plaintext archive allowlist consists of its final OTA output, its selection
metadata, the microG report when enabled, and the designated public AVB key.
Private signing material and raw working directories are never exported.

The recipient decrypts to a temporary archive and extracts only after OpenSSL
reports success. For example:

```sh
openssl cms -decrypt -binary -inform DER -in candidate.cms.der \
  -recip recipient.crt -inkey recipient.key -out candidate.tar.tmp &&
  mv candidate.tar.tmp candidate.tar
```

Keep the recipient private key outside the repository and GitHub workflow;
supply only the base64-encoded public certificate. GitHub artifact delivery is
private device-acceptance transport, not release or updater publication.

[signing-guide]: https://github.com/chenxiaolong/chenxiaolong/blob/master/VERIFY_SSH_SIGNATURES.md
[afsr-release]: https://github.com/chenxiaolong/afsr/releases/tag/v1.0.4
[avbroot-release]: https://github.com/chenxiaolong/avbroot/releases/tag/v3.34.1
[custota-release]: https://github.com/chenxiaolong/Custota/releases/tag/v6.4

The reusable ROM publisher inherits its calling job's permissions. Publishing
callers explicitly grant `contents: write`; the shared workflow does not request
write itself, because GitHub validates skipped reusable jobs against a read-only
caller's ceiling before evaluating their conditions. The executed build-only
job explicitly requests `contents: read`, including under a write-capable caller.
