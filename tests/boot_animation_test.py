#!/usr/bin/env python3
"""Focused validation tests for the optional local boot animation."""

from __future__ import annotations

import hashlib
import io
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boot_animation import (  # noqa: E402
    MAX_MEMBER_COUNT,
    BootAnimationError,
    build_runtime_payload,
    install_runtime_payload,
    resolve_runtime_payloads,
    validate_payload,
    verify_runtime_installation,
)


def write_valid(path: Path, frame: bytes = b"frame") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
        archive.writestr("part0/frame.png", frame)


def write_valid_with_timestamp(
    path: Path,
    timestamp: tuple[int, int, int, int, int, int],
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in (
            ("desc.txt", b"1 1 1\np 1 0 part0\n"),
            ("part0/frame.png", b"frame"),
        ):
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def assert_rejected(path: Path, context: str) -> None:
    try:
        validate_payload(path)
    except BootAnimationError:
        return
    raise AssertionError(f"{context}: invalid archive was accepted")


class FakeExtFs:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.mkdir_calls: list[tuple[str, int, bool, bool]] = []
        self.open_calls: list[tuple[str, str, int]] = []
        self.contexts = [
            (re.compile(r"/product(/.*)?"), "u:object_r:system_file:s0"),
        ]

    @property
    def tree(self) -> Path:
        raise AssertionError("boot animation injection bypassed ExtFs metadata APIs")

    def mkdir(
        self,
        path: str,
        mode: int = 0o755,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        self.mkdir_calls.append((path, mode, parents, exist_ok))
        (self.root / path.lstrip("/")).mkdir(
            mode=mode,
            parents=parents,
            exist_ok=exist_ok,
        )

    def open(self, path: str, open_mode: str, mode: int = 0o644):
        self.open_calls.append((path, open_mode, mode))
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.open(open_mode)


def test_runtime_payload_installation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source.zip"
        write_valid(source)

        runtime = build_runtime_payload(source)
        with zipfile.ZipFile(io.BytesIO(runtime), "r") as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
            assert files
            assert all(info.compress_type == zipfile.ZIP_STORED for info in files)
            assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in files)

        timestamp_a = root / "timestamp-a.zip"
        timestamp_b = root / "timestamp-b.zip"
        write_valid_with_timestamp(timestamp_a, (2020, 1, 2, 3, 4, 6))
        write_valid_with_timestamp(timestamp_b, (2026, 9, 25, 10, 58, 20))
        assert build_runtime_payload(timestamp_a) == build_runtime_payload(timestamp_b)

        product = FakeExtFs(root / "product-fs")
        install_runtime_payload({"product": product}, runtime)

        expected_targets = [
            "/media/bootanimation.zip",
            "/media/bootanimation-dark.zip",
        ]
        assert [call[0] for call in product.open_calls] == expected_targets
        assert all(call[1:] == ("wb", 0o644) for call in product.open_calls)
        assert all(call[0] == "/media" for call in product.mkdir_calls)
        for target in expected_targets:
            assert (product.root / target.lstrip("/")).read_bytes() == runtime

        expected_alias_paths = {
            "/media",
            "/media/bootanimation.zip",
            "/media/bootanimation-dark.zip",
        }
        alias_labels = {
            path: next(
                label
                for pattern, label in product.contexts
                if pattern.fullmatch(path)
            )
            for path in expected_alias_paths
        }
        assert set(alias_labels.values()) == {"u:object_r:system_file:s0"}

        # A standalone product.img is mounted at /product. Its filesystem root
        # therefore contains /media, not another nested /product directory.
        assert not (product.root / "product").exists()

        light_runtime, dark_runtime = resolve_runtime_payloads(source, None)
        assert light_runtime == dark_runtime == runtime
        verify_runtime_installation(
            source,
            source,
            product.root / "media/bootanimation.zip",
            product.root / "media/bootanimation-dark.zip",
        )

        dark_source = root / "dark-source.zip"
        write_valid(dark_source, frame=b"dark-frame")
        dark_runtime = build_runtime_payload(dark_source)
        themed_product = FakeExtFs(root / "themed-product-fs")
        resolved_light, resolved_dark = resolve_runtime_payloads(source, dark_source)
        assert resolved_light == runtime
        assert resolved_dark == dark_runtime
        install_runtime_payload(
            {"product": themed_product},
            resolved_light,
            resolved_dark,
        )
        assert (
            themed_product.root / "media/bootanimation.zip"
        ).read_bytes() == runtime
        assert (
            themed_product.root / "media/bootanimation-dark.zip"
        ).read_bytes() == dark_runtime
        verify_runtime_installation(
            source,
            dark_source,
            themed_product.root / "media/bootanimation.zip",
            themed_product.root / "media/bootanimation-dark.zip",
        )

        dark_only_light, dark_only_dark = resolve_runtime_payloads(None, dark_source)
        assert dark_only_light == dark_only_dark == dark_runtime

        try:
            resolve_runtime_payloads(None, None)
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing light and dark payloads were accepted")

        missing_contexts = FakeExtFs(root / "missing-contexts")
        missing_contexts.contexts = []
        try:
            install_runtime_payload({"product": missing_contexts}, runtime)
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing runtime SELinux context was accepted")

        try:
            install_runtime_payload({}, runtime)
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing product partition was accepted")


def main() -> None:
    test_runtime_payload_installation()
    checked_in = Path("custom/boot-animation/bootanimation.zip")
    expected = hashlib.sha256(checked_in.read_bytes()).hexdigest()
    assert validate_payload(checked_in) == expected

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        valid = root / "bootanimation.zip"
        write_valid(valid)
        expected = hashlib.sha256(valid.read_bytes()).hexdigest()
        assert validate_payload(valid) == expected

        desc_only = root / "desc-only.zip"
        with zipfile.ZipFile(desc_only, "w") as archive:
            archive.writestr("desc.txt", "1 1 1\n")
        assert_rejected(desc_only, "desc-only archive")

        missing_desc = root / "missing-desc.zip"
        with zipfile.ZipFile(missing_desc, "w") as archive:
            archive.writestr("part0/frame.png", b"frame")
        assert_rejected(missing_desc, "missing desc")

        malformed = root / "malformed.zip"
        malformed.write_bytes(b"not a zip")
        assert_rejected(malformed, "malformed archive")

        traversal = root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
            archive.writestr("../outside", b"escape")
        assert_rejected(traversal, "path traversal")

        duplicate = root / "duplicate.zip"
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
            archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
            archive.writestr("part0/frame.png", b"frame")
        assert_rejected(duplicate, "duplicate member")

        encrypted = root / "encrypted.zip"
        with zipfile.ZipFile(encrypted, "w") as archive:
            archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
            archive.writestr("part0/frame.png", b"frame")
        encrypted_data = bytearray(encrypted.read_bytes())
        for marker, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            cursor = 0
            while True:
                cursor = encrypted_data.find(marker, cursor)
                if cursor < 0:
                    break
                flags = int.from_bytes(
                    encrypted_data[cursor + offset : cursor + offset + 2], "little"
                ) | 0x1
                encrypted_data[cursor + offset : cursor + offset + 2] = flags.to_bytes(2, "little")
                cursor += len(marker)
        encrypted.write_bytes(encrypted_data)
        assert_rejected(encrypted, "encrypted member")

        symlink_member = root / "symlink-member.zip"
        with zipfile.ZipFile(symlink_member, "w") as archive:
            archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
            info = zipfile.ZipInfo("part0/frame.png")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"frame")
        assert_rejected(symlink_member, "symlink member")

        excessive = root / "excessive.zip"
        with zipfile.ZipFile(excessive, "w") as archive:
            for index in range(MAX_MEMBER_COUNT + 1):
                archive.writestr(f"part0/frame-{index}.png", b"x")
        assert_rejected(excessive, "member count")

        extended_description = root / "extended-description.zip"
        with zipfile.ZipFile(extended_description, "w") as archive:
            archive.writestr(
                "desc.txt",
                "1440 1440 30\np 0 0 part0 #000000 -1\n",
            )
            archive.writestr("part0/frame.png", b"frame")
        validate_payload(extended_description)

        invalid_description = root / "invalid-description.zip"
        with zipfile.ZipFile(invalid_description, "w") as archive:
            archive.writestr("desc.txt", "not a boot animation\n")
            archive.writestr("part0/frame.png", b"frame")
        assert_rejected(invalid_description, "invalid description")

        compressed_bomb = root / "compression-ratio.zip"
        with zipfile.ZipFile(compressed_bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("desc.txt", "1 1 1\np 1 0 part0\n")
            archive.writestr("part0/frame.png", b"0" * (2 * 1024 * 1024))
        assert_rejected(compressed_bomb, "compression ratio")

        symlink = root / "symlink.zip"
        symlink.symlink_to(valid)
        assert_rejected(symlink, "payload symlink")

    print("boot animation validation tests passed")


if __name__ == "__main__":
    main()
