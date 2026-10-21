#!/usr/bin/env python3
"""Verify fail-closed exfat image metadata diagnostics."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exfatprogs_fsck import (  # noqa: E402
    ExfatFsckError,
    TARGET_PATH,
    _inject_system_binary,
    _metadata_diagnostics,
)


class ExfatMetadataDiagnosticsTest(unittest.TestCase):
    def entry(self, **changes):
        values = {
            "path": TARGET_PATH,
            "file_type": "RegularFile",
            "file_mode": 0o755,
            "uid": 0,
            "gid": 0,
            "xattrs": {"security.selinux": "u:object_r:fsck_exec:s0\0"},
        }
        values.update(changes)
        return SimpleNamespace(**values)

    def filesystem(self, entry):
        return SimpleNamespace(
            info=SimpleNamespace(entries=[entry]),
            tree=Path("/unused-until-metadata-passes"),
            open_calls=0,
            open=lambda *args, **kwargs: self.fail("metadata rejection must precede mutation"),
        )

    def test_valid_metadata_reports_presence_only(self):
        entry = self.entry()
        diagnostics = _metadata_diagnostics(entry)
        self.assertEqual(
            diagnostics,
            {
                "file_type": "RegularFile",
                "executable_bit": True,
                "uid_present": True,
                "gid_present": True,
                "selinux_label_present": True,
                "selinux_label_empty": False,
            },
        )
        self.assertNotIn("u:object_r:fsck_exec:s0", repr(diagnostics))

    def test_each_invalid_metadata_field_is_reported_before_mutation(self):
        cases = (
            ("file_type", self.entry(file_type="Symlink"), "file_type=Symlink"),
            ("executable", self.entry(file_mode=0o644), "executable_bit=False"),
            ("mode_missing", self.entry(file_mode=None), "executable_bit=False"),
            ("uid", self.entry(uid=None), "uid_present=False"),
            ("gid", self.entry(gid=None), "gid_present=False"),
            ("label_missing", self.entry(xattrs={}), "selinux_label_present=False"),
            (
                "label_empty",
                self.entry(xattrs={"security.selinux": "\0"}),
                "selinux_label_empty=True",
            ),
        )
        for name, entry, expected in cases:
            with self.subTest(name=name):
                filesystem = self.filesystem(entry)
                with self.assertRaises(ExfatFsckError) as raised:
                    _inject_system_binary(filesystem, b"replacement")
                message = str(raised.exception)
                self.assertIn(expected, message)
                self.assertNotIn("u:object_r:fsck_exec:s0", message)


if __name__ == "__main__":
    unittest.main()
