#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors
"""Offline, deterministic pin and workflow boundary regression tests.

This intentionally does NOT attempt to prove care_map.pb correctness. Worklane
3 owns binary conformance fixtures, and Worklanes 1-2 own the actual semantic
verifier/runtime requirements. This freezes the fail-closed ModOS integration
until those independent reviews are complete.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_SHA256 = "b2b4269738df1d3092de49bde6c7d0ffcb62ae19b342213a819187c30b7ab1f8"
HELPER_COMMIT = "70819893ff91488b4b40a8c6f45fde39854c6c68"
PATCH_SHA256 = "3405142ea7d056a37ac5f7ff9301fff5d4fef8519ab63f32333ff2574fd387c5"
AVBROOT_ARCHIVE_SHA256 = "b1740ebf92d503cf2e81ca443afa4b615fb97ec365e170b71791ed72d9e559f8"
AVBROOT_BINARY_SHA256 = "de0ed1b439175c8c358e2b2de289adc1a8947c655e3cce1598de374ab71d316b"
LINEAGE_PUBKEY_SHA256 = "0848ca38cb604887cea216a7f39d7dd62bad20b513cfdeff357ece4e731d6372"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_source_pin_is_closed(policy: str) -> None:
    assignments = re.findall(
        r"^readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256=(.*)$",
        policy,
        flags=re.MULTILINE,
    )
    if assignments != ["''"]:
        raise AssertionError(
            "October 9 production source pin must remain a literal empty "
            "readonly assignment; no environment/workflow/metadata injection"
        )


class ModOSCareMapIntegrationContractTests(unittest.TestCase):
    def test_source_pin_is_reviewer_owned_and_disabled(self) -> None:
        policy = read("src/source_care_map_policy.sh")
        assert_source_pin_is_closed(policy)
        self.assertIn(
            f"readonly _LINEAGEOS_OFFICIAL_OTA_PUBKEY_SPKI_SHA256='{LINEAGE_PUBKEY_SHA256}'",
            policy,
        )
        self.assertIn("source_care_map_certificate_has_public_key_digest", policy)
        self.assertIn("source_care_map_exception_allowed()", policy)
        self.assertIn("source_care_map_known_exception", policy)
        self.assertIn('"$1" "$2" "$3" "$4" "$5"', policy)
        # The real trusted SHA is documented evidence, never an active pin.
        self.assertNotIn(f"='{SOURCE_SHA256}'", policy)

    def test_pin_mutation_fixtures_are_rejected(self) -> None:
        # Literal, interpolated, command-derived, and duplicate pin mutations.
        original = read("src/source_care_map_policy.sh")
        for replacement in (
            f"readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256='{SOURCE_SHA256}'",
            'readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256="${PIXENEOS_PDX235_CARE_MAP_SHA256:-}"',
            'readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256="$(sha256sum source.zip)"',
            "readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256=${SOURCE_DIGEST}",
        ):
            with self.subTest(replacement=replacement):
                mutated = original.replace(
                    "readonly _SOURCE_CARE_MAP_PDX235_20261009_SHA256=''",
                    replacement,
                )
                self.assertNotEqual(mutated, original)
                with self.assertRaises(AssertionError):
                    assert_source_pin_is_closed(mutated)

    def test_helper_pin_and_executable_trust_are_exact(self) -> None:
        declarations = read("src/declarations.sh")
        self.assertRegex(
            declarations,
            rf'(?m)^VERSION\[AVBROOT_SETUP\]="{HELPER_COMMIT}"(?:\s|$)',
        )
        compat = json.loads(read("tools/compat/avbroot_setup_compat.json"))
        self.assertEqual(compat["repository"], "https://github.com/0cwa/my-avbroot-setup")
        self.assertEqual(compat["revision"], HELPER_COMMIT)
        self.assertEqual(compat["targets"]["patch.py"]["sha256"], PATCH_SHA256)
        self.assertEqual(compat["targets"]["patch.py"]["replacement"]["qualified_name"],
                         "external.generate_update_info")

        lock = json.loads(read("locks/executable-tools-v1.json"))
        entries = {entry["id"]: entry for entry in lock["tools"]}
        self.assertEqual(set(entries), {"avbroot", "afsr", "custota-tool"})
        avbroot = entries["avbroot"]
        self.assertEqual(avbroot["version"], "3.34.1")
        self.assertEqual(avbroot["sha256"], AVBROOT_ARCHIVE_SHA256)
        self.assertEqual(avbroot["url"], (
            "https://github.com/chenxiaolong/avbroot/releases/download/"
            "v3.34.1/avbroot-3.34.1-x86_64-unknown-linux-gnu.zip"
        ))
        self.assertEqual(
            next(member["sha256"] for member in avbroot["layout"]
                 if member["path"] == "avbroot"),
            AVBROOT_BINARY_SHA256,
        )
        self.assertEqual(avbroot["signature"]["type"], "ssh")
        self.assertEqual(avbroot["signature"]["namespace"], "file")
        self.assertEqual(avbroot["signature"]["identity"], "chenxiaolong")
        self.assertEqual(
            avbroot["signature"]["signer_fingerprint"],
            "SHA256:Ct0HoRyrFLrnF9W+A/BKEiJmwx7yWkgaW/JvghKrboA",
        )

    def test_strict_source_and_paired_output_path_remains_required(self) -> None:
        util = read("src/util_functions.sh")
        for required in (
            '"--verify-public-key-avb" "${grapheneos_pkmd}"',
            '"--verify-cert-ota" "${grapheneos_otacert}"',
            '"--sign-key-avb" "${KEYS[AVB]}"',
            '"--sign-key-ota" "${KEYS[OTA]}"',
            '"--sign-cert-ota" "${KEYS[CERT_OTA]}"',
            '"--pass-avb-env-var" "PASSPHRASE_AVB"',
            '"--pass-ota-env-var" "PASSPHRASE_OTA"',
            "verify_requested_root_outputs || return 1",
            "--secondary-output",
            "--secondary-patch-arg=--clear-vbmeta-flags",
            "--patch-arg=--clear-vbmeta-flags",
            "generate_custota_variant_sidecars",
        ):
            with self.subTest(required=required):
                self.assertIn(required, util)

        exception = 'args+=("--allow-source-care-map-mismatch")'
        self.assertEqual(util.count(exception), 1)
        position = util.index(exception)
        prior = util[max(0, position - 1000):position]
        self.assertIn("source_care_map_exception_allowed", prior)
        self.assertIn("_SOURCE_CARE_MAP_PDX235_20261009_SHA256", prior)
        self.assertIn("run_executable_tool avbroot --version", prior)
        self.assertIn("source_care_map_exception_allowed", read("src/source_care_map_policy.sh"))

    def test_no_workflow_pin_input_or_hidden_publication_path(self) -> None:
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in (ROOT / ".github/workflows").glob("*.yml")
        }
        self.assertEqual(set(workflows), {
            "build-rom.yml", "ci.yml", "multi-release.yml",
            "release-lineage.yml", "release.yml", "renovate.yml",
        })
        for name, text in workflows.items():
            with self.subTest(workflow=name):
                self.assertNotRegex(
                    text.lower(),
                    r"allow-source-care-map-mismatch|pdx235_care_map_sha256|"
                    r"source_care_map_pdx235_20261009_sha256",
                )
        build = workflows["build-rom.yml"]
        lineage = workflows["release-lineage.yml"]
        self.assertIn(
            "OUTPUT_SCOPE: ${{ inputs.publish && inputs.release-type != 'build-only' && 'published' || 'local-unpublished' }}",
            build,
        )
        self.assertIn("enforce_output_policy \"${OUTPUT_SCOPE}\"", build)
        self.assertIn("enforce_publication_evidence \"${OUTPUT_SCOPE}\"", build)
        self.assertLess(
            build.index("enforce_publication_evidence \"${OUTPUT_SCOPE}\""),
            build.index("- name: Publish GitHub release"),
        )
        for step in (
            "Publish GitHub release",
            "Publish paired GitHub release assets",
            "Remove superseded release assets",
            "Remove superseded paired release assets",
            "Publish OTA metadata",
            "Publish paired OTA metadata",
        ):
            with self.subTest(step=step):
                match = re.search(
                    rf"(?m)^      - name: {re.escape(step)}\n"
                    r"        if: ([^\n]+)",
                    build,
                )
                self.assertIsNotNone(match, f"missing conditional release step: {step}")
                self.assertIn("inputs.publish", match.group(1))
                self.assertIn("inputs.release-type != 'build-only'", match.group(1))
        self.assertIn("release-type: ${{ inputs.release-type }}", lineage)
        self.assertIn("publish: ${{ inputs.release-type != 'build-only' }}", lineage)
        self.assertIn("bash tests/pdx235_release_quarantine_test.sh", workflows["ci.yml"])
        self.assertIn("python3 tests/pdx235_integration_contract_test.py", workflows["ci.yml"])

    def test_quarantine_reached_from_all_publication_boundaries(self) -> None:
        common = read("src/ci/pdx235_release_quarantine.sh")
        self.assertIn("ModOS#114", common)
        self.assertIn("'local-unpublished'", common)
        profiles = read("src/rom_profiles.sh")
        self.assertIn('enforce_pdx235_care_map_quarantine \\', profiles)
        self.assertIn('enforce_pdx235_care_map_quarantine', read("src/ci/publish_ota.sh"))
        self.assertIn('enforce_pdx235_care_map_quarantine', read("src/ci/publish_ota_pair.sh"))
        # This static test complements the shell tests of the actual gate and
        # direct publisher entry points. It is not a care-map parser.


if __name__ == "__main__":
    unittest.main(verbosity=2)
