#!/usr/bin/env python3
"""Tests for the microG Companion signer services.jar patch.

These cover the smali rewrite, jar rebuilding, certificate parsing and image
installation contract. They do not run baksmali/smali (Java); the rewrite is
checked on a synthetic smali tree and the installer with a stub patcher.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import microg_spoof_patch as m  # noqa: E402
from microg_spoof_patch import (  # noqa: E402
    HELPER_SMALI_PATH,
    HELPER_TYPE,
    MicrogSpoofPatchError,
    REDIRECTS,
)

CERT_DER = b"\x30\x82\x01\x00" + bytes(range(256)) * 2
CERT_HEX = CERT_DER.hex()


def assert_raises(func, context: str) -> MicrogSpoofPatchError:
    try:
        func()
    except MicrogSpoofPatchError as exc:
        return exc
    raise AssertionError(f"{context}: expected MicrogSpoofPatchError")


def write_tree(root: Path, counts: dict[str, int] | None = None) -> None:
    """Create ComputerEngine/AppsFilterImpl smali with N stock call sites."""

    counts = counts or {}
    for relative, method, descriptor in REDIRECTS:
        call = (
            f"    invoke-static {{v0}}, {m._MICROG}->{method}{descriptor}\n"
            "    move-result-object p0\n"
        )
        other = (
            f"    invoke-static {{v0}}, {m._MICROG}->isDebuggable()Z\n"
            if "AppsFilter" not in relative
            else ""
        )
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            ".class public Lfixture;\n.method m()V\n"
            + call * counts.get(relative, 1)
            + other
            + ".end method\n"
        )


def test_certificate_loading() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        der = root / "cert.der"
        der.write_bytes(CERT_DER)
        assert m.load_certificate_der(der) == CERT_DER

        pem = root / "cert.pem"
        b64 = base64.b64encode(CERT_DER).decode()
        pem.write_text(
            "-----BEGIN CERTIFICATE-----\n"
            + "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
            + "\n-----END CERTIFICATE-----\n"
        )
        assert m.load_certificate_der(pem) == CERT_DER

        two = root / "two.pem"
        two.write_text(pem.read_text() * 2)
        assert_raises(lambda: m.load_certificate_der(two), "two certificates")

        bad_b64 = root / "bad.pem"
        bad_b64.write_text("-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n")
        assert_raises(lambda: m.load_certificate_der(bad_b64), "invalid base64")

        junk = root / "junk"
        junk.write_bytes(b"not a certificate" * 10)
        assert_raises(lambda: m.load_certificate_der(junk), "non-DER data")

        private = root / "key.pem"
        # Built from parts so secret scanners do not flag this dummy fixture.
        marker = "PRIVATE " + "KEY-----"
        private.write_text(f"-----BEGIN {marker}\nAAAA\n-----END {marker}\n")
        assert_raises(lambda: m.load_certificate_der(private), "private key")


def test_smali_rewrite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree(root)
        m.patch_smali_tree(root, CERT_DER)

        for relative, method, descriptor in REDIRECTS:
            text = (root / relative).read_text()
            assert f"{m._MICROG}->{method}" not in text
            assert f"{HELPER_TYPE}->{method}{descriptor}" in text
        # Unrelated MicrogUtils callers are untouched.
        engine = (root / "com/android/server/pm/ComputerEngine.smali").read_text()
        assert f"{m._MICROG}->isDebuggable()Z" in engine

        helper = (root / HELPER_SMALI_PATH).read_text()
        assert f'const-string v3, "{CERT_HEX}"' in helper
        assert '"com.android.vending"' in helper
        assert "isDebuggable" in helper  # same gate as the stock check
        assert f"{m._MICROG}->isMicrogSigned" in helper  # stock check runs first
        assert "gms" not in helper  # Services is deliberately not extended


def test_companion_lock_requires_the_installed_signer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lock.json"
        companion = {
            "id": "companion-apk",
            "apk": {"package_name": "com.android.vending",
                    "signer_sha256": m.certificate_sha256(CERT_DER)},
        }
        lock = {"modules": [{"id": "microg", "artifacts": [companion]}]}
        path.write_text(json.dumps(lock))
        m.validate_companion_lock(path, CERT_DER)
        assert_raises(lambda: m.validate_companion_lock(path, CERT_DER + b"x"), "other signer")
        for artifacts in ([], [companion, companion], [{"id": "other", "apk": None}]):
            lock["modules"][0]["artifacts"] = artifacts
            path.write_text(json.dumps(lock))
            assert_raises(lambda: m.validate_companion_lock(path, CERT_DER), "ambiguous lock")


def test_smali_rewrite_fails_closed() -> None:
    for relative, _, _ in REDIRECTS:
        for count in (0, 2):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_tree(root, {relative: count})
                before = {p: p.read_text() for p in root.rglob("*.smali")}
                assert_raises(
                    lambda: m.patch_smali_tree(root, CERT_DER),
                    f"{count} call sites in {relative}",
                )
                # Nothing is written when any site fails validation.
                assert before == {p: p.read_text() for p in root.rglob("*.smali")}
                assert not (root / HELPER_SMALI_PATH).exists()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree(root)
        (root / REDIRECTS[1][0]).unlink()
        assert_raises(lambda: m.patch_smali_tree(root, CERT_DER), "missing class")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree(root)
        m.patch_smali_tree(root, CERT_DER)
        assert_raises(lambda: m.patch_smali_tree(root, CERT_DER), "already patched")


def make_jar(path: Path, dex: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data, kind in (
            ("classes.dex", b"dex\n039\0" + b"A" * 40, zipfile.ZIP_STORED),
            ("classes2.dex", dex, zipfile.ZIP_STORED),
            ("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n", zipfile.ZIP_DEFLATED),
        ):
            info = zipfile.ZipInfo(name, date_time=(2008, 1, 1, 0, 0, 0))
            info.compress_type = kind
            archive.writestr(info, data)


def test_jar_rewrite_and_verification() -> None:
    patched_dex = b"dex\n039\0" + HELPER_TYPE.encode() + CERT_HEX.encode() + (
        b"isOwnCompanion getFakeSignature isMicrogSigned"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "services.jar"
        out = root / "out.jar"
        make_jar(source, b"dex\n039\0" + b"B" * 40)
        with zipfile.ZipFile(source) as archive:
            m._write_aligned_jar(archive, out, {"classes2.dex": patched_dex})

        with zipfile.ZipFile(out) as archive, zipfile.ZipFile(source) as original:
            assert [i.filename for i in archive.infolist()] == [
                i.filename for i in original.infolist()
            ]
            assert archive.read("classes2.dex") == patched_dex
            assert archive.read("classes.dex") == original.read("classes.dex")
            kinds = {i.filename: i.compress_type for i in archive.infolist()}
            assert kinds["classes2.dex"] == zipfile.ZIP_STORED
            assert kinds["META-INF/MANIFEST.MF"] == zipfile.ZIP_DEFLATED
            # Stored dex data must be 4-byte aligned for mmap.
            for info in archive.infolist():
                if info.compress_type == zipfile.ZIP_STORED:
                    data_offset = (
                        info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
                    )
                    assert data_offset % 4 == 0, info.filename

        assert m.verify_patched_jar(out, CERT_DER) == "classes2.dex"
        # The stock jar and a jar with a different certificate are rejected.
        assert_raises(lambda: m.verify_patched_jar(source, CERT_DER), "stock jar")
        assert_raises(lambda: m.verify_patched_jar(out, CERT_DER + b"x"), "other cert")


class FakeFs:
    def __init__(self, root: Path, files: dict[str, bytes], types: dict[str, str] | None = None):
        self.tree = root
        self.written: dict[str, bytes] = {}
        entries = []
        for raw, data in files.items():
            path = PurePosixPath(raw)
            target = root / path.relative_to("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            entries.append(
                SimpleNamespace(path=path, file_type=(types or {}).get(raw, "RegularFile"))
            )
        self.info = SimpleNamespace(entries=entries)

    def open(self, path: str, open_mode: str, **kwargs):
        assert open_mode == "wb" and kwargs == {"mode": 0o644}
        fs = self

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def write(self, data: bytes):
                fs.written[path] = data

        return Stream()


def stub_patcher(source, output, cert_der, baksmali, smali, api_level):
    assert Path(source).read_bytes() == b"stock jar"
    assert api_level == 37
    dex = b"dex\n039\0" + HELPER_TYPE.encode() + cert_der.hex().encode() + (
        b"isOwnCompanion getFakeSignature isMicrogSigned"
    )
    make_jar(Path(output), dex)


def test_install_patch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        files = {"/system/framework/services.jar": b"stock jar"}
        for abi in ("arm64",):
            for ext in ("odex", "vdex", "art"):
                files[f"/system/framework/oat/{abi}/services.{ext}"] = b"aot"
        files["/system/framework/oat/arm64/other.odex"] = b"keep"
        fs = FakeFs(Path(tmp), files)
        m.install_patch(
            {"system": fs}, CERT_DER, Path("b.jar"), Path("s.jar"), 37, patcher=stub_patcher
        )
        assert list(fs.written) == ["/system/framework/services.jar"]
        assert zipfile.ZipFile(io.BytesIO(fs.written["/system/framework/services.jar"])).testzip() is None
        remaining = {str(e.path) for e in fs.info.entries}
        assert remaining == {
            "/system/framework/services.jar",
            "/system/framework/oat/arm64/other.odex",
        }
        assert not (Path(tmp) / "system/framework/oat/arm64/services.odex").exists()
        assert (Path(tmp) / "system/framework/oat/arm64/other.odex").exists()

    with tempfile.TemporaryDirectory() as tmp:
        assert_raises(
            lambda: m.install_patch({}, CERT_DER, Path("b"), Path("s"), 37, patcher=stub_patcher),
            "missing system partition",
        )

    with tempfile.TemporaryDirectory() as tmp:
        fs = FakeFs(Path(tmp), {"/system/framework/other.jar": b"x"})
        assert_raises(
            lambda: m.install_patch(
                {"system": fs}, CERT_DER, Path("b"), Path("s"), 37, patcher=stub_patcher
            ),
            "missing services.jar",
        )

    with tempfile.TemporaryDirectory() as tmp:
        fs = FakeFs(
            Path(tmp),
            {"/system/framework/services.jar": b"stock jar"},
            {"/system/framework/services.jar": "Symlink"},
        )
        assert_raises(
            lambda: m.install_patch(
                {"system": fs}, CERT_DER, Path("b"), Path("s"), 37, patcher=stub_patcher
            ),
            "services.jar not a regular file",
        )

    # A patcher failure must leave the image untouched.
    with tempfile.TemporaryDirectory() as tmp:
        fs = FakeFs(Path(tmp), {"/system/framework/services.jar": b"stock jar"})

        def failing(*args, **kwargs):
            raise MicrogSpoofPatchError("boom")

        assert_raises(
            lambda: m.install_patch(
                {"system": fs}, CERT_DER, Path("b"), Path("s"), 37, patcher=failing
            ),
            "patcher failure",
        )
        assert fs.written == {}

    # A patcher that produces a jar without the helper is rejected.
    with tempfile.TemporaryDirectory() as tmp:
        fs = FakeFs(Path(tmp), {"/system/framework/services.jar": b"stock jar"})

        def noop(source, output, *args, **kwargs):
            make_jar(Path(output), b"dex\n039\0" + b"C" * 40)

        assert_raises(
            lambda: m.install_patch(
                {"system": fs}, CERT_DER, Path("b"), Path("s"), 37, patcher=noop
            ),
            "unpatched output",
        )
        assert fs.written == {}


def test_tool_pins() -> None:
    assert set(m.TOOL_DOWNLOADS) == {"baksmali-3.0.10-fat.jar", "smali-3.0.10-fat.jar"}
    for name, (url, sha256, size) in m.TOOL_DOWNLOADS.items():
        assert url.startswith("https://github.com/baksmali/smali/releases/download/3.0.10/")
        assert url.endswith(name)
        assert len(sha256) == 64 and int(sha256, 16) >= 0
        assert size > 1_000_000
    # smali 3.0.10 emits a header ART rejects above API 29 (see the comment).
    assert m.ASSEMBLE_API == 29


def test_invalid_aot_metadata_is_rejected_before_jar_writes() -> None:
    stale = "/system/framework/oat/arm64/services.odex"
    for invalid in ("duplicate", "symlink", "nonregular"):
        with tempfile.TemporaryDirectory() as tmp:
            fs = FakeFs(Path(tmp), {
                "/system/framework/services.jar": b"stock jar", stale: b"aot",
            })
            if invalid == "duplicate":
                fs.info.entries.append(fs.info.entries[-1])
            elif invalid == "nonregular":
                fs.info.entries[-1].file_type = "Directory"
            else:
                path = Path(tmp) / stale.lstrip("/")
                path.unlink()
                path.symlink_to(Path(tmp) / "elsewhere")
            assert_raises(lambda: m.install_patch(
                {"system": fs}, CERT_DER, Path("b"), Path("s"), 37,
                patcher=stub_patcher,
            ), invalid)
            assert fs.written == {}, "invalid AOT metadata changed services.jar"


def test_fetch_rejects_tampered_download() -> None:
    original = m.urllib.request.urlopen

    class Response:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size: int = -1) -> bytes:
            data, self.data = self.data, b""
            return data

    m.urllib.request.urlopen = lambda url, timeout=0: Response(b"tampered")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            assert_raises(lambda: m.fetch_tools(Path(tmp)), "tampered tool download")
            assert not list(Path(tmp).glob("*.jar"))
            assert not list(Path(tmp).glob(".*.part"))
    finally:
        m.urllib.request.urlopen = original


def main() -> None:
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
    print("microG Companion signer patch tests passed")


if __name__ == "__main__":
    main()
