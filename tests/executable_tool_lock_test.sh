#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

LOCK="locks/executable-tools-v1.json"
TRUST="trust/chenxiaolong.allowed_signers"
DOC="docs/executable-tool-trust.md"
VALIDATOR="src/validate_executable_tool_lock.py"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT

fail() {
  echo "$*" >&2
  exit 1
}

for required_path in "${LOCK}" "${TRUST}" "${DOC}" "${VALIDATOR}"; do
  [[ -f "${required_path}" && ! -L "${required_path}" ]] ||
    fail "required executable-tool trust input is missing or not a regular file: ${required_path}"
done

run_validator() {
  local lock_path="${1}"

  env \
    HTTP_PROXY=http://127.0.0.1:9 \
    HTTPS_PROXY=http://127.0.0.1:9 \
    ALL_PROXY=http://127.0.0.1:9 \
    NO_PROXY= \
    python3 "${VALIDATOR}" \
      --lock "${lock_path}" \
      --trust "${TRUST}"
}

assert_rejected() {
  local case_name="${1}"
  local mutated="${TEST_ROOT}/${case_name}.json"

  write_mutation "${case_name}" "${mutated}"
  if run_validator "${mutated}" >"${TEST_ROOT}/${case_name}.out" 2>&1; then
    fail "invalid executable-tool lock unexpectedly validated: ${case_name}"
  fi
}

