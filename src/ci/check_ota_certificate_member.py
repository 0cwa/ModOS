#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors
"""Fail closed if a source OTA's certificate differs from the extracted cert.

This is only a source/extraction consistency check. The caller must independently
pin the LineageOS publisher SPKI, and the helper must still verify the signed
OTA *bytes* with that independently trusted certificate before modifying it.
"""
from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path

MEMBER = "META-INF/com/android/otacert"
MAX_PEM_BYTES = 65536


def check_archive_certificate(archive: Path, certificate: Path) -> None:
    for path in (archive, certificate):
        if path.is_symlink() or not path.is_file():
            raise ValueError("OTA/certificate must be regular, non-symlink files")
    cert_size = certificate.stat().st_size
    if not 0 < cert_size <= MAX_PEM_BYTES:
        raise ValueError("extracted OTA certificate size is invalid")
    with zipfile.ZipFile(archive, "r", allowZip64=True) as zf:
        entries = [entry for entry in zf.infolist() if entry.filename == MEMBER]
        if len(entries) != 1:
            raise ValueError("source OTA must contain exactly one otacert entry")
        member = entries[0]
        mode = stat.S_IFMT(member.external_attr >> 16)
        if (member.is_dir() or member.flag_bits & 1
                or mode not in (0, stat.S_IFREG)
                or not 0 < member.file_size <= MAX_PEM_BYTES):
            raise ValueError("source OTA otacert ZIP member is invalid")
        with zf.open(member, "r") as source:
            embedded = source.read(MAX_PEM_BYTES + 1)
        if len(embedded) != member.file_size:
            raise ValueError("source OTA otacert size changed while reading")

    extracted = certificate.read_bytes()
    if extracted != embedded:
        raise ValueError("extracted OTA certificate does not match current source ZIP")
    if embedded.count(b"-----BEGIN CERTIFICATE-----") != 1 or (
        embedded.count(b"-----END CERTIFICATE-----") != 1
    ):
        raise ValueError("source OTA must contain exactly one PEM certificate")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: check_ota_certificate_member.py SOURCE_ZIP EXTRACTED_CERT", file=sys.stderr)
        return 2
    try:
        check_archive_certificate(Path(argv[1]), Path(argv[2]))
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as error:
        print(f"Error: source OTA certificate consistency check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
