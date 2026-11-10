#!/usr/bin/env python3
"""Let LineageOS restricted signature spoofing accept one extra Companion signer.

LineageOS only spoofs signatures for ``com.android.vending`` and
``com.google.android.gms`` when the package is signed with the official microG
certificate (``MicrogUtils.REAL_SIGNATURE``, a hardcoded constant in
framework.jar). This module patches ``/system/framework/services.jar`` so that
``com.android.vending`` signed with one additional, owner-supplied certificate
is accepted as well. The official microG certificate stays accepted, and
``com.google.android.gms`` is not touched.

Only ``services.jar`` is edited. ``framework.jar`` is part of the boot image, so
editing it would invalidate that image; ``services.jar`` is not.

The two call sites in services.jar are redirected to a small helper class added
to the same dex file:

* ``ComputerEngine``: ``MicrogUtils.getFakeSignature(AndroidPackage)``
* ``AppsFilterImpl``:  ``MicrogUtils.isMicrogSigned(AndroidPackage)``

The helper first asks the stock ``MicrogUtils`` and only falls back to the extra
certificate when the stock check refuses. The patch fails closed: if either call
site is not found exactly once the build is refused.

This module is copied into the pinned my-avbroot-setup checkout at build time,
like the local boot-animation module. baksmali/smali are Java tools; their
paths come from the environment and are downloaded and hash-checked by the
``fetch-tools`` subcommand.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any, Callable


CERT_ENVIRONMENT = "MODOS_MICROG_COMPANION_CERT"
BAKSMALI_ENVIRONMENT = "MODOS_BAKSMALI_JAR"
SMALI_ENVIRONMENT = "MODOS_SMALI_JAR"
API_LEVEL_ENVIRONMENT = "MODOS_MICROG_SPOOF_API_LEVEL"

SERVICES_JAR = PurePosixPath("/system/framework/services.jar")
# Precompiled artifacts of the stock services.jar. They no longer match the
# patched jar, so they are removed and ART recompiles from the jar.
STALE_AOT_FILES = tuple(
    PurePosixPath(f"/system/framework/oat/{abi}/services.{ext}")
    for abi in ("arm64", "arm")
    for ext in ("odex", "vdex", "art")
)

HELPER_TYPE = "Lcom/android/server/pm/ModosMicrogSigner;"
HELPER_SMALI_PATH = "com/android/server/pm/ModosMicrogSigner.smali"
COMPANION_PACKAGE = "com.android.vending"

_PKG = "Lcom/android/server/pm/pkg/AndroidPackage;"
_MICROG = "Lcom/android/internal/content/MicrogUtils;"

# (smali file, method name, descriptor): each must appear exactly once.
REDIRECTS: tuple[tuple[str, str, str], ...] = (
    (
        "com/android/server/pm/ComputerEngine.smali",
        "getFakeSignature",
        f"({_PKG})Landroid/content/pm/Signature;",
    ),
    (
        "com/android/server/pm/AppsFilterImpl.smali",
        "isMicrogSigned",
        f"({_PKG})Z",
    ),
)

# Pinned tool downloads (baksmali/smali 3.0.10 fat jars).
TOOL_DOWNLOADS: dict[str, tuple[str, str, int]] = {
    "baksmali-3.0.10-fat.jar": (
        "https://github.com/baksmali/smali/releases/download/3.0.10/baksmali-3.0.10-fat.jar",
        "37ae4a41a8886e15c20b8362fa4250f96bbdb55e1a608199ad8b5dff068b588f",
        4447943,
    ),
    "smali-3.0.10-fat.jar": (
        "https://github.com/baksmali/smali/releases/download/3.0.10/smali-3.0.10-fat.jar",
        "32fa0e88a6c397b3922201adf5f3e534fbaed5a663c71d0c558c3ddce0af844a",
        5384623,
    ),
}

# smali 3.0.10 picks the dex version from --api and always writes a 112-byte
# header: API 28-29 give dex 039 (valid), API 30-34 give dex 040 and API 35+ give
# dex 041, both of which ART rejects ("Header size is 112 but 120 was expected").
# ROM services.jar files carry dex 039, so assemble as API 29. The assembled
# magic is checked against the original dex in patch_services_jar().
ASSEMBLE_API = 29

_DEX_NAME = re.compile(r"^classes[0-9]*\.dex$")
_TOOL_TIMEOUT_SECONDS = 1800


class MicrogSpoofPatchError(RuntimeError):
    """Raised when the services.jar patch contract does not match."""


# ---------------------------------------------------------------- certificate


def load_certificate_der(path: str | os.PathLike[str]) -> bytes:
    """Read one X.509 certificate (PEM or DER) and return its DER bytes."""

    data = Path(path).read_bytes()
    if b"-----BEGIN CERTIFICATE-----" in data:
        if data.count(b"-----BEGIN CERTIFICATE-----") != 1:
            raise MicrogSpoofPatchError("certificate file must hold exactly one certificate")
        body = data.split(b"-----BEGIN CERTIFICATE-----", 1)[1]
        body = body.split(b"-----END CERTIFICATE-----", 1)[0]
        try:
            data = base64.b64decode(b"".join(body.split()), validate=True)
        except ValueError as exc:
            raise MicrogSpoofPatchError("certificate PEM is not valid base64") from exc
    # A DER certificate is a SEQUENCE with a long-form length.
    if len(data) < 64 or len(data) > 16384 or data[0] != 0x30:
        raise MicrogSpoofPatchError("certificate is not a plausible DER X.509 certificate")
    return data


def certificate_sha256(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def validate_companion_lock(path: Path, cert_der: bytes) -> None:
    """Bind the spoofed signer to the exact Companion selected for installation."""

    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
        modules = [module for module in lock["modules"] if module["id"] == "microg"]
        if len(modules) != 1:
            raise ValueError("expected one microg module")
        companions = [
            artifact for artifact in modules[0]["artifacts"]
            if (artifact.get("apk") or {}).get("package_name") == COMPANION_PACKAGE
        ]
        if len(companions) != 1 or companions[0]["id"] != "companion-apk":
            raise ValueError("expected one Companion APK")
        signer = companions[0]["apk"]["signer_sha256"]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise MicrogSpoofPatchError("invalid microG Companion artifact lock") from exc
    if signer != certificate_sha256(cert_der):
        raise MicrogSpoofPatchError(
            "signer certificate does not match the locked Companion APK; "
            "update the reviewed helper lock and certificate together"
        )


# --------------------------------------------------------------------- smali


def _helper_smali(cert_hex: str) -> str:
    if not re.fullmatch(r"[0-9a-f]+", cert_hex):
        raise MicrogSpoofPatchError("certificate hex contains unexpected characters")
    return f""".class public final {HELPER_TYPE}