write_mutation() {
  local case_name="${1}"
  local output_path="${2}"

  python3 - "${LOCK}" "${output_path}" "${case_name}" <<'PY'
import copy
import json
import pathlib
import sys

source, output, case = sys.argv[1:]
data = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
tools = data["tools"]
tool = tools[0]
layout = tool["layout"]

if case == "unknown_tool":
    tool["id"] = "unknown-tool"
elif case == "unknown_version":
    tool["version"] = "999.0"
elif case == "unknown_arch":
    tool["arch"] = "aarch64-unknown-linux-gnu"
elif case == "unknown_top_level_field":
    data["future_policy"] = True
elif case == "unknown_tool_field":
    tool["executable_hint"] = "run-me"
elif case == "duplicate_identity":
    tools.append(copy.deepcopy(tool))
elif case == "duplicate_member":
    layout.append(copy.deepcopy(layout[0]))
elif case == "wrong_artifact_name":
    tool["artifact_name"] = "another-tool.zip"
elif case == "noncanonical_artifact_url":
    tool["url"] += "?download=1"
elif case == "floating_artifact_url":
    tool["url"] = (
        "https://github.com/chenxiaolong/afsr/"
        "releases/latest/download/afsr-latest.zip"
    )
elif case == "mutable_branch_url":
    tool["url"] = (
        "https://raw.githubusercontent.com/chenxiaolong/afsr/"
        "main/afsr.zip"
    )
elif case == "noncanonical_archive_hash":
    tool["sha256"] = tool["sha256"].upper()
elif case == "short_archive_hash":
    tool["sha256"] = "0" * 63
elif case == "signature_url_mismatch":
    tool["signature"]["url"] = tool["url"] + ".wrong.sig"
elif case == "floating_signature_url":
    tool["signature"]["url"] = (
        "https://github.com/chenxiaolong/afsr/"
        "releases/latest/download/afsr-latest.zip.sig"
    )
elif case == "unknown_signature_type":
    tool["signature"]["type"] = "openpgp"
elif case == "wrong_signature_identity":
    tool["signature"]["identity"] = "unreviewed"
elif case == "wrong_signature_namespace":
    tool["signature"]["namespace"] = "git"
elif case == "signer_fingerprint_mismatch":
    tool["signature"]["signer_fingerprint"] = (
        "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
elif case == "symlink_layout":
    layout[0]["type"] = "symlink"
elif case == "special_layout":
    layout[0]["type"] = "device"
elif case == "extra_layout_member":
    extra = copy.deepcopy(layout[0])
    extra["path"] = "unexpected-helper"
    layout.append(extra)
elif case == "noncanonical_layout_mode":
    layout[0]["mode"] = "755"
elif case == "wrong_layout_mode":
    layout[0]["mode"] = "0777"
elif case == "noncanonical_executable_hash":
    layout[0]["sha256"] = layout[0]["sha256"].upper()
elif case == "short_executable_hash":
    layout[0]["sha256"] = "f" * 63
elif case.startswith("missing_"):
    targets = {
        "missing_artifact_size": (tool, "size"),
        "missing_archive_hash": (tool, "sha256"),
        "missing_signature_url": (tool["signature"], "url"),
        "missing_signature_type": (tool["signature"], "type"),
        "missing_signature_identity": (tool["signature"], "identity"),
        "missing_signature_namespace": (tool["signature"], "namespace"),
        "missing_signer_fingerprint": (
            tool["signature"],
            "signer_fingerprint",
        ),
        "missing_layout": (tool, "layout"),
        "missing_executable_size": (layout[0], "size"),
        "missing_executable_hash": (layout[0], "sha256"),
        "missing_executable_mode": (layout[0], "mode"),
    }
    target, key = targets[case]
    del target[key]
elif case == "noncanonical_json_form":
    pathlib.Path(output).write_text(
        json.dumps(data, indent=4, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
else:
    raise SystemExit(f"unknown mutation case: {case}")

pathlib.Path(output).write_text(
    json.dumps(data, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

test_canonical_committed_lock() {
  run_validator "${LOCK}" >/dev/null

  python3 - "${LOCK}" "${TRUST}" <<'PY'
import json
import pathlib
import re
import sys

lock_path, trust_path = map(pathlib.Path, sys.argv[1:])
data = json.loads(lock_path.read_text(encoding="utf-8"))

if data.get("schema_version") != 1:
    raise SystemExit("schema_version must be 1")
if set(data) != {"schema_version", "tools"}:
    raise SystemExit("top-level executable lock fields are not exact")

expected = {
    "afsr": {
        "version": "2.0.0",
        "artifact": "afsr-2.0.0-x86_64-unknown-linux-gnu.zip",
        "layout": {"afsr": "0755"},
        "url": (
            "https://github.com/chenxiaolong/afsr/releases/download/"
            "v2.0.0/afsr-2.0.0-x86_64-unknown-linux-gnu.zip"
        ),
    },
    "avbroot": {
        "version": "3.34.1",
        "artifact": "avbroot-3.34.1-x86_64-unknown-linux-gnu.zip",
        "archive_size": 4254252,
        "archive_sha256": "b1740ebf92d503cf2e81ca443afa4b615fb97ec365e170b71791ed72d9e559f8",
        "layout": {
            "LICENSE": "0644",
            "README.md": "0644",
            "avbroot": "0755",
        },
        "members": {
            "LICENSE": {
                "size": 35149,
                "sha256": "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
            },
            "README.md": {
                "size": 33128,
                "sha256": "36f29f6c9142be36a1895ff1f1b8675b9f29f1d2949d9680da8100ed47555d29",
            },
            "avbroot": {
                "size": 10931888,
                "sha256": "de0ed1b439175c8c358e2b2de289adc1a8947c655e3cce1598de374ab71d316b",
            },
        },
        "url": (
            "https://github.com/chenxiaolong/avbroot/releases/download/"
            "v3.34.1/avbroot-3.34.1-x86_64-unknown-linux-gnu.zip"
        ),
    },
    "custota-tool": {
        "version": "6.6",
        "artifact": "custota-tool-6.6-x86_64-unknown-linux-gnu.zip",
        "layout": {"custota-tool": "0755"},
        "url": (
            "https://github.com/chenxiaolong/Custota/releases/download/"
            "v6.6/custota-tool-6.6-x86_64-unknown-linux-gnu.zip"
        ),
    },
}

tools = data.get("tools")
if not isinstance(tools, list) or len(tools) != 3:
    raise SystemExit("lock must contain exactly three tools")
if [tool.get("id") for tool in tools] != sorted(expected):
    raise SystemExit("tools must be in canonical ID order")

trust_lines = [
    line.strip()
    for line in trust_path.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
if len(trust_lines) != 1:
    raise SystemExit("trust file must contain exactly one active signer")
trust_identity = trust_lines[0].split(maxsplit=1)[0]

hash_re = re.compile(r"[0-9a-f]{64}")
fingerprint = "SHA256:Ct0HoRyrFLrnF9W+A/BKEiJmwx7yWkgaW/JvghKrboA"
for tool in tools:
    tool_id = tool["id"]
    pin = expected[tool_id]
    if set(tool) != {
        "id", "version", "arch", "artifact_name", "url", "size",
        "sha256", "signature", "layout",
    }:
        raise SystemExit(f"{tool_id}: tool fields are not exact")
    if tool["version"] != pin["version"]:
        raise SystemExit(f"{tool_id}: wrong version")
    if tool["arch"] != "x86_64-unknown-linux-gnu":
        raise SystemExit(f"{tool_id}: wrong architecture")
    if tool["artifact_name"] != pin["artifact"] or tool["url"] != pin["url"]:
        raise SystemExit(f"{tool_id}: wrong canonical artifact identity")
    if tool_id == "avbroot":
        if tool["size"] != pin["archive_size"]:
            raise SystemExit(f"{tool_id}: wrong archive size")
        if tool["sha256"] != pin["archive_sha256"]:
            raise SystemExit(f"{tool_id}: wrong archive SHA-256")
    if not isinstance(tool["size"], int) or isinstance(tool["size"], bool) or tool["size"] <= 0:
        raise SystemExit(f"{tool_id}: archive size must be positive")
    if not hash_re.fullmatch(tool["sha256"]):
        raise SystemExit(f"{tool_id}: archive SHA-256 is not canonical")

    signature = tool["signature"]
    if set(signature) != {
        "url", "type", "identity", "namespace", "signer_fingerprint",
    }:
        raise SystemExit(f"{tool_id}: signature fields are not exact")
    if signature["url"] != tool["url"] + ".sig":
        raise SystemExit(f"{tool_id}: signature URL is not artifact-bound")
    if signature["type"] != "ssh" or signature["namespace"] != "file":
        raise SystemExit(f"{tool_id}: wrong signature protocol")
    if signature["identity"] != trust_identity:
        raise SystemExit(f"{tool_id}: signature identity is not trust-bound")
    if signature["signer_fingerprint"] != fingerprint:
        raise SystemExit(f"{tool_id}: signer fingerprint is not the reviewed pin")

    layout = tool["layout"]
    if not isinstance(layout, list) or not layout:
        raise SystemExit(f"{tool_id}: layout must be a nonempty exact allowlist")
    if [member.get("path") for member in layout] != sorted(
        member.get("path") for member in layout
    ):
        raise SystemExit(f"{tool_id}: layout is not in canonical path order")
    actual_layout = {member.get("path"): member.get("mode") for member in layout}
    if actual_layout != pin["layout"]:
        raise SystemExit(f"{tool_id}: layout does not match the reviewed release")
    for member in layout:
        if set(member) != {"path", "type", "size", "sha256", "mode"}:
            raise SystemExit(f"{tool_id}: layout fields are not exact")
        if member["type"] != "file":
            raise SystemExit(f"{tool_id}: layout members must be regular files")
        if not isinstance(member["size"], int) or isinstance(member["size"], bool) or member["size"] <= 0:
            raise SystemExit(f"{tool_id}: member size must be positive")
        if not hash_re.fullmatch(member["sha256"]):
            raise SystemExit(f"{tool_id}: member SHA-256 is not canonical")
        if tool_id == "avbroot":
            expected_member = pin["members"].get(member["path"])
            if expected_member is None or member["size"] != expected_member["size"] or member["sha256"] != expected_member["sha256"]:
                raise SystemExit(f"{tool_id}: wrong locked member integrity for {member['path']}")
    executable = next(member for member in layout if member["path"] == tool_id)
    if executable["mode"] != "0755":
        raise SystemExit(f"{tool_id}: executable mode must be canonical 0755")
PY
}

test_invalid_locks_fail_closed() {
  local case_name
  local -a cases=(
    unknown_tool
    unknown_version
    unknown_arch
    unknown_top_level_field
    unknown_tool_field
    duplicate_identity
    duplicate_member
    wrong_artifact_name
    noncanonical_artifact_url
    floating_artifact_url
    mutable_branch_url
    noncanonical_archive_hash
    short_archive_hash
    signature_url_mismatch
    floating_signature_url
    unknown_signature_type
    wrong_signature_identity
    wrong_signature_namespace
    signer_fingerprint_mismatch
    symlink_layout
    special_layout
    extra_layout_member
    noncanonical_layout_mode
    wrong_layout_mode
    noncanonical_executable_hash
    short_executable_hash
    missing_artifact_size
    missing_archive_hash
    missing_signature_url
    missing_signature_type
    missing_signature_identity
    missing_signature_namespace
    missing_signer_fingerprint
    missing_layout
    missing_executable_size
    missing_executable_hash
    missing_executable_mode
    noncanonical_json_form
  )

  for case_name in "${cases[@]}"; do
    assert_rejected "${case_name}"
  done
}

test_trust_documentation_contract() {
  grep -Eqi 'provenance|authoritative source' "${DOC}" ||
    fail "trust documentation does not record signer provenance"
  grep -Eqi 'fingerprint|SHA256:' "${DOC}" ||
    fail "trust documentation does not record the reviewed signer fingerprint"
  grep -Eqi 'rotat(e|ion)|replacement key' "${DOC}" ||
    fail "trust documentation does not define signer rotation"
  grep -Eqi 'review|approval' "${DOC}" ||
    fail "trust documentation does not require review for rotation"
  grep -Eqi \
    '(does not|never|no).*(authoriz(e|ation)|permit).*(extract|execut|install)|verification.*(does not|never).*(extract|execut|install)' \
    "${DOC}" ||
    fail "trust documentation does not state that verification grants no execution authorization"
}

test_canonical_committed_lock
test_invalid_locks_fail_closed
test_trust_documentation_contract

echo "executable tool lock tests passed"
