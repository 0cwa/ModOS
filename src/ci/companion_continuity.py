#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preserve the Companion signing identity of the published pdx235 feeds.

This is a publication guard, not Android migration or runtime qualification.
There is deliberately no option to waive a signer change.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "locks/companion-publication.json"
MAX_JSON = 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
PACKAGE = "com.android.vending"


class ContinuityError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContinuityError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_json(data: bytes):
    require(len(data) <= MAX_JSON, "JSON evidence exceeds the size limit")
    try:
        return json.loads(data, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ContinuityError(f"Invalid JSON evidence: {error}") from error


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), f"Missing or unsafe artifact: {path}")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sha(value) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def validate_identity(value):
    if value is None:
        return None
    require(isinstance(value, dict), "Companion identity must be an object or null")
    require(value.get("package_name") == PACKAGE, "Unexpected Companion package")
    require(type(value.get("version_code")) is int and value["version_code"] > 0,
            "Invalid Companion version code")
    for key in ("signer_sha256", "apk_sha256", "lock_sha256", "report_sha256"):
        require(sha(value.get(key)), f"Invalid Companion {key}")
    return value


def publication_fields(artifact: Path, report: Path | None) -> dict:
    """Read identity from the helper report written after successful verification."""
    identity = None
    ota_sha256 = file_digest(artifact)
    if report is not None:
        require(report.is_file() and not report.is_symlink(), "Verified microG patch report is missing")
        raw = report.read_bytes()
        evidence = parse_json(raw)
        require(isinstance(evidence, dict) and evidence.get("schema_version") == 1,
                "Unsupported microG patch report")
        require("microg" in evidence.get("selected_modules", []), "microG was not selected in the patch report")
        require(sha(evidence.get("lock_sha256")), "Patch report has no lock identity")
        outputs = evidence.get("modos_verified_outputs")
        require(isinstance(outputs, list) and 1 <= len(outputs) <= 2
                and all(isinstance(output, dict) for output in outputs),
                "Patch report lacks the helper's verified output binding")
        bound = [output for output in outputs if output.get("artifact_name") == artifact.name]
        require(len(bound) == 1 and bound[0].get("sha256") == ota_sha256,
                "MicroG report does not bind this verified OTA output")
        artifacts = [item for item in evidence.get("artifacts", [])
                     if isinstance(item, dict) and item.get("module") == "microg"
                     and item.get("apk_package_name") == PACKAGE]
        require(len(artifacts) == 1, "Patch report must contain exactly one Companion APK")
        apk = artifacts[0]
        require(apk.get("kind") == "apk" and apk.get("role") == "injection-input",
                "Companion report entry is not an injected APK")
        signers = [item for item in evidence.get("signers", [])
                   if isinstance(item, dict) and item.get("module") == "microg"
                   and item.get("artifact") == apk.get("artifact")
                   and item.get("type") == "apk-signer-sha256" and "member" not in item]
        require(len(signers) == 1, "Patch report must contain exactly one Companion signer")
        identity = validate_identity({
            "package_name": PACKAGE,
            "version_code": apk.get("apk_version_code"),
            "signer_sha256": signers[0].get("value"),
            "apk_sha256": apk.get("sha256"),
            "lock_sha256": evidence["lock_sha256"],
            "report_sha256": digest(raw),
        })
    return {"ota_sha256": ota_sha256, "microg_companion": identity,
            "microg_patch_report": report.name if report is not None else None}


class GitHub:
    def raw(self, endpoint: str, *, binary: bool = False) -> bytes:
        command = ["gh", "api", "--method", "GET", endpoint]
        if binary:
            command += ["-H", "Accept: application/octet-stream"]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    check=False, timeout=60)
        except subprocess.TimeoutExpired as error:
            raise ContinuityError(f"GitHub publication evidence timed out: {endpoint}") from error
        require(result.returncode == 0, f"Cannot read GitHub publication evidence: {endpoint}")
        require(len(result.stdout) <= MAX_JSON, "GitHub publication evidence exceeds the size limit")
        return result.stdout

    def json(self, endpoint: str):
        return parse_json(self.raw(endpoint))