.super Ljava/lang/Object;
.source "ModosMicrogSigner.java"

# Generated by ModOS microg_spoof_patch.py. Accepts one extra signer for
# com.android.vending on top of the stock MicrogUtils check.

.method public static isMicrogSigned({_PKG})Z
    .registers 3

    invoke-static {{p0}}, {_MICROG}->isMicrogSigned({_PKG})Z

    move-result v0

    if-nez v0, :done

    invoke-static {{p0}}, {HELPER_TYPE}->isOwnCompanion({_PKG})Z

    move-result v0

    :done
    return v0
.end method

.method public static getFakeSignature({_PKG})Landroid/content/pm/Signature;
    .registers 5

    invoke-static {{p0}}, {_MICROG}->getFakeSignature({_PKG})Landroid/content/pm/Signature;

    move-result-object v0

    if-eqz v0, :try_own

    return-object v0

    :try_own
    invoke-static {{p0}}, {HELPER_TYPE}->isOwnCompanion({_PKG})Z

    move-result v0

    if-eqz v0, :no_signature

    invoke-interface {{p0}}, {_PKG}->getMetaData()Landroid/os/Bundle;

    move-result-object v0

    if-eqz v0, :no_signature

    const-string v1, "fake-signature"

    invoke-virtual {{v0, v1}}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v0

    invoke-static {{v0}}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

    move-result v1

    if-nez v1, :no_signature

    new-instance v1, Landroid/content/pm/Signature;

    invoke-direct {{v1, v0}}, Landroid/content/pm/Signature;-><init>(Ljava/lang/String;)V

    sget-object v0, {_MICROG}->FAKE_SIGNATURE:Landroid/content/pm/Signature;

    invoke-virtual {{v0, v1}}, Landroid/content/pm/Signature;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :no_signature

    return-object v1

    :no_signature
    const/4 v0, 0x0

    return-object v0
.end method

