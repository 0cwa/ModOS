#!/usr/bin/env python3
"""Static release-routing gates for the source-built pdx235 vold module."""

import json
from pathlib import Path
import re
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
LINEAGE = ROOT / ".github/workflows/release-lineage.yml"
BUILD = ROOT / ".github/workflows/build-rom.yml"
GRAPHENE = ROOT / ".github/workflows/release.yml"
LOADER = ROOT / "src/ci/load_schedule_definition.sh"
PROFILE = ROOT / "src/rom_profiles.sh"
PREP = ROOT / "src/util_functions.sh"
LOCK = ROOT / "locks/lineage-vold-exfat-timeout.json"


class VoldWorkflowContractTest(unittest.TestCase):
    def test_both_schedules_are_explicit_and_grapheneos_is_off(self):
        for name, expected_device, expected_family in (
            ("lineageos-pdx235.toml", "pdx235", "lineageos"),
            ("grapheneos-shiba.toml", "shiba", "grapheneos"),
        ):
            schedule = tomllib.loads((ROOT / ".github/schedules" / name).read_text())
            self.assertEqual(schedule["device"]["DEVICE_NAME"], expected_device)
            self.assertEqual(schedule["device"]["ROM_FAMILY"], expected_family)
            self.assertIs(
                schedule["build"]["ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]"], False
            )

        self.assertIn("rom_family", LOADER.read_text())
        self.assertIn("lineage_vold_exfat_timeout", LOADER.read_text())
        self.assertIn("Source-built vold is supported only for LineageOS pdx235", LOADER.read_text())

    def test_manual_and_scheduled_builds_fetch_same_approved_artifact(self):
        lineage, build, graphene = (
            LINEAGE.read_text(), BUILD.read_text(), GRAPHENE.read_text()
        )
        self.assertIn("lineage-vold-exfat-timeout:", lineage)
        self.assertIn("lineage-vold-exfat-timeout:", build)
        self.assertIn("inputs.lineage-vold-exfat-timeout", lineage)
        self.assertIn("needs.schedule_config.outputs.lineage_vold_exfat_timeout == 'true'", lineage)
        self.assertIn("inputs.lineage-vold-exfat-timeout", build)
        self.assertIn("ADDITIONALS_LINEAGE_VOLD_EXFAT_TIMEOUT:", build)
        self.assertNotIn("lineage-vold-exfat-timeout", graphene)
        self.assertNotIn("LINEAGE_VOLD_EXFAT_TIMEOUT", graphene)

        staged = "src/ci/fetch_lineage_vold_artifact.py"
        self.assertIn(staged, lineage)
        self.assertIn(staged, build)
        self.assertLess(
            lineage.index("Download approved vold artifact for scheduled preflight"),
            lineage.index("Check for an already-published matching OTA pair"),
        )
        self.assertLess(
            build.index("Download approved source-built vold artifact"),
            build.index("Enforce profile output policy"),
        )
        self.assertIn("PIXENEOS_VOLD_BASE_BUILD_ID=lineage-24.0-20261016-nightly-pdx235-signed", lineage)
        self.assertIn("PIXENEOS_VOLD_BASE_BUILD_ID=lineage-24.0-20261016-nightly-pdx235-signed", build)

    def test_publication_cannot_inherit_self_reported_provenance_only(self):
        lock = json.loads(LOCK.read_text())
        profile = PROFILE.read_text()
        prep = PREP.read_text()
        self.assertEqual(lock["device"], "pdx235")
        self.assertEqual(lock["rom_family"], "lineageos")
        self.assertEqual(lock["base_build_id"], "lineage-24.0-20261016-nightly-pdx235-signed")
        for script in (profile, prep):
            self.assertIn("src/ci/check_lineage_vold_artifact.py", script)
            self.assertIn("--scope", script)
            self.assertIn("--source-target", script)

        approved = lock.get("approved_binary_sha256", "")
        tag = lock.get("artifact_distribution", {}).get("release_tag", "")
        if approved or tag:
            self.assertRegex(approved, r"^[0-9a-f]{64}$")
            self.assertRegex(tag, r"^vold-pdx235-20261016-[A-Za-z0-9_.-]+$")
            self.assertRegex(
                lock["artifact_distribution"]["provenance_sha256"],
                r"^[0-9a-f]{64}$",
            )
        else:
            self.assertEqual(lock["artifact_distribution"]["provenance_sha256"], "")


if __name__ == "__main__":
    unittest.main()
