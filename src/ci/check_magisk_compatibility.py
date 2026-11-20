#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise the locked patcher with a real Magisk APK and tiny synthetic OTAs.

These fixtures have disposable signing keys and an impossible device name. They
prove host patch-format and output-verification behavior, never device boot or
runtime root. Every avbroot invocation uses the existing sealed executable
runner; there is deliberately no unsealed fallback or compatibility bypass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from magisk_artifact import (  # noqa: E402
    MagiskArtifact,
    MagiskError,
    resolve_artifact,
    verify_apk,
    verify_ramdisk,
)


DEVICE = "modos_magisk_synthetic_do_not_flash"
OFFICIAL_MAGISK_SIGNER_SHA256 = (
    "b4cb83b4dad99f997dbe872f013aa16c14eec41d167021f371f7e1330f273ee6"
)
FIXTURES = (
    ("boot-v3", 3, ("boot",), "boot", "sda47"),
    ("boot-v4", 4, ("boot",), "boot", "sda47"),
    ("init-boot-v4", 4, ("boot", "init_boot"), "init_boot", "sda10"),
)


def checked(command: list[str | Path], cwd: Path) -> str:
    completed = subprocess.run(
        [str(value) for value in command], cwd=cwd, check=False,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(value) for value in command)}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed.stdout


def validate_apk_signer_output(output: str) -> dict:
    """Check the single upstream signer across Android Build Tools formats.

    Build Tools <=36 reports ``Signer #1``. Build Tools 37 reports one line
    per signing scheme, such as ``V2 Signer:`` and ``V3.0 Signer:``. Repeated
    certificates across distinct schemes are expected, but duplicate signer
    records, mixed output formats and multiple APK signers are not accepted.
    The subprocess's exit status must be checked before calling this parser.
    """
    counts = []
    legacy = []
    schemes = []
    for line in output.splitlines():
        line = line.rstrip()
        if line.startswith("Number of signers:"):
            counts.append(line.removeprefix("Number of signers:").strip())
        if "certificate SHA-256 digest:" not in line:
            continue
        if line.startswith("Source Stamp Signer:"):
            # Source stamps are not APK signers and cannot establish identity.
            continue
        match = re.fullmatch(
            r"Signer #(\d+) certificate SHA-256 digest: ([0-9A-Fa-f:]+)", line
        )
        if match:
            legacy.append(match.groups())
            continue
        match = re.fullmatch(
            r"(V[1-4](?:\.\d+)?) Signer:(?: \([^\n)]*\))? "
            r"certificate SHA-256 digest: ([0-9A-Fa-f:]+)", line,
        )
        if match:
            schemes.append(match.groups())
            continue
        raise ValueError("Unrecognized APK signer certificate report; review apksigner output")

    if counts != ["1"]:
        raise ValueError(
            "Expected exactly one APK signer count of 1 from apksigner --verbose; "
            f"reported counts: {counts!r}"
        )
    if legacy:
        if schemes or len(legacy) != 1 or legacy[0][0] != "1":
            raise ValueError("APK signer report has duplicate, mixed or multiple signer records")
        format_name = "legacy"
        certificates = [legacy[0][1]]
        scheme_names = []
    else:
        scheme_names = [name for name, _ in schemes]
        if not schemes or len(set(scheme_names)) != len(scheme_names):
            raise ValueError("APK signer report has missing or duplicate signing-scheme records")
        format_name = "scheme-prefixed"
        certificates = [certificate for _, certificate in schemes]
    if any(value.replace(":", "").lower() != OFFICIAL_MAGISK_SIGNER_SHA256
           for value in certificates):
        raise ValueError("APK certificate does not match the single official Magisk signer")
    return {
        "certificate_sha256": OFFICIAL_MAGISK_SIGNER_SHA256,
        "signer_count": 1,
        "output_format": format_name,
        "schemes": scheme_names,
    }


