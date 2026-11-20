#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise APK signer policy at the real apksigner process boundary."""

from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_magisk_compatibility", REPO_ROOT / "src/ci/check_magisk_compatibility.py"
)
assert SPEC is not None and SPEC.loader is not None
compatibility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compatibility)

# Kept independent of the implementation constant: this is upstream's
# longstanding release certificate, also declared in Magisk scripts/release.sh.
SIGNER = "b4cb83b4dad99f997dbe872f013aa16c14eec41d167021f371f7e1330f273ee6"
OTHER = "a" * 64

LEGACY = f"""Verifies
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Number of signers: 1
Signer #1 certificate DN: CN=Android
Signer #1 certificate SHA-256 digest: {SIGNER}
Signer #1 certificate SHA-1 digest: 00112233445566778899aabbccddeeff00112233
"""

BUILD_TOOLS_37 = f"""Verifies
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Verified using v3.1 scheme (APK Signature Scheme v3.1): false
Verified using v4 scheme (APK Signature Scheme v4): false
Number of signers: 1
V2 Signer: certificate DN: CN=Android
V2 Signer: certificate SHA-256 digest: {SIGNER}
V3.0 Signer: certificate DN: CN=Android
V3.0 Signer: certificate SHA-256 digest: {SIGNER}
"""


class ApkSignerTest(unittest.TestCase):
    def verify(self, output: str, returncode: int = 0) -> dict:
        apk = Path("/tmp/magisk-signer-fixture.apk")
        completed = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=output,
            stderr="signature verification failed" if returncode else "",
        )
        with patch.object(compatibility.subprocess, "run", return_value=completed) as run:
            with redirect_stdout(io.StringIO()):
                result = compatibility.verify_apk_signer(apk)
            run.assert_called_once_with(
                ["apksigner", "verify", "--verbose", "--print-certs", str(apk)],
                cwd=apk.parent, check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        return result

    def test_build_tools_36_legacy_report(self):
        report = self.verify(LEGACY)
        self.assertEqual(report["certificate_sha256"], SIGNER)
        self.assertEqual(report["signer_count"], 1)
        self.assertEqual(report["output_format"], "legacy")

    def test_build_tools_37_repeated_certificate_across_schemes(self):
        report = self.verify(BUILD_TOOLS_37)
        self.assertEqual(report["certificate_sha256"], SIGNER)
        self.assertEqual(report["signer_count"], 1)
        self.assertEqual(report["schemes"], ["V2", "V3.0"])

    def test_scheme_sdk_bounds_and_colon_delimited_uppercase_digest(self):
        colon_digest = ":".join(SIGNER[i:i + 2].upper() for i in range(0, 64, 2))
        output = (
            "Verifies\nNumber of signers: 1\n"
            "V3.0 Signer: (minSdkVersion=28, maxSdkVersion=35) "
            f"certificate SHA-256 digest: {colon_digest}\n"
        )
        self.assertEqual(self.verify(output)["schemes"], ["V3.0"])

    def test_source_stamp_does_not_replace_the_apk_signer(self):
        output = (
            "Number of signers: 1\n"
            f"Source Stamp Signer: certificate SHA-256 digest: {SIGNER}\n"
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            self.verify(output)

    def test_unrelated_source_stamp_does_not_add_an_apk_signer(self):
        report = self.verify(
            BUILD_TOOLS_37
            + f"Source Stamp Signer: certificate SHA-256 digest: {OTHER}\n"
        )
        self.assertEqual(report["certificate_sha256"], SIGNER)

    def test_wrong_and_malformed_certificates_fail(self):
        for output in (
            LEGACY.replace(SIGNER, OTHER),
            BUILD_TOOLS_37.replace(SIGNER, OTHER, 1),
            BUILD_TOOLS_37.replace(SIGNER, SIGNER[:-1], 1),
            BUILD_TOOLS_37.replace(SIGNER, "invalid", 1),
        ):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    self.verify(output)

    def test_duplicate_and_mixed_records_fail(self):
        for output in (
            LEGACY + f"Signer #1 certificate SHA-256 digest: {SIGNER}\n",
            BUILD_TOOLS_37 + f"V2 Signer: certificate SHA-256 digest: {SIGNER}\n",
            BUILD_TOOLS_37 + f"Signer #1 certificate SHA-256 digest: {SIGNER}\n",
            LEGACY.replace("Signer #1", "Signer #2"),
            LEGACY + f"Signer #2 certificate SHA-256 digest: {SIGNER}\n",
        ):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "duplicate|mixed|multiple"):
                    self.verify(output)

    def test_missing_duplicate_malformed_and_multiple_signer_counts_fail(self):
        for count in ("", "Number of signers: 2\n", "Number of signers: unknown\n",
                      "Number of signers: 1\nNumber of signers: 1\n"):
            for template in (LEGACY, BUILD_TOOLS_37):
                with self.subTest(count=count, template=template):
                    with self.assertRaisesRegex(ValueError, "signer count"):
                        self.verify(template.replace("Number of signers: 1\n", count))

    def test_missing_signer_certificate_fails(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.verify("Verifies\nNumber of signers: 1\n")

    def test_unknown_scheme_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.verify(BUILD_TOOLS_37 + f"V5 Signer: certificate SHA-256 digest: {OTHER}\n")

    def test_failed_verifier_cannot_pass_with_valid_looking_stdout(self):
        with self.assertRaisesRegex(RuntimeError, r"command failed \(1\).*apksigner"):
            self.verify(BUILD_TOOLS_37, returncode=1)

    def test_missing_verifier_does_not_fall_back_to_digest_parsing(self):
        with patch.object(compatibility.subprocess, "run", side_effect=FileNotFoundError("apksigner")):
            with self.assertRaises(FileNotFoundError):
                compatibility.verify_apk_signer(Path("/tmp/magisk-signer-fixture.apk"))


if __name__ == "__main__":
    unittest.main()
