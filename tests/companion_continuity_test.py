#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Publication transitions, exact legacy evidence and direct-publisher boundaries."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ci"))
import companion_continuity as c

REPO = "0cwa/ModOS"
CUSTOM = "d30b31d91ed58e786f03f266d9b8fc7a6b22f388b0d632aab563c0f3cdc90bef"
OFFICIAL = "9bd06727e62796c0130eb6dab39b73157451582cbd138e86c468acc395d14165"
SNAPSHOT = "1" * 40


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def identity(signer):
    return {"package_name": c.PACKAGE, "version_code": 84022635,
            "signer_sha256": signer, "apk_sha256": "2" * 64,
            "lock_sha256": "3" * 64, "report_sha256": "4" * 64}


class API:
    def __init__(self, contract):
        self.data = {f"repos/{REPO}/git/ref/heads/gh-pages": encoded({
            "object": {"type": "commit", "sha": SNAPSHOT}})}
        self.release = {"tag_name": "20261106", "draft": False, "prerelease": False, "assets": []}
        for flavor, legacy in zip(("rootless", "magisk"), contract["legacy_releases"]):
            selection = (ROOT / "tests/fixtures/companion-continuity" / f"{flavor}-legacy-selection.json").read_bytes()
            assert c.digest(selection) == legacy["selection_sha256"]
            artifact = json.loads(selection)["artifact_name"]
            url = legacy["location_ota"]
            feed = encoded({"version": 2, "full": {"location_ota": url, "location_csig": url + ".csig"}})
            for device in ("pdx235", "XQ-DC72"):
                self.set_content(f"{flavor}/{device}.json", feed)
            self.release["assets"] += [
                self.asset(legacy["ota_asset_id"], artifact, legacy["ota_sha256"], 100),
                self.asset(legacy["ota_asset_id"] + 100, artifact + ".csig", "5" * 64, 100),
                self.asset(legacy["selection_asset_id"], artifact + ".selection.json",
                           c.digest(selection), len(selection)),
            ]
            self.data[f"repos/{REPO}/releases/assets/{legacy['selection_asset_id']}"] = selection
        self.sync_release()

    @staticmethod
    def asset(identifier, name, digest, size):
        return {"id": identifier, "name": name, "state": "uploaded", "size": size, "digest": "sha256:" + digest}

    def set_content(self, path, raw):
        self.data[f"repos/{REPO}/contents/{path}?ref={SNAPSHOT}"] = encoded({
            "type": "file", "encoding": "base64", "path": path,
            "content": base64.b64encode(raw).decode()})

    def sync_release(self):
        self.data[f"repos/{REPO}/releases/tags/20261106"] = encoded(self.release)

    def modern(self, signer):
        for asset in self.release["assets"]:
            if not asset["name"].endswith(".selection.json"):
                continue
            endpoint = f"repos/{REPO}/releases/assets/{asset['id']}"
            selection = json.loads(self.data[endpoint])
            ota = next(x for x in self.release["assets"] if x["name"] == selection["artifact_name"])
            selection.update(ota_sha256=ota["digest"][7:], microg_companion=identity(signer))
            self.data[endpoint] = raw = encoded(selection)
            asset.update(digest="sha256:" + c.digest(raw), size=len(raw))
        self.sync_release()

    def raw(self, endpoint, *, binary=False):
        if endpoint not in self.data:
            raise c.ContinuityError("GitHub evidence unavailable")
        return self.data[endpoint]

    def json(self, endpoint):
        return c.parse_json(self.raw(endpoint))


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = c.parse_json(c.CONTRACT.read_bytes())
        self.api = API(self.contract)

    @staticmethod
    def report(signer, outputs):
        return {"schema_version": 1, "selected_modules": ["microg"], "lock_sha256": "1" * 64,
                "modos_verified_outputs": outputs,
                "artifacts": [{"module": "microg", "artifact": "microg-companion", "kind": "apk",
                               "role": "injection-input", "apk_package_name": c.PACKAGE,
                               "apk_version_code": 84022635, "sha256": "2" * 64}],
                "signers": [{"module": "microg", "artifact": "microg-companion",
                             "type": "apk-signer-sha256", "value": signer}]}

    def incoming(self, signer=CUSTOM, flavors=("rootless", "magisk")):
        paths = {}
        outputs = []
        for flavor in flavors:
            name = f"pdx235-20261110-{flavor}-fixture.zip"
            ota = self.root / name
            ota.write_bytes(b"verified OTA fixture " + flavor.encode())
            selection = {"schema_version": 2, "artifact_name": name, "device": "pdx235",
                         "rom_family": "lineageos", "grapheneos_version": "20261110",
                         "output_scope": "published", "modos_commit": "6" * 40,
                         "module_selection_fingerprint": "7" * 64}
            paths[flavor] = path = self.root / (name + ".selection.json")
            path.write_bytes(encoded(selection))
            (self.root / (name + ".csig")).write_text("signature fixture")
            outputs.append({"artifact_name": name, "sha256": c.file_digest(ota)})
            url = f"https://github.com/{REPO}/releases/download/20261110/{name}"
            (self.root / f"pdx235-{flavor}.json").write_bytes(encoded({
                "version": 2, "full": {"location_ota": url, "location_csig": url + ".csig"}}))
        report_path = None
        if signer is not None:
            report_path = self.root / (outputs[0]["artifact_name"] + ".microg-patch-report.json")
            report_path.write_bytes(encoded(self.report(signer, outputs)))
        for path in paths.values():
            selection = json.loads(path.read_text())
            selection.update(c.publication_fields(self.root / selection["artifact_name"], report_path))
            path.write_bytes(encoded(selection))
        return paths

    def pairs(self, paths):
        return {flavor: (path, self.root / f"pdx235-{flavor}.json",
                        self.root / json.loads(path.read_bytes())["artifact_name"])
                for flavor, path in paths.items()}

    def check(self, paths, *, uploaded=False):
        return c.check(self.api, REPO, self.pairs(paths), self.contract,
                       release_tag="20261110", uploaded=uploaded)

    def assert_blocked(self, paths, message=None):
        with self.assertRaisesRegex(c.ContinuityError, message or ".+"):
            self.check(paths)

    def test_current_shipped_custom_pair_rejects_official_rollback(self):
        self.assert_blocked(self.incoming(OFFICIAL), "signer change")

    def test_current_shipped_custom_pair_accepts_same_signer(self):
        self.assertEqual(self.check(self.incoming()), SNAPSHOT)

    def test_new_metadata_accepts_official_and_custom_continuity(self):
        for signer in (OFFICIAL, CUSTOM):
            with self.subTest(signer=signer):
                self.api = API(self.contract)
                self.api.modern(signer)
                self.assertEqual(self.check(self.incoming(signer)), SNAPSHOT)

    def test_single_update_checks_both_current_feeds(self):
        self.assertEqual(self.check(self.incoming(flavors=("rootless",))), SNAPSHOT)
        del self.api.data[f"repos/{REPO}/contents/magisk/XQ-DC72.json?ref={SNAPSHOT}"]
        self.assert_blocked(self.incoming(flavors=("rootless",)))

    def test_removing_companion_is_not_an_implicit_migration(self):
        self.assert_blocked(self.incoming(None), "signer change or removal")

    def test_unknown_legacy_and_missing_evidence_fail_closed(self):
        self.contract["legacy_releases"] = []
        self.assert_blocked(self.incoming(), "no reviewed Companion identity")
        del self.api.data[f"repos/{REPO}/git/ref/heads/gh-pages"]
        self.assert_blocked(self.incoming(), "unavailable")

    def test_legacy_receipt_requires_exact_asset_id_and_digest(self):
        for key, value in (("ota_asset_id", 1), ("ota_sha256", "0" * 64),
                           ("selection_asset_id", 2), ("selection_sha256", "0" * 64)):
            with self.subTest(key=key):
                contract = copy.deepcopy(self.contract)
                contract["legacy_releases"][0][key] = value
                with self.assertRaisesRegex(c.ContinuityError, "differs from the reviewed"):
                    c.check(self.api, REPO, self.pairs(self.incoming()), contract, release_tag="20261110")

    def test_remote_tampering_alias_drift_and_ambiguous_assets_fail(self):
        paths = self.incoming()
        self.api.set_content("rootless/XQ-DC72.json", b"{}")
        self.assert_blocked(paths, "feeds disagree")
        self.api = API(self.contract)
        endpoint = f"repos/{REPO}/releases/assets/{self.contract['legacy_releases'][0]['selection_asset_id']}"
        self.api.data[endpoint] += b" "
        self.assert_blocked(paths, "digest mismatch")
        self.api = API(self.contract)
        self.api.release["assets"].append(copy.deepcopy(self.api.release["assets"][0]))
        self.api.sync_release()
        self.assert_blocked(paths, "ambiguous release asset")

    def test_incoming_artifact_tamper_and_unknown_identity_fail(self):
        paths = self.incoming()
        ota = self.pairs(paths)["rootless"][2]
        ota.write_bytes(b"changed")
        self.assert_blocked(paths, "recorded digest")
        paths = self.incoming()
        data = json.loads(paths["rootless"].read_text())
        del data["microg_companion"]
        paths["rootless"].write_bytes(encoded(data))
        self.assert_blocked(paths, "no Companion identity")

    def test_modern_selection_must_bind_published_ota_digest(self):
        self.api.modern(CUSTOM)
        self.api.release["assets"][0]["digest"] = "sha256:" + "9" * 64
        self.api.sync_release()
        self.assert_blocked(self.incoming(), "published OTA digest")

    def test_report_derives_identity_and_rejects_ambiguous_signers(self):
        ota = self.root / "output.zip"
        ota.write_bytes(b"verified OTA")
        report = self.report(CUSTOM, [{"artifact_name": ota.name, "sha256": c.file_digest(ota)}])
        path = self.root / "report.json"
        path.write_bytes(encoded(report))
        fields = c.publication_fields(ota, path)
        self.assertEqual(fields["microg_companion"]["signer_sha256"], CUSTOM)
        self.assertEqual(fields["microg_companion"]["report_sha256"], c.file_digest(path))
        report["signers"] *= 2
        path.write_bytes(encoded(report))
        with self.assertRaisesRegex(c.ContinuityError, "exactly one Companion signer"):
            c.publication_fields(ota, path)
        self.assertIsNone(c.publication_fields(ota, None)["microg_companion"])

    def test_stale_custom_report_cannot_relabel_an_official_output(self):
        paths = self.incoming()
        path = paths["rootless"]
        selection = json.loads(path.read_text())
        ota = self.root / selection["artifact_name"]
        ota.write_bytes(b"different official OTA")
        selection["ota_sha256"] = c.file_digest(ota)
        path.write_bytes(encoded(selection))
        self.assert_blocked(paths, "does not bind this verified OTA")

    def test_pair_report_must_bind_the_secondary_output(self):
        paths = self.incoming()
        selection = json.loads(paths["rootless"].read_text())
        report_path = self.root / selection["microg_patch_report"]
        report = json.loads(report_path.read_text())
        report["modos_verified_outputs"] = report["modos_verified_outputs"][:1]
        report_path.write_bytes(encoded(report))
        # Refresh recorded report digests: the secondary must still be rejected.
        for path in paths.values():
            data = json.loads(path.read_text())
            data["microg_companion"]["report_sha256"] = c.file_digest(report_path)
            path.write_bytes(encoded(data))
        self.assert_blocked(paths, "does not bind this verified OTA")

    def add_uploaded(self, paths):
        release = {"tag_name": "20261110", "draft": False, "prerelease": False, "assets": []}
        for path in paths.values():
            ota = self.root / json.loads(path.read_bytes())["artifact_name"]
            for asset in (ota, path, ota.with_name(ota.name + ".csig")):
                release["assets"].append(API.asset(100 + len(release["assets"]), asset.name,
                                                   c.file_digest(asset), asset.stat().st_size))
        self.api.data[f"repos/{REPO}/releases/tags/20261110"] = encoded(release)

    def test_destination_feed_and_uploaded_assets_must_match_guarded_output(self):
        paths = self.incoming()
        feed = self.root / "pdx235-rootless.json"
        original = feed.read_bytes()
        data = json.loads(original)
        data["full"]["location_ota"] = "https://example.invalid/another.zip"
        feed.write_bytes(encoded(data))
        self.assert_blocked(paths, "feed does not point to the guarded OTA")
        feed.write_bytes(original)
        self.add_uploaded(paths)
        self.assertEqual(self.check(paths, uploaded=True), SNAPSHOT)
        endpoint = f"repos/{REPO}/releases/tags/20261110"
        original_release = self.api.data[endpoint]
        for index in range(3):  # OTA, selection, csig.
            with self.subTest(index=index):
                release = json.loads(original_release)
                release["assets"][index]["digest"] = "sha256:" + "0" * 64
                self.api.data[endpoint] = encoded(release)
                with self.assertRaisesRegex(c.ContinuityError, "Uploaded OTA, selection or signature differs"):
                    self.check(paths, uploaded=True)

    def fake_cli(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        mapping = self.root / "api.json"
        mapping.write_text(json.dumps({k: base64.b64encode(v).decode() for k, v in self.api.data.items()}))
        cli = bin_dir / "gh"
        cli.write_text("""#!/usr/bin/env python3
import base64,json,os,pathlib,sys
args=sys.argv[1:]
with open(os.environ['OPERATION_LOG'],'a') as f: f.write(' '.join(args)+'\\n')
if args[:3] == ['api','--method','GET']:
    mapping=json.loads(pathlib.Path(os.environ['API_FIXTURE']).read_text())
    if args[3] not in mapping: sys.exit(1)
    sys.stdout.buffer.write(base64.b64decode(mapping[args[3]]))
elif args[:2] == ['release','view']:
    print(os.environ['INCOMING_ASSETS'])
else:
    sys.exit(99)
""")
        cli.chmod(0o700)
        git = bin_dir / "git"
        git.write_text("#!/bin/sh\nprintf 'GIT-MUTATION %s\\n' \"$*\" >>\"$OPERATION_LOG\"\nexit 99\n")
        git.chmod(0o700)
        return {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "API_FIXTURE": str(mapping), "OPERATION_LOG": str(self.root / "operations.log")}

    def publisher_env(self, paths):
        env = self.fake_cli()
        env.update(GITHUB_REPOSITORY=REPO, ROM_FAMILY="lineageos", DEVICE_NAME="pdx235",
                   ROM_PROFILE_PROVIDER="lineageos", GRAPHENEOS_UPDATE_CHANNEL="nightly",
                   GRAPHENEOS_VERSION="20261110", RELEASE_TAG="20261110",
                   OUTPUT_SCOPE="published", RELEASE_TYPE="default", ROOT_MODE_RESOLVED="both",
                   SELECTION_METADATA_ROOTLESS=paths["rootless"].name,
                   SELECTION_METADATA_MAGISK=paths["magisk"].name,
                   ROOTLESS_SELECTION_METADATA_PATH=paths["rootless"].name,
                   MAGISK_SELECTION_METADATA_PATH=paths["magisk"].name,
                   ROOTLESS_MODULE_SELECTION_FINGERPRINT="7" * 64,
                   MAGISK_MODULE_SELECTION_FINGERPRINT="7" * 64,
                   ROOTLESS_OTA_METADATA_PATH="pdx235-rootless.json",
                   MAGISK_OTA_METADATA_PATH="pdx235-magisk.json",
                   OTA_METADATA_ROOTLESS="pdx235-rootless.json", OTA_METADATA_MAGISK="pdx235-magisk.json")
        assets = []
        for flavor in paths:
            name = json.loads(paths[flavor].read_text())["artifact_name"]
            env[flavor.upper() + "_OTA_ARTIFACT_PATH"] = name
            env["OUTPUTS_PATCHED_OTA_" + flavor.upper()] = name
            assets += [name, name + ".csig", paths[flavor].name]
        env["INCOMING_ASSETS"] = "\n".join(assets)
        return env

    def run_publisher(self, script, env):
        return subprocess.run(["bash", str(ROOT / "src/ci" / script)], cwd=self.root,
                              env=env, capture_output=True, text=True)

    def assert_no_mutations(self):
        path = self.root / "operations.log"
        operations = path.read_text() if path.exists() else ""
        for marker in ("GIT-MUTATION", "release upload", "release create", "release edit"):
            self.assertNotIn(marker, operations)

    def test_direct_publishers_use_exact_paths_and_reject_before_mutation(self):
        paths = self.incoming(OFFICIAL)
        env = self.publisher_env(paths)
        result = self.run_publisher("publish_exfatprogs_release_assets.sh", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signer change or removal refused", result.stderr)
        # Invalid aliases must not override validated direct-publisher paths.
        env.update(SELECTION_METADATA_ROOTLESS="conflicting-stale.json",
                   SELECTION_METADATA_MAGISK="conflicting-stale.json",
                   SELECTION_METADATA="conflicting-stale.json")
        result = self.run_publisher("publish_ota_pair.sh", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signer change or removal refused", result.stderr)
        env.update(ROM_FLAVOR="rootless", MODULE_SELECTION_FINGERPRINT="7" * 64,
                   OTA_ARTIFACT_PATH=env["ROOTLESS_OTA_ARTIFACT_PATH"],
                   SELECTION_METADATA_PATH=paths["rootless"].name, OTA_METADATA_PATH="pdx235.json")
        (self.root / "pdx235.json").write_bytes((self.root / "pdx235-rootless.json").read_bytes())
        result = self.run_publisher("publish_ota.sh", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signer change or removal refused", result.stderr)
        self.assert_no_mutations()

    def test_source_first_actual_upload_path_and_tag_are_bound(self):
        paths = self.incoming()
        baseline = self.publisher_env(paths)
        wrong = self.root / "pdx235-20261110-rootless-other.zip"
        wrong.write_bytes(b"another output")
        for field, value, message in (
            ("OUTPUTS_PATCHED_OTA_ROOTLESS", wrong.name, "Upload OTA path differs"),
            ("RELEASE_TAG", "20261111", "Upload release tag differs"),
        ):
            with self.subTest(field=field):
                env = {**baseline, field: value}
                result = self.run_publisher("publish_exfatprogs_release_assets.sh", env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        self.assert_no_mutations()

    def test_direct_alias_and_wrong_family_cannot_replace_protected_feed(self):
        for device, family, message in (("XQ-DC72", "lineageos", "direct alias publication"),
                                        ("pdx235", "grapheneos", "protected pdx235 feed")):
            with self.subTest(device=device, family=family):
                paths = self.incoming()
                env = self.publisher_env(paths)
                path = paths["rootless"]
                selection = json.loads(path.read_text())
                selection.update(device=device, rom_family=family)
                path.write_bytes(encoded(selection))
                metadata = self.root / f"{device}.json"
                metadata.write_bytes((self.root / "pdx235-rootless.json").read_bytes())
                env.update(ROM_FAMILY=family, DEVICE_NAME=device, ROM_PROFILE_PROVIDER=family,
                           ROM_FLAVOR="rootless", MODULE_SELECTION_FINGERPRINT="7" * 64,
                           OTA_ARTIFACT_PATH=env["ROOTLESS_OTA_ARTIFACT_PATH"],
                           SELECTION_METADATA_PATH=path.name, OTA_METADATA_PATH=metadata.name)
                result = self.run_publisher("publish_ota.sh", env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        self.assert_no_mutations()

    def test_snapshot_change_stops_before_branch_or_pointer_changes(self):
        paths = self.incoming()
        self.add_uploaded(paths)
        env = self.fake_cli()
        git = self.root / "bin/git"
        git.write_text("""#!/bin/sh
printf 'git %s\\n' "$*" >>"$OPERATION_LOG"
case "$1" in
  fetch) exit 0 ;;
  rev-parse) printf '%040d\\n' 0; exit 0 ;;
  *) exit 99 ;;
esac
""")
        env.update(GITHUB_REPOSITORY=REPO, ROM_FAMILY="lineageos", DEVICE_NAME="pdx235")
        command = ["bash", "-c", 'source "$1"; shift; prepare_companion_publication_snapshot "$@"',
                   "fixture", str(ROOT / "src/ci/companion_continuity.sh"), "--release-tag", "20261110"]
        for flavor, values in self.pairs(paths).items():
            command += ["--" + flavor, *(str(value) for value in values)]
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("feeds changed", result.stderr)
        operations = (self.root / "operations.log").read_text()
        self.assertNotIn("checkout", operations)
        self.assertNotIn("push", operations)

    def test_workflow_guards_all_upload_routes(self):
        workflow = (ROOT / ".github/workflows/build-rom.yml").read_text()
        guard = workflow.index("name: Preserve published Companion identity before release upload")
        for route in ("name: Publish GitHub release", "name: Publish paired GitHub release assets",
                      "name: Publish source-first exfatprogs release assets"):
            self.assertLess(guard, workflow.index(route))
        self.assertIn("data.update(publication_fields", workflow)
        self.assertIn("group: gh-pages", workflow)


if __name__ == "__main__":
    unittest.main()