def verify_apk_signer(apk: Path) -> dict:
    output = checked(
        ["apksigner", "verify", "--verbose", "--print-certs", apk], apk.parent,
    )
    report = validate_apk_signer_output(output)
    scheme_text = ", ".join(report["schemes"]) or "legacy signer report"
    print(
        f"Verified official Magisk APK signer {report['certificate_sha256']} "
        f"(one signer; {scheme_text})."
    )
    return report


class Avbroot:
    """Use the same authenticated, sealed executable contract as OTA builds."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.prefix = [
            sys.executable, REPO_ROOT / "src/bootstrap_executable_tools.py",
            "--workdir", workdir / "bootstrap",
        ]

    def install(self) -> None:
        checked(
            [*self.prefix, "install", "--report",
             self.workdir / "bootstrap/report.json", "avbroot"],
            self.workdir,
        )

    def __call__(self, *arguments: str | Path) -> str:
        return checked([*self.prefix, "run", "avbroot", *arguments], self.workdir)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scalars(values: dict) -> str:
    """Serialize the small scalar-only tables returned by avbroot."""
    return "".join(f"{name} = {json.dumps(value)}\n" for name, value in values.items())


def avb_header() -> str:
    return "[header]\n" + scalars({
        "required_libavb_version_major": 1,
        "required_libavb_version_minor": 0,
        "algorithm_type": "Sha256Rsa4096",
        "hash": "", "signature": "", "public_key": "", "public_key_metadata": "",
        "rollback_index": 0, "flags": 0, "rollback_index_location": 0,
        "release_string": "ModOS synthetic fixture", "reserved": "00" * 80,
    })


def create_keys(workdir: Path, avbroot: Avbroot) -> None:
    checked([
        "openssl", "req", "-x509", "-newkey", "rsa:4096", "-nodes", "-sha256",
        "-days", "2", "-subj", "/CN=ModOS synthetic fixture only/",
        "-keyout", workdir / "test.key", "-out", workdir / "test.pem",
    ], workdir)
    avbroot("key", "encode-avb", "--key", workdir / "test.key",
            "--output", workdir / "test.pkmd")


def create_fixture(
    directory: Path, version: int, partitions: tuple[str, ...],
    workdir: Path, avbroot: Avbroot,
) -> Path:
    directory.mkdir(mode=0o700)
    tree = directory / "input-tree"
    (tree / "system/etc/security").mkdir(parents=True)
    (tree / "init").write_bytes(b"Synthetic init; this image cannot boot.\n")
    with zipfile.ZipFile(tree / "system/etc/security/otacerts.zip", "w") as archive:
        archive.write(workdir / "test.pem", "test.x509.pem")

    cpio_info = directory / "input-cpio.toml"
    cpio_info.write_text(
        'format = "Gzip"\n'
        '[[entries]]\npath = "init"\nfile_type = "Regular"\n'
        'file_mode = "750"\nnlink = 1\n'
        '[[entries]]\npath = "system/etc/security/otacerts.zip"\n'
        'file_type = "Regular"\nfile_mode = "644"\nnlink = 1\n'
    )
    ramdisk_prefix = directory / "ramdisk."
    avbroot("cpio", "pack", "--output", f"{ramdisk_prefix}0",
            "--input-info", cpio_info, "--input-tree", tree, "--quiet")
    boot_info = directory / "boot.toml"
    boot_info.write_text(
        'type = "V3Through4"\nos_version = 0\nreserved = [0, 0, 0, 0]\n'
        'cmdline = "modos_synthetic_fixture=1"\n'
        + ("[v4_extra]\n" if version == 4 else "")
    )
    raw = directory / "raw.img"
    avbroot("boot", "pack", "--output", raw, "--input-header", boot_info,
            "--input-ramdisk-prefix", ramdisk_prefix, "--quiet")

    images = directory / "images"
    images.mkdir()
    vbmeta_info = "image_size = 4096\n" + avb_header()
    for name in partitions:
        info = directory / f"{name}-avb.toml"
        completed_info = directory / f"{name}-avb-complete.toml"
        info.write_text(
            "image_size = 16777216\n" + avb_header()
            + "[[header.descriptors]]\n" + scalars({
                "type": "Hash", "image_size": 0, "hash_algorithm": "sha256",
                "partition_name": name, "salt": "", "root_digest": "",
                "flags": 0, "reserved": "00" * 60,
            })
            + "[footer]\n" + scalars({
                "version_major": 1, "version_minor": 0, "original_image_size": 0,
                "vbmeta_offset": 0, "vbmeta_size": 0, "reserved": "00" * 28,
            })
        )
        # The root vbmeta signs these hash descriptors; appended headers do
        # not need independent signatures or chain-partition descriptors.
        avbroot("avb", "pack", "--output", images / f"{name}.img",
                "--input-info", info, "--input-raw", raw,
                "--output-info", completed_info, "--force", "--quiet")
        descriptor = tomllib.loads(completed_info.read_text())["header"]["descriptors"][0]
        vbmeta_info += "[[header.descriptors]]\n" + scalars(descriptor)
    vbmeta_path = directory / "vbmeta.toml"
    vbmeta_path.write_text(vbmeta_info)
    avbroot("avb", "pack", "--output", images / "vbmeta.img",
            "--input-info", vbmeta_path, "--key", workdir / "test.key",
            "--force", "--quiet")

    payload_info = directory / "payload.toml"
    payload_info.write_text(
        "version = 2\n[manifest]\nblock_size = 4096\nminor_version = 0\n"
        + "".join(f'[[manifest.partitions]]\npartition_name = "{name}"\n'
                  for name in (*partitions, "vbmeta"))
    )
    ota_info = directory / "ota.toml"
    ota_info.write_text(
        'files = ["payload.bin", "payload_properties.txt", "care_map.pb"]\n'
        '[metadata]\ntype = "AB"\n'
        f'[metadata.precondition]\ndevice = ["{DEVICE}"]\n'
        f'[metadata.postcondition]\ndevice = ["{DEVICE}"]\n'
        f'build = ["modos/{DEVICE}:17/TEST/1:user/release-keys"]\n'
        'build_incremental = "1"\nsdk_level = "37"\n'
        'security_patch_level = "2026-11-01"\n'
    )
    files = directory / "files"
    files.mkdir()
    (files / "care_map.pb").write_bytes(b"")
    source = directory / "source-do-not-flash.zip"
    avbroot("zip", "pack", "--output", source, "--input-info", ota_info,
            "--input-files", files, "--payload", "--input-payload-info", payload_info,
            "--input-payload-images", images, "--key", workdir / "test.key",
            "--cert", workdir / "test.pem", "--quiet")
    return source


def unpack_target(ota: Path, target: str, directory: Path, avbroot: Avbroot) -> Path:
    directory.mkdir()
    avbroot("ota", "extract", "--input", ota, "--partition", target,
            "--directory", directory)
    avbroot("boot", "unpack", "--input", directory / f"{target}.img",
            "--output-header", directory / "boot.toml",
            "--output-ramdisk-prefix", directory / "ramdisk.",
            "--no-output-kernel", "--no-output-second", "--no-output-recovery-dtbo",
            "--no-output-dtb", "--no-output-vts-signature", "--no-output-bootconfig",
            "--quiet")
    avbroot("cpio", "unpack", "--input", directory / "ramdisk.0",
            "--output-info", directory / "cpio.toml",
            "--output-tree", directory / "tree", "--quiet")
    return directory / "tree"


def qualify_fixture(
    spec: tuple[str, int, tuple[str, ...], str, str], workdir: Path,
    apk: Path, artifact: MagiskArtifact, avbroot: Avbroot,
) -> dict:
    name, version, partitions, target, preinit = spec
    directory = workdir / name
    source = create_fixture(directory, version, partitions, workdir, avbroot)
    verification = ["--public-key-avb", workdir / "test.pkmd",
                    "--cert-ota", workdir / "test.pem"]
    avbroot("ota", "verify", "--input", source, *verification)
    reports = {}
    targets = {}
    for mode in ("rootless", "magisk"):
        output = directory / f"{mode}-do-not-flash.zip"
        root_args = (["--rootless"] if mode == "rootless" else
                     ["--magisk", apk, "--magisk-preinit-device", preinit])
        # Synthetic fixtures have no system filesystem. Recovery certificates,
        # OTA signatures, payload hashes and AVB verification remain enabled.
        avbroot("ota", "patch", "--input", source, "--output", output,
                "--key-avb", workdir / "test.key", "--key-ota", workdir / "test.key",
                "--cert-ota", workdir / "test.pem", "--skip-system-ota-cert", *root_args)
        avbroot("ota", "verify", "--input", output, *verification)
        listed = set(avbroot("ota", "list", "--input", output).splitlines())
        if listed != {*partitions, "vbmeta"}:
            raise RuntimeError(f"{name}: unexpected finished partition set: {listed}")
        extracted = directory / f"{mode}-extracted"
        tree = unpack_target(output, target, extracted, avbroot)
        targets[mode] = extracted / f"{target}.img"
        if mode == "rootless":
            try:
                verify_ramdisk(tree, apk, artifact, extracted / "cpio.toml")
            except (MagiskError, FileNotFoundError):
                pass
            else:
                raise RuntimeError(f"{name}: rootless image accepted as selected Magisk")
        else:
            info = avbroot("boot", "magisk-info", "--image", targets[mode])
            if f"PREINITDEVICE={preinit}" not in info.splitlines():
                raise RuntimeError(f"{name}: expected preinit device was not retained")
            payload = verify_ramdisk(tree, apk, artifact, extracted / "cpio.toml")
            reports["magisk_payload"] = payload
            # A stale init or changed executable must not satisfy output identity.
            init = tree / "init"
            original = init.read_bytes()
            init.write_bytes(b"different Magisk init\n")
            try:
                verify_ramdisk(tree, apk, artifact, extracted / "cpio.toml")
            except MagiskError:
                pass
            else:
                raise RuntimeError(f"{name}: mismatched Magisk init was accepted")
            finally:
                init.write_bytes(original)
        reports[mode] = {"ota_sha256": digest(output), "boot_sha256": digest(targets[mode])}
    if reports["rootless"]["boot_sha256"] == reports["magisk"]["boot_sha256"]:
        raise RuntimeError(f"{name}: rootless and Magisk boot targets are identical")
    if target == "init_boot":
        # The patcher must prefer init_boot even with a usable boot ramdisk.
        other_digests = []
        for mode in ("rootless", "magisk"):
            other = directory / f"{mode}-boot-extracted"
            avbroot("ota", "extract", "--input", directory / f"{mode}-do-not-flash.zip",
                    "--partition", "boot", "--directory", other)
            other_digests.append(digest(other / "boot.img"))
        if other_digests[0] != other_digests[1]:
            raise RuntimeError(f"{name}: Magisk unexpectedly changed the other boot image")
    return {"fixture": name, "header_version": version, "target": target,
            "preinit_device": preinit, **reports}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True,
                        help="new directory for private synthetic fixtures and receipt")
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--repository", default="topjohnwu/Magisk")
    parser.add_argument("--version", default="v31.1")
    parser.add_argument("--sha256", default="")
    args = parser.parse_args()
    workdir = args.workdir.absolute()
    apk = args.apk.absolute()
    artifact = resolve_artifact(args.repository, args.version, args.sha256)
    apk_report = verify_apk(apk, artifact)
    signer_report = verify_apk_signer(apk)
    workdir.mkdir(mode=0o700)
    avbroot = Avbroot(workdir)
    avbroot.install()
    version = avbroot("--version").strip()
    create_keys(workdir, avbroot)
    fixtures = [qualify_fixture(spec, workdir, apk, artifact, avbroot) for spec in FIXTURES]
    report = {
        "schema": "modos-magisk-host-compatibility-v1",
        "scope": "synthetic host patch/verification only; no device boot or runtime root",
        "execution": "authenticated sealed avbroot runner", "avbroot": version,
        "apk": apk_report, "apk_signer": signer_report, "fixtures": fixtures,
    }
    (workdir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Magisk {artifact.version}: {len(fixtures)} real-patcher fixture pairs passed ({version}).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Magisk host compatibility check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