.method private static isOwnCompanion({_PKG})Z
    .registers 6

    invoke-static {{}}, {_MICROG}->isDebuggable()Z

    move-result v0

    if-eqz v0, :no

    invoke-interface {{p0}}, {_PKG}->getPackageName()Ljava/lang/String;

    move-result-object v0

    const-string v1, "{COMPANION_PACKAGE}"

    invoke-virtual {{v1, v0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :no

    invoke-interface {{p0}}, {_PKG}->getSigningDetails()Landroid/content/pm/SigningDetails;

    move-result-object v0

    if-eqz v0, :no

    const/4 v1, 0x1

    new-array v1, v1, [Landroid/content/pm/Signature;

    new-instance v2, Landroid/content/pm/Signature;

    const-string v3, "{cert_hex}"

    invoke-direct {{v2, v3}}, Landroid/content/pm/Signature;-><init>(Ljava/lang/String;)V

    const/4 v3, 0x0

    aput-object v2, v1, v3

    invoke-static {{v0, v1}}, Landroid/content/pm/Signature;->areExactMatch(Landroid/content/pm/SigningDetails;[Landroid/content/pm/Signature;)Z

    move-result v0

    return v0

    :no
    const/4 v0, 0x0

    return v0
.end method
"""


def patch_smali_tree(root: Path, cert_der: bytes) -> None:
    """Redirect the two stock call sites and add the helper class.

    ``root`` is a baksmali output directory holding the dex that defines both
    ``ComputerEngine`` and ``AppsFilterImpl``.
    """

    root = Path(root)
    helper_path = root / HELPER_SMALI_PATH
    if helper_path.exists():
        raise MicrogSpoofPatchError("services.jar is already patched")

    planned: list[tuple[Path, str]] = []
    for relative, method, descriptor in REDIRECTS:
        path = root / relative
        if not path.is_file():
            raise MicrogSpoofPatchError(f"expected class is missing from dex: {relative}")
        text = path.read_text(encoding="utf-8")
        needle = f"{_MICROG}->{method}{descriptor}"
        pattern = re.compile(r"invoke-static (\{[^}]*\}), " + re.escape(needle))
        matches = pattern.findall(text)
        if len(matches) != 1:
            raise MicrogSpoofPatchError(
                f"expected exactly one MicrogUtils.{method} call in {relative}, "
                f"found {len(matches)}"
            )
        patched = pattern.sub(
            lambda m: f"invoke-static {m.group(1)}, {HELPER_TYPE}->{method}{descriptor}",
            text,
        )
        planned.append((path, patched))

    # Every site validated; now write.
    for path, patched in planned:
        path.write_text(patched, encoding="utf-8")
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_text(_helper_smali(cert_der.hex()), encoding="utf-8")


# ----------------------------------------------------------------------- jar


def _dex_entries(archive: zipfile.ZipFile) -> list[str]:
    names = [info.filename for info in archive.infolist() if _DEX_NAME.match(info.filename)]
    if not names:
        raise MicrogSpoofPatchError("services.jar contains no dex files")
    return names


def _run_java(jar: Path, args: list[str]) -> None:
    java = shutil.which("java")
    if java is None:
        raise MicrogSpoofPatchError("java is required to patch services.jar")
    try:
        subprocess.run(
            [java, "-jar", str(jar), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_TOOL_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        tail = exc.stdout.decode("utf-8", "replace")[-2000:]
        raise MicrogSpoofPatchError(f"{jar.name} failed: {tail}") from exc


def _write_aligned_jar(
    source: zipfile.ZipFile,
    destination: Path,
    replacements: dict[str, bytes],
) -> None:
    """Copy a jar, replacing some stored entries and 4-byte aligning stored data."""

    with zipfile.ZipFile(destination, "w") as out:
        for info in source.infolist():
            data = replacements.get(info.filename)
            if data is None:
                data = source.read(info.filename)
            new = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            new.compress_type = info.compress_type
            new.external_attr = info.external_attr
            new.create_system = info.create_system
            if new.compress_type == zipfile.ZIP_STORED:
                offset = out.fp.tell()
                header = 30 + len(info.filename.encode("utf-8"))
                new.extra = b"\x00" * ((-(offset + header)) % 4)
            out.writestr(new, data)


def patch_services_jar(
    services_jar: Path,
    output_jar: Path,
    cert_der: bytes,
    baksmali_jar: Path,
    smali_jar: Path,
    api_level: int,
    work_dir: Path | None = None,
) -> None:
    """Write a patched copy of ``services_jar`` to ``output_jar``."""

    with tempfile.TemporaryDirectory(prefix="modos-spoof-", dir=work_dir) as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(services_jar) as archive:
            target = None
            for name in _dex_entries(archive):
                dex_path = tmp_path / name
                dex_path.write_bytes(archive.read(name))
                out_dir = tmp_path / f"smali-{name}"
                _run_java(
                    Path(baksmali_jar),
                    ["d", str(dex_path), "-o", str(out_dir), "--api", str(api_level)],
                )
                if all((out_dir / rel).is_file() for rel, _, _ in REDIRECTS):
                    target = (name, out_dir)
                    break
                shutil.rmtree(out_dir)
            if target is None:
                raise MicrogSpoofPatchError(
                    "no single dex file defines both ComputerEngine and AppsFilterImpl"
                )
            name, out_dir = target
            patch_smali_tree(out_dir, cert_der)
            new_dex = tmp_path / f"patched-{name}"
            _run_java(
                Path(smali_jar),
                ["a", str(out_dir), "-o", str(new_dex), "--api", str(ASSEMBLE_API)],
            )
            patched_dex = new_dex.read_bytes()
            if patched_dex[:8] != archive.read(name)[:8]:
                raise MicrogSpoofPatchError(
                    f"assembled {name} has dex magic {patched_dex[:8]!r}, "
                    f"expected {archive.read(name)[:8]!r}"
                )
            _write_aligned_jar(archive, Path(output_jar), {name: patched_dex})


def verify_patched_jar(jar: Path, cert_der: bytes) -> str:
    """Cheap structural check of a patched jar; returns the patched dex name."""

    cert_hex = cert_der.hex().encode("ascii")
    with zipfile.ZipFile(jar) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise MicrogSpoofPatchError(f"patched jar has a corrupt entry: {bad}")
        for name in _dex_entries(archive):
            data = archive.read(name)
            if HELPER_TYPE.encode("ascii") in data and cert_hex in data:
                for marker in (b"isOwnCompanion", b"getFakeSignature", b"isMicrogSigned"):
                    if marker not in data:
                        raise MicrogSpoofPatchError(f"patched dex lacks {marker.decode()}")
                return name
    raise MicrogSpoofPatchError("patched jar does not contain the signer helper")


# ------------------------------------------------------------------- download


def fetch_tools(destination: Path) -> dict[str, Path]:
    """Download the pinned baksmali/smali jars, verifying size and SHA-256."""

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for name, (url, sha256, size) in TOOL_DOWNLOADS.items():
        path = destination / name
        if not (path.is_file() and _file_matches(path, sha256, size)):
            partial = destination / f".{name}.part"
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
                with partial.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
            if not _file_matches(partial, sha256, size):
                partial.unlink(missing_ok=True)
                raise MicrogSpoofPatchError(f"{name} failed SHA-256/size verification")
            partial.replace(path)
        result[name] = path
    return result


def _file_matches(path: Path, sha256: str, size: int) -> bool:
    if path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == sha256


# --------------------------------------------------------------- image install


def install_patch(
    ext_fs: dict[str, Any],
    cert_der: bytes,
    baksmali_jar: Path,
    smali_jar: Path,
    api_level: int,
    patcher: Callable[..., None] = patch_services_jar,
) -> None:
    """Patch services.jar in the unpacked system filesystem."""

    fs = ext_fs.get("system")
    if fs is None:
        raise MicrogSpoofPatchError("system partition is missing")

    entries = [e for e in fs.info.entries if e.path == SERVICES_JAR]
    if len(entries) != 1 or entries[0].file_type != "RegularFile":
        raise MicrogSpoofPatchError(f"{SERVICES_JAR} is not a single regular file")
    source = fs.tree / SERVICES_JAR.relative_to("/")
    if source.is_symlink() or not source.is_file():
        raise MicrogSpoofPatchError(f"{SERVICES_JAR} is not a safe regular tree file")

    stale_files = []
    for stale in STALE_AOT_FILES:
        stale_entries = [e for e in fs.info.entries if e.path == stale]
        stale_tree = fs.tree / stale.relative_to("/")
        if len(stale_entries) > 1:
            raise MicrogSpoofPatchError(f"duplicate filesystem metadata: {stale}")
        if stale_entries and stale_entries[0].file_type != "RegularFile":
            raise MicrogSpoofPatchError(f"{stale} is not a regular file")
        if stale_tree.is_symlink():
            raise MicrogSpoofPatchError(f"{stale} is a symlink in the unpacked tree")
        if stale_entries:
            stale_files.append((stale_entries[0], stale_tree))

    with tempfile.TemporaryDirectory(prefix="modos-spoof-out-") as tmp:
        patched = Path(tmp) / "services.jar"
        patcher(source, patched, cert_der, baksmali_jar, smali_jar, api_level)
        verify_patched_jar(patched, cert_der)
        payload = patched.read_bytes()

    with fs.open(str(SERVICES_JAR), "wb", mode=0o644) as stream:
        stream.write(payload)

    for stale_entry, stale_tree in stale_files:
        if stale_tree.exists():
            stale_tree.unlink()
        fs.info.entries.remove(stale_entry)


def _module_class() -> type[Any]:
    # Imported only after ModOS copies this module into the pinned helper.
    from lib.filesystem import CpioFs, ExtFs
    from lib.modules import LegacyCliModule, MissingArgs, ModuleRequirements

    class MicrogSpoofPatchMod(LegacyCliModule):
        NAME = "microg-spoof-patch"

        @classmethod
        def add_args(cls, parser: argparse.ArgumentParser) -> None:
            parser.add_argument(f"--module-{cls.NAME}", type=Path)
            parser.add_argument(f"--module-{cls.NAME}-sig", type=Path)

        @classmethod
        def from_args(cls, args: argparse.Namespace) -> "MicrogSpoofPatchMod":
            if getattr(args, "module_microg_spoof_patch", None) is None:
                raise MissingArgs()
            return cls()

        def requirements(self) -> ModuleRequirements:
            return ModuleRequirements(
                boot_images=set(),
                ext_images={"system"},
                selinux_patching=False,
            )

        def inject(
            self,
            boot_fs: dict[str, CpioFs],
            ext_fs: dict[str, ExtFs],
            sepolicies: Iterable[Path],
            compatible_sepolicy: bool = False,
        ) -> None:
            del boot_fs, sepolicies, compatible_sepolicy
            cert = os.environ.get(CERT_ENVIRONMENT)
            baksmali = os.environ.get(BAKSMALI_ENVIRONMENT)
            smali = os.environ.get(SMALI_ENVIRONMENT)
            api = os.environ.get(API_LEVEL_ENVIRONMENT, "")
            if not (cert and baksmali and smali and api.isdigit()):
                raise MicrogSpoofPatchError(
                    "signer certificate, baksmali/smali jars or API level were not provided"
                )
            install_patch(
                ext_fs,
                load_certificate_der(cert),
                Path(baksmali),
                Path(smali),
                int(api),
            )

    return MicrogSpoofPatchMod


if __name__ != "__main__":
    try:
        MicrogSpoofPatchMod = _module_class()
    except ModuleNotFoundError as exc:
        # Unit tests import the helpers without the downloaded helper.
        if exc.name != "lib":
            raise


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch-tools", help="download pinned baksmali/smali jars")
    fetch.add_argument("destination", type=Path)

    check = sub.add_parser("check-cert", help="validate a signer certificate")
    check.add_argument("certificate", type=Path)

    check_lock = sub.add_parser("check-lock", help="verify the installed Companion signer")
    check_lock.add_argument("lock", type=Path)
    check_lock.add_argument("certificate", type=Path)

    patch = sub.add_parser("patch-jar", help="patch a services.jar (for offline testing)")
    patch.add_argument("services_jar", type=Path)
    patch.add_argument("output_jar", type=Path)
    patch.add_argument("certificate", type=Path)
    patch.add_argument("--tools", type=Path, required=True)
    patch.add_argument("--api", type=int, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "fetch-tools":
            for name, path in fetch_tools(args.destination).items():
                print(f"{name}\t{path}")
        elif args.command == "check-cert":
            print(certificate_sha256(load_certificate_der(args.certificate)))
        elif args.command == "check-lock":
            validate_companion_lock(args.lock, load_certificate_der(args.certificate))
        else:
            der = load_certificate_der(args.certificate)
            tools = fetch_tools(args.tools)
            patch_services_jar(
                args.services_jar,
                args.output_jar,
                der,
                tools["baksmali-3.0.10-fat.jar"],
                tools["smali-3.0.10-fat.jar"],
                args.api,
            )
            print(verify_patched_jar(args.output_jar, der))
    except (MicrogSpoofPatchError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
