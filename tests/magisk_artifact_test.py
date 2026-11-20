#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise Magisk artifact identity, cache replacement and finished boot payloads.

Fixtures are small synthetic APKs, authenticated by their test-specific digest.
They deliberately do not claim to be signed or executable upstream releases.
The real upstream APK/avbroot integration is covered by the separate smoke test.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import lzma
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import magisk_artifact as magisk  # noqa: E402


REJECTED = (magisk.MagiskError, OSError, zipfile.BadZipFile, lzma.LZMAError)


class MagiskSelectionTest(unittest.TestCase):
    def test_official_latest_and_rollback_are_reviewed_selections(self):
        for version, code, size, digest in (
            ("v31.1", 31100, 10679283,
             "89cefac0a318afa39fb3880c45c87b036c05bc1ebf02a7a715b7f8d7c26ac6e7"),
            ("v30.7", 30700, 11613864,
             "e0d32d2123532860f97123d927b1bb86c4e08e6fd8a48bfc6b5bee0afae9ebd5"),
        ):
            with self.subTest(version=version):
                selected = magisk.resolve_artifact("topjohnwu/Magisk", version)
                self.assertEqual((selected.version_code, selected.size, selected.sha256),
                                 (code, size, digest))
                self.assertEqual(
                    magisk.resolve_artifact("topjohnwu/Magisk", version, digest), selected)
                with self.assertRaises(magisk.MagiskError):
                    magisk.resolve_artifact("topjohnwu/Magisk", version, "0" * 64)

    def test_unreviewed_version_and_fork_require_explicit_digest(self):
        for repository, version in (("someone/Magisk", "v31.1"),
                                    ("topjohnwu/Magisk", "v99.0")):
            with self.subTest(repository=repository, version=version):
                with self.assertRaises(magisk.MagiskError):
                    magisk.resolve_artifact(repository, version)
                selected = magisk.resolve_artifact(repository, version, "a" * 64)
                self.assertEqual((selected.repository, selected.version, selected.sha256),
                                 (repository, version, "a" * 64))
                self.assertIsNone(selected.version_code)
                self.assertIsNone(selected.size)

    def test_unreviewed_selection_rejects_ambiguous_inputs(self):
        cases = (
            ("https://github.com/someone/Magisk", "v31.1", "a" * 64),
            ("someone/Magisk/other", "v31.1", "a" * 64),
            ("../Magisk", "v31.1", "a" * 64),
            ("someone/..", "v31.1", "a" * 64),
            ("someone/Magisk", "latest", "a" * 64),
            ("someone/Magisk", "v31.1/other", "a" * 64),
            ("someone/Magisk", "v31.1", "a" * 63),
            ("someone/Magisk", "v31.1", "g" * 64),
        )
        for args in cases:
            with self.subTest(args=args), self.assertRaises(magisk.MagiskError):
                magisk.resolve_artifact(*args)


class ArtifactFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.apk = self.root / "selected.apk"
        self.artifact, self.members = self.write_apk()

    def write_apk(self, *, path=None, version="31.1", code=31100, abi="arm64-v8a",
                  stub=False, init_ld=True, comment=None, script=None, omit=(),
                  duplicate=None, salt=b"", payload_overrides=None):
        path = self.apk if path is None else path
        # Distinct ELF-shaped bytes make selecting the wrong ABI observable.
        elf = bytearray(64)
        elf[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<H", elf, 18, 183 if abi == "arm64-v8a" else 62)
        members = {
            "assets/util_functions.sh": (
                f"MAGISK_VER='{version}'\nMAGISK_VER_CODE={code}\n".encode()
                if script is None else script
            ),
            f"lib/{abi}/libmagiskinit.so": bytes(elf) + b"init:" + salt,
            f"lib/{abi}/libmagisk.so": bytes(elf) + b"core:" + salt,
        }
        if init_ld:
            members[f"lib/{abi}/libinit-ld.so"] = bytes(elf) + b"init-ld:" + salt
        if stub:
            members["assets/stub.apk"] = b"stub APK fixture"
        members.update(payload_overrides or {})
        for name in omit:
            members.pop(name)
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members.items():
                archive.writestr(zipfile.ZipInfo(name), content)
            if duplicate is not None:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(zipfile.ZipInfo(duplicate), members[duplicate])
            archive.comment = (
                f"version={version}\nversionCode={code}\n".encode()
                if comment is None else comment
            )
        payload = path.read_bytes()
        artifact = magisk.MagiskArtifact(
            repository="test/Magisk", version=f"v{version}",
            sha256=hashlib.sha256(payload).hexdigest(), size=len(payload),
            version_code=code,
        )
        return artifact, members

    def write_ramdisk(self, members=None, *, name="ramdisk", abi="arm64-v8a", xz_preset=6):
        members = self.members if members is None else members
        root = self.root / name
        binary_dir = root / "overlay.d/sbin"
        binary_dir.mkdir(parents=True)
        (root / "init").write_bytes(members[f"lib/{abi}/libmagiskinit.so"])
        payloads = {"magisk.xz": f"lib/{abi}/libmagisk.so",
                    "init-ld.xz": f"lib/{abi}/libinit-ld.so",
                    "stub.xz": "assets/stub.apk"}
        for filename, member in payloads.items():
            if member in members:
                (binary_dir / filename).write_bytes(lzma.compress(
                    members[member], preset=xz_preset, check=lzma.CHECK_NONE))
        return root

    def ramdisk_entries(self, members=None, *, abi="arm64-v8a"):
        members = self.members if members is None else members
        entries = [
            {"path": "init", "file_type": "Regular", "file_mode": "750"},
            {"path": "overlay.d", "file_type": "Directory", "file_mode": "750"},
            {"path": "overlay.d/sbin", "file_type": "Directory", "file_mode": "750"},
        ]
        for filename, member in (
            ("magisk.xz", f"lib/{abi}/libmagisk.so"),
            ("init-ld.xz", f"lib/{abi}/libinit-ld.so"),
            ("stub.xz", "assets/stub.apk"),
        ):
            if member in members:
                entries.append({"path": f"overlay.d/sbin/{filename}",
                                "file_type": "Regular", "file_mode": "644"})
        return entries

    def write_cpio_info(self, entries=None):
        entries = self.ramdisk_entries() if entries is None else entries
        metadata = self.root / "cpio.toml"
        lines = ['format = "Gzip"']
        for entry in entries:
            lines.extend(("", "[[entries]]"))
            lines.extend(f"{key} = {json.dumps(value)}" for key, value in entry.items())
        metadata.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return metadata


class MagiskApkTest(ArtifactFixture):
    def test_current_apk_without_stub_is_accepted(self):
        report = magisk.verify_apk(self.apk, self.artifact)
        self.assertEqual(report["version"], "31.1")
        self.assertEqual(report["version_code"], 31100)
        self.assertEqual(report["sha256"], self.artifact.sha256)
        self.assertEqual(report["size"], self.apk.stat().st_size)

    def test_previous_apk_with_stub_and_extra_comment_is_accepted(self):
        artifact, _ = self.write_apk(version="30.7", code=30700, stub=True,
                                     comment=b"version=30.7\nversionCode=30700\nstubVersion=40\n")
        self.assertEqual(magisk.verify_apk(self.apk, artifact)["version_code"], 30700)

    def test_x86_64_payload_and_absent_optional_init_ld_are_accepted(self):
        artifact, _ = self.write_apk(abi="x86_64", init_ld=False)
        self.assertEqual(magisk.verify_apk(self.apk, artifact)["version"], "31.1")

    def test_stale_or_tampered_apk_cannot_satisfy_selected_digest(self):
        self.write_apk(salt=b"different build")
        with self.assertRaises(magisk.MagiskError):
            magisk.verify_apk(self.apk, self.artifact)

    def test_size_is_verified_independently_of_digest(self):
        with self.assertRaises(magisk.MagiskError):
            magisk.verify_apk(self.apk, replace(self.artifact, size=self.artifact.size + 1))

    def test_truncated_and_non_zip_bytes_are_rejected_even_with_matching_digest(self):
        original = self.apk.read_bytes()
        for raw in (original[:100], b"not an APK archive"):
            with self.subTest(length=len(raw)):
                self.apk.write_bytes(raw)
                artifact = replace(self.artifact, size=len(raw),
                                   sha256=hashlib.sha256(raw).hexdigest())
                with self.assertRaises(REJECTED):
                    magisk.verify_apk(self.apk, artifact)

    def test_comment_and_script_versions_must_match_selection(self):
        cases = (
            {"comment": b"version=30.7\nversionCode=31100\n"},
            {"comment": b"version=31.1\nversionCode=30700\n"},
            {"comment": b"version=31.1\n"},
            {"comment": b"version=31.1\nversionCode=31100\nversionCode=31100\n"},
            {"comment": b""},
            {"script": b"MAGISK_VER='30.7'\nMAGISK_VER_CODE=31100\n"},
            {"script": b"MAGISK_VER='31.1'\nMAGISK_VER_CODE=30700\n"},
            {"script": b"MAGISK_VER='31.1'\nMAGISK_VER_CODE=31100\nMAGISK_VER_CODE=31100\n"},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                artifact, _ = self.write_apk(**kwargs)
                with self.assertRaises(magisk.MagiskError):
                    magisk.verify_apk(self.apk, artifact)

    def test_locked_version_code_is_checked_even_when_apk_metadata_agrees(self):
        artifact, _ = self.write_apk(code=30700)
        with self.assertRaises(magisk.MagiskError):
            magisk.verify_apk(self.apk, replace(artifact, version_code=31100))

    def test_duplicate_zip_entries_are_rejected(self):
        artifact, _ = self.write_apk(duplicate="lib/arm64-v8a/libmagisk.so")
        with self.assertRaises(magisk.MagiskError):
            magisk.verify_apk(self.apk, artifact)

    def test_missing_script_or_required_native_payload_is_rejected(self):
        for name in ("assets/util_functions.sh", "lib/arm64-v8a/libmagiskinit.so",
                     "lib/arm64-v8a/libmagisk.so"):
            with self.subTest(name=name):
                artifact, _ = self.write_apk(omit=(name,))
                with self.assertRaises(magisk.MagiskError):
                    magisk.verify_apk(self.apk, artifact)

    def test_empty_native_or_bootstrap_payload_is_rejected_during_apk_preflight(self):
        for name in ("lib/arm64-v8a/libmagiskinit.so", "lib/arm64-v8a/libmagisk.so",
                     "lib/arm64-v8a/libinit-ld.so", "assets/stub.apk"):
            with self.subTest(name=name):
                artifact, _ = self.write_apk(payload_overrides={name: b""})
                with self.assertRaises(magisk.MagiskError):
                    magisk.verify_apk(self.apk, artifact)


class MagiskDownloadTest(ArtifactFixture):
    def test_verified_cached_apk_needs_no_network(self):
        with mock.patch.object(magisk, "download_https") as network:
            report = magisk.download_apk(self.apk, self.artifact)
        network.assert_not_called()
        self.assertEqual(report["sha256"], self.artifact.sha256)

    def test_stale_cache_is_replaced_only_after_validating_download(self):
        selected = self.apk.read_bytes()
        stale = b"an APK left by a different build"
        self.apk.write_bytes(stale)

        def download(url, temporary, maximum):
            self.assertEqual(self.apk.read_bytes(), stale)
            self.assertNotEqual(temporary, self.apk)
            self.assertEqual(url, "https://github.com/test/Magisk/releases/download/v31.1/Magisk-v31.1.apk")
            temporary.write_bytes(selected)
            return len(selected), hashlib.sha256(selected).hexdigest()

        with mock.patch.object(magisk, "download_https", side_effect=download) as network:
            report = magisk.download_apk(self.apk, self.artifact)
        network.assert_called_once()
        self.assertEqual(self.apk.read_bytes(), selected)
        self.assertEqual(report["sha256"], self.artifact.sha256)
        self.assertEqual(set(self.root.iterdir()), {self.apk})

    def test_failed_download_never_overwrites_existing_apk_or_leaves_partial(self):
        original = b"existing cached APK"
        self.apk.write_bytes(original)

        def download(url, temporary, maximum):
            temporary.write_bytes(b"partial network response")
            raise OSError("simulated disconnected transfer")

        with mock.patch.object(magisk, "download_https", side_effect=download):
            with self.assertRaises(OSError):
                magisk.download_apk(self.apk, self.artifact)
        self.assertEqual(self.apk.read_bytes(), original)
        self.assertEqual(set(self.root.iterdir()), {self.apk})

    def test_untrusted_download_is_not_installed(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.apk.write_bytes(b"existing cached APK")
                else:
                    self.apk.unlink(missing_ok=True)

                def download(url, temporary, maximum):
                    temporary.write_bytes(b"wrong APK")
                    return 9, "0" * 64

                with mock.patch.object(magisk, "download_https", side_effect=download):
                    with self.assertRaises(magisk.MagiskError):
                        magisk.download_apk(self.apk, self.artifact)
                if existing:
                    self.assertEqual(self.apk.read_bytes(), b"existing cached APK")
                    self.assertEqual(set(self.root.iterdir()), {self.apk})
                else:
                    self.assertFalse(self.apk.exists())
                    self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_destination_is_not_followed(self):
        target = self.root / "protected.apk"
        self.apk.rename(target)
        self.apk.symlink_to(target)
        with mock.patch.object(magisk, "download_https") as network:
            with self.assertRaises(magisk.MagiskError):
                magisk.download_apk(self.apk, self.artifact)
        network.assert_not_called()
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), self.artifact.sha256)


class MagiskRamdiskTest(ArtifactFixture):
    def test_finished_v31_payload_without_stub_matches_selected_apk(self):
        directory = self.write_ramdisk()
        report = magisk.verify_ramdisk(directory, self.apk, self.artifact)
        self.assertEqual(report["abi"], "arm64-v8a")
        self.assertEqual(report["version_code"], 31100)

    def test_actual_avbroot_xz_preset_nine_is_accepted(self):
        # avbroot uses preset 9 (a 64 MiB dictionary). The decoder also needs
        # bookkeeping memory, so a 64 MiB total memory limit rejects real OTAs.
        directory = self.write_ramdisk(xz_preset=9)
        report = magisk.verify_ramdisk(directory, self.apk, self.artifact)
        self.assertEqual(report["version_code"], 31100)

    def test_previous_payload_with_stub_and_x86_payload_are_supported(self):
        for index, kwargs in enumerate((dict(version="30.7", code=30700, stub=True),
                                        dict(abi="x86_64", init_ld=False))):
            with self.subTest(kwargs=kwargs):
                artifact, members = self.write_apk(**kwargs)
                abi = kwargs.get("abi", "arm64-v8a")
                directory = self.write_ramdisk(members, name=f"ramdisk-{index}", abi=abi)
                report = magisk.verify_ramdisk(directory, self.apk, artifact)
                self.assertEqual(report["abi"], abi)

    def test_wrong_or_missing_init_and_embedded_payloads_are_rejected(self):
        for index, relative in enumerate(("init", "overlay.d/sbin/magisk.xz",
                                          "overlay.d/sbin/init-ld.xz")):
            for missing in (False, True):
                with self.subTest(relative=relative, missing=missing):
                    directory = self.write_ramdisk(name=f"ramdisk-{index}-{missing}")
                    payload = directory / relative
                    if missing:
                        payload.unlink()
                    else:
                        payload.write_bytes(b"wrong init" if relative == "init"
                                            else lzma.compress(b"wrong native binary"))
                    with self.assertRaises(REJECTED):
                        magisk.verify_ramdisk(directory, self.apk, self.artifact)

    def test_legacy_or_unexpected_stub_payload_is_rejected(self):
        for filename in ("stub.xz", "magisk32.xz", "magisk64.xz"):
            with self.subTest(filename=filename):
                directory = self.write_ramdisk(name=f"ramdisk-{filename}")
                (directory / "overlay.d/sbin" / filename).write_bytes(lzma.compress(b"stale payload"))
                with self.assertRaises(magisk.MagiskError):
                    magisk.verify_ramdisk(directory, self.apk, self.artifact)

    def test_stub_is_required_when_selected_apk_contains_it(self):
        artifact, members = self.write_apk(version="30.7", code=30700, stub=True)
        directory = self.write_ramdisk(members)
        (directory / "overlay.d/sbin/stub.xz").unlink()
        with self.assertRaises(REJECTED):
            magisk.verify_ramdisk(directory, self.apk, artifact)

    def test_payload_symlink_is_rejected_even_when_target_bytes_match(self):
        directory = self.write_ramdisk()
        payload = directory / "overlay.d/sbin/magisk.xz"
        other = self.root / "external-magisk.xz"
        payload.rename(other)
        payload.symlink_to(other)
        with self.assertRaises(magisk.MagiskError):
            magisk.verify_ramdisk(directory, self.apk, self.artifact)

    def test_truncated_oversized_and_concatenated_xz_are_rejected(self):
        expected = self.members["lib/arm64-v8a/libmagisk.so"]
        encoded = lzma.compress(expected)
        for index, data in enumerate((encoded[:20], lzma.compress(expected + b"x" * 100),
                                      encoded + lzma.compress(b"second stream"))):
            with self.subTest(index=index):
                directory = self.write_ramdisk(name=f"ramdisk-{index}")
                (directory / "overlay.d/sbin/magisk.xz").write_bytes(data)
                with self.assertRaises(REJECTED):
                    magisk.verify_ramdisk(directory, self.apk, self.artifact)

    def test_ramdisk_verification_rechecks_the_selected_apk(self):
        directory = self.write_ramdisk()
        self.write_apk(salt=b"other fork")
        with self.assertRaises(magisk.MagiskError):
            magisk.verify_ramdisk(directory, self.apk, self.artifact)


class MagiskCpioMetadataTest(ArtifactFixture):
    def setUp(self):
        super().setUp()
        self.directory = self.write_ramdisk()

    def verify(self, entries=None):
        return magisk.verify_ramdisk(
            self.directory, self.apk, self.artifact,
            cpio_info=self.write_cpio_info(entries),
        )

    def test_real_cpio_shape_accepts_omitted_or_explicit_root_ownership(self):
        self.assertEqual(self.verify()["abi"], "arm64-v8a")
        entries = self.ramdisk_entries()
        for entry in entries:
            entry.update(uid=0, gid=0)
        self.assertEqual(self.verify(entries)["version_code"], 31100)

    def test_tree_bytes_cannot_hide_nonexecutable_or_writable_init_metadata(self):
        for mode in ("644", "000", "772", "777"):
            with self.subTest(mode=mode):
                entries = self.ramdisk_entries()
                entries[0]["file_mode"] = mode
                with self.assertRaises(magisk.MagiskError):
                    self.verify(entries)

    def test_tree_bytes_cannot_hide_wrong_init_type_or_ownership(self):
        for field, value in (("file_type", "Symlink"), ("file_type", "Directory"),
                             ("uid", 2000), ("gid", 2000)):
            with self.subTest(field=field, value=value):
                entries = self.ramdisk_entries()
                entries[0][field] = value
                with self.assertRaises(magisk.MagiskError):
                    self.verify(entries)

    def test_parent_directories_need_safe_modes_types_and_root_ownership(self):
        for index in (1, 2):
            for field, value in (("file_type", "Symlink"), ("file_type", "Regular"),
                                 ("file_mode", "644"), ("file_mode", "300"),
                                 ("file_mode", "777"), ("uid", 2000), ("gid", 2000)):
                with self.subTest(path=index, field=field, value=value):
                    entries = self.ramdisk_entries()
                    entries[index][field] = value
                    with self.assertRaises(magisk.MagiskError):
                        self.verify(entries)

    def test_compressed_payloads_need_regular_type_safe_mode_and_root_ownership(self):
        for index in (3, 4):
            for field, value in (("file_type", "Symlink"), ("file_type", "Character"),
                                 ("file_mode", "200"), ("file_mode", "666"),
                                 ("uid", 2000), ("gid", 2000)):
                with self.subTest(path=index, field=field, value=value):
                    entries = self.ramdisk_entries()
                    entries[index][field] = value
                    with self.assertRaises(magisk.MagiskError):
                        self.verify(entries)

    def test_required_metadata_cannot_be_missing_even_with_correct_extracted_bytes(self):
        entries = self.ramdisk_entries()
        for index in range(len(entries)):
            with self.subTest(path=entries[index]["path"]):
                without_entry = [entry for i, entry in enumerate(entries) if i != index]
                with self.assertRaises(magisk.MagiskError):
                    self.verify(without_entry)

    def test_duplicate_metadata_paths_are_rejected(self):
        entries = self.ramdisk_entries()
        entries.append(dict(entries[0]))
        with self.assertRaises(magisk.MagiskError):
            self.verify(entries)

    def test_path_alias_cannot_hide_duplicate_or_unexpected_special_payload(self):
        # avbroot normalizes these spellings when extracting regular files,
        # but preserves raw paths for skipped special entries in metadata.
        for alias in ("./init", "/init", "overlay.d//sbin/magisk.xz",
                      "overlay.d/sbin/./init-ld.xz", "overlay.d//sbin/stub.xz"):
            with self.subTest(alias=alias):
                entries = self.ramdisk_entries()
                entries.append({"path": alias, "file_type": "Symlink", "file_mode": "777"})
                with self.assertRaises(magisk.MagiskError):
                    self.verify(entries)

    def test_unexpected_boot_payload_metadata_is_rejected_without_extracted_file(self):
        # cpio unpack omits special files. Checking only the extracted tree
        # would miss an unexpected stub or legacy core represented this way.
        for filename in ("stub.xz", "magisk32.xz", "magisk64.xz"):
            with self.subTest(filename=filename):
                entries = self.ramdisk_entries()
                entries.append({"path": f"overlay.d/sbin/{filename}",
                                "file_type": "Symlink", "file_mode": "777"})
                self.assertFalse((self.directory / "overlay.d/sbin" / filename).exists())
                with self.assertRaises(magisk.MagiskError):
                    self.verify(entries)

    def test_missing_metadata_file_and_empty_metadata_are_rejected(self):
        with self.assertRaises(REJECTED):
            magisk.verify_ramdisk(self.directory, self.apk, self.artifact,
                                  cpio_info=self.root / "missing.toml")
        with self.assertRaises(magisk.MagiskError):
            self.verify([])

    def test_production_cli_requires_cpio_metadata_argument(self):
        result = subprocess.run(
            [sys.executable, magisk.__file__, "--repository", self.artifact.repository,
             "--version", self.artifact.version, "--sha256", self.artifact.sha256,
             "verify-ramdisk", "--apk", str(self.apk), "--ramdisk-dir", str(self.directory)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--cpio-info", result.stderr)


if __name__ == "__main__":
    unittest.main()