def contents(api: GitHub, repository: str, snapshot: str, path: str) -> bytes:
    result = api.json(f"repos/{repository}/contents/{path}?ref={snapshot}")
    require(isinstance(result, dict) and result.get("type") == "file"
            and result.get("encoding") == "base64" and result.get("path") == path,
            f"Missing or unexpected canonical feed: {path}")
    try:
        data = base64.b64decode("".join(result["content"].split()), validate=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ContinuityError(f"Invalid canonical feed encoding: {path}") from error
    require(len(data) <= MAX_JSON, "Canonical feed exceeds the size limit")
    return data


def validate_selection(data, *, artifact: str, version: str):
    require(isinstance(data, dict), "Selection evidence must be an object")
    require(data.get("schema_version") == 2 and data.get("artifact_name") == artifact
            and data.get("grapheneos_version") == version
            and data.get("rom_family") == "lineageos" and data.get("device") == "pdx235"
            and data.get("output_scope") == "published"
            and sha(data.get("module_selection_fingerprint"))
            and isinstance(data.get("modos_commit"), str)
            and SHA1.fullmatch(data["modos_commit"]), "Selection identity does not match its OTA")


def one_asset(release: dict, name: str) -> dict:
    found = [item for item in release.get("assets", [])
             if isinstance(item, dict) and item.get("name") == name]
    require(len(found) == 1, f"Missing or ambiguous release asset: {name}")
    asset = found[0]
    require(asset.get("state") == "uploaded" and type(asset.get("id")) is int
            and asset["id"] > 0 and type(asset.get("size")) is int and asset["size"] > 0
            and isinstance(asset.get("digest"), str)
            and asset["digest"].startswith("sha256:") and sha(asset["digest"][7:]),
            f"Release asset lacks an expected digest: {name}")
    return asset


def previous_identity(api: GitHub, repository: str, snapshot: str, flavor: str, contract: dict):
    raw = contents(api, repository, snapshot, f"{flavor}/pdx235.json")
    alias = contents(api, repository, snapshot, f"{flavor}/XQ-DC72.json")
    require(raw == alias, f"Canonical pdx235 and XQ-DC72 {flavor} feeds disagree")
    feed = parse_json(raw)
    require(isinstance(feed, dict) and feed.get("version") == 2
            and isinstance(feed.get("full"), dict), "Unsupported canonical OTA feed")
    url = feed["full"].get("location_ota")
    prefix = f"https://github.com/{repository}/releases/download/"
    require(isinstance(url, str) and url.startswith(prefix), "Canonical OTA is outside the reviewed repository")
    match = re.fullmatch(r"(20[0-9]{6})/(pdx235-[A-Za-z0-9._-]+\.zip)", url[len(prefix):])
    require(match is not None and f"-{flavor}-" in match[2], "Canonical OTA release identity is invalid")
    version, artifact = match.groups()
    require(feed["full"].get("location_csig") == url + ".csig", "Canonical OTA signature does not match")
    release = api.json(f"repos/{repository}/releases/tags/{version}")
    require(isinstance(release, dict) and release.get("tag_name") == version
            and release.get("draft") is False and release.get("prerelease") is False,
            "Canonical OTA does not belong to a published release")
    ota = one_asset(release, artifact)
    one_asset(release, artifact + ".csig")
    selection_asset = one_asset(release, artifact + ".selection.json")
    require(selection_asset["size"] <= MAX_JSON, "Selection asset exceeds the size limit")
    raw_selection = api.raw(f"repos/{repository}/releases/assets/{selection_asset['id']}", binary=True)
    require(len(raw_selection) == selection_asset["size"]
            and digest(raw_selection) == selection_asset["digest"][7:], "Selection asset digest mismatch")
    selection = parse_json(raw_selection)
    validate_selection(selection, artifact=artifact, version=version)
    # A known legacy URL ALWAYS requires its exact pinned receipt. Adding modern
    # fields to a replaced selection asset must never relabel the shipped OTA.
    legacy = [entry for entry in contract.get("legacy_releases", [])
              if entry.get("location_ota") == url]
    if legacy:
        require(len(legacy) == 1, "Ambiguous legacy Companion identity receipt")
        entry = legacy[0]
        require(entry.get("ota_asset_id") == ota["id"]
                and entry.get("ota_sha256") == ota["digest"][7:]
                and entry.get("selection_asset_id") == selection_asset["id"]
                and entry.get("selection_sha256") == digest(raw_selection)
                and entry.get("modos_commit") == selection.get("modos_commit"),
                "Legacy OTA differs from the reviewed publication receipt")
        signer = entry.get("companion_signer_sha256")
        require(sha(signer), "Legacy receipt has an invalid Companion signer")
        return {"signer_sha256": signer}
    require("microg_companion" in selection,
            "Legacy OTA has no reviewed Companion identity; publication stopped")
    require(selection.get("ota_sha256") == ota["digest"][7:], "Selection does not bind the published OTA digest")
    return validate_identity(selection["microg_companion"])


def incoming_identity(path: Path):
    require(path.is_file() and not path.is_symlink(), "Incoming selection metadata is missing or unsafe")
    selection = parse_json(path.read_bytes())
    require(isinstance(selection, dict), "Incoming selection must be an object")
    artifact = selection.get("artifact_name")
    require(isinstance(artifact, str) and re.fullmatch(r"pdx235-[A-Za-z0-9._-]+\.zip", artifact)
            and path.name == artifact + ".selection.json", "Incoming OTA filename is invalid")
    version = selection.get("grapheneos_version")
    require(isinstance(version, str) and re.fullmatch(r"20[0-9]{6}", version), "Incoming release version is invalid")
    validate_selection(selection, artifact=artifact, version=version)
    require(sha(selection.get("ota_sha256"))
            and selection["ota_sha256"] == file_digest(path.parent / artifact),
            "Incoming OTA differs from its recorded digest")
    require("microg_companion" in selection, "Incoming selection has no Companion identity")
    identity = validate_identity(selection["microg_companion"])
    report = selection.get("microg_patch_report")
    if identity is None:
        require(report is None, "Absent Companion has unexpected patch evidence")
    else:
        require(isinstance(report, str)
                and re.fullmatch(r"pdx235-[A-Za-z0-9._-]+\.zip\.microg-patch-report\.json", report),
                "Incoming selection has no safe microG report path")
        fields = publication_fields(path.parent / artifact, path.parent / report)
        require(all(selection.get(key) == value for key, value in fields.items()),
                "Incoming Companion identity differs from verified helper output evidence")
    return selection, identity


def check(api: GitHub, repository: str, incoming: dict[str, tuple[Path, Path, Path]], contract: dict,
          *, release_tag: str, uploaded: bool = False) -> str:
    require(contract.get("schema_version") == 1 and contract.get("repository") == repository
            and repository == "0cwa/ModOS", "Publication continuity contract does not match this repository")
    require(bool(incoming) and set(incoming) <= {"rootless", "magisk"}, "No supported incoming OTA flavor")
    ref = api.json(f"repos/{repository}/git/ref/heads/gh-pages")
    require(isinstance(ref, dict) and isinstance(ref.get("object"), dict)
            and ref["object"].get("type") == "commit", "Cannot resolve the canonical feed snapshot")
    snapshot = ref["object"].get("sha")
    require(isinstance(snapshot, str) and SHA1.fullmatch(snapshot), "Invalid canonical feed commit")
    previous = [previous_identity(api, repository, snapshot, flavor, contract)
                for flavor in ("rootless", "magisk")]
    for flavor, (path, next_feed_path, upload_path) in incoming.items():
        selection, identity = incoming_identity(path)
        artifact = selection["artifact_name"]
        require(f"-{flavor}-" in artifact, "Incoming OTA does not match its root flavor")
        require(selection["grapheneos_version"] == release_tag, "Upload release tag differs from guarded selection")
        require(upload_path.name == artifact and not upload_path.is_symlink()
                and upload_path.resolve() == (path.parent / artifact).resolve(),
                "Upload OTA path differs from guarded selection")
        signature_sha256 = file_digest(upload_path.with_name(artifact + ".csig"))
        require(next_feed_path.is_file() and not next_feed_path.is_symlink(), "Incoming OTA feed is missing or unsafe")
        next_feed = parse_json(next_feed_path.read_bytes())
        url = f"https://github.com/{repository}/releases/download/{selection['grapheneos_version']}/{artifact}"
        require(isinstance(next_feed, dict) and next_feed.get("version") == 2
                and isinstance(next_feed.get("full"), dict)
                and next_feed["full"].get("location_ota") == url
                and next_feed["full"].get("location_csig") == url + ".csig",
                "Incoming feed does not point to the guarded OTA and signature")
        next_signer = identity["signer_sha256"] if identity is not None else None
        for old in previous:
            old_signer = old["signer_sha256"] if old is not None else None
            require(old_signer == next_signer,
                    "Companion signer change or removal refused: preserve the published identity; "
                    "an Android migration needs an explicit reviewed contract")
        if uploaded:
            release = api.json(f"repos/{repository}/releases/tags/{selection['grapheneos_version']}")
            require(isinstance(release, dict) and release.get("tag_name") == selection["grapheneos_version"]
                    and release.get("draft") is False and release.get("prerelease") is False,
                    "Incoming OTA release is not published")
            ota_asset = one_asset(release, artifact)
            selection_asset = one_asset(release, artifact + ".selection.json")
            signature_asset = one_asset(release, artifact + ".csig")
            require(ota_asset["digest"][7:] == selection["ota_sha256"]
                    and selection_asset["digest"][7:] == file_digest(path)
                    and signature_asset["digest"][7:] == signature_sha256,
                    "Uploaded OTA, selection or signature differs from guarded local output")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--rootless", type=Path, nargs=3, metavar=("SELECTION", "FEED", "OTA"))
    parser.add_argument("--magisk", type=Path, nargs=3, metavar=("SELECTION", "FEED", "OTA"))
    parser.add_argument("--uploaded", action="store_true", help="Also require exact uploaded asset digests")
    args = parser.parse_args()
    try:
        incoming = {flavor: path for flavor in ("rootless", "magisk") if (path := getattr(args, flavor)) is not None}
        snapshot = check(GitHub(), args.repository, incoming, parse_json(CONTRACT.read_bytes()),
                         release_tag=args.release_tag, uploaded=args.uploaded)
    except (ContinuityError, OSError, KeyError, TypeError) as error:
        print(f"Companion publication guard: {error}", file=sys.stderr)
        return 1
    print(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
