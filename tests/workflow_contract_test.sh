#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 PixeneOS contributors

set -euo pipefail

WORKFLOW_DIR=".github/workflows"
REUSABLE="${WORKFLOW_DIR}/build-rom.yml"
RELEASE="${WORKFLOW_DIR}/release.yml"

fail() {
  echo "$*" >&2
  exit 1
}

assert_contains() {
  local file="${1}"
  local pattern="${2}"
  local context="${3}"

  grep -Eq -- "${pattern}" "${file}" ||
    fail "${context}: ${file} does not match ${pattern}"
}

assert_not_contains() {
  local file="${1}"
  local pattern="${2}"
  local context="${3}"

  if grep -Eq -- "${pattern}" "${file}"; then
    fail "${context}: ${file} unexpectedly matches ${pattern}"
  fi
}

assert_thin_trigger() {
  local file="${1}"
  local family="${2}"

  [[ -f "${file}" ]] || fail "missing ${family} release trigger: ${file}"
  assert_contains \
    "${file}" \
    'uses:[[:space:]]*\./\.github/workflows/build-rom\.yml' \
    "${family} trigger must call the shared workflow"
  assert_contains \
    "${file}" \
    "rom-family:[[:space:]]*['\"]?${family}['\"]?" \
    "${family} trigger must select its ROM family"
  assert_not_contains \
    "${file}" \
    'uses:[[:space:]]*actions/checkout@' \
    "thin triggers must not duplicate checkout/build steps"
  assert_not_contains \
    "${file}" \
    '(^|[[:space:]])ref:[[:space:]]*['\"]?lineage['\"]?([[:space:]#]|$)' \
    "release triggers must not check out a divergent lineage branch"
}

assert_dispatch_default() {
  local file="${1}"
  local input="${2}"
  local expected="${3}"
  local actual
  local trigger="${4:-}"

  if [[ -z "${trigger}" ]]; then
    trigger='workflow_call'
    if grep -Eq '^[[:space:]]{2}workflow_dispatch:[[:space:]]*$' "${file}"; then
      trigger='workflow_dispatch'
    fi
  fi

  actual="$(awk -v input="${input}" -v trigger="${trigger}" '
    $0 ~ "^[[:space:]]{2}" trigger ":[[:space:]]*$" { in_trigger = 1; next }
    in_trigger && $0 ~ "^[[:space:]]{2}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_trigger && $0 ~ "^[[:space:]]{6}" input ":[[:space:]]*$" { in_input = 1; next }
    in_input && $0 ~ "^[[:space:]]{6}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_input && $0 ~ "^[[:space:]]+default:[[:space:]]*" {
      sub(/^.*default:[[:space:]]*/, "")
      gsub(/[[:space:]\047"]/, "")
      print
      exit
    }
  ' "${file}")"

  [[ "${actual}" == "${expected}" ]] ||
    fail "${file}: ${input} default expected ${expected}, got ${actual:-missing}"
}

assert_workflow_input_contract() {
  local file="${1}"
  local input="${2}"
  local expected_type="${3}"
  local expected_default="${4}"
  local actual
  local trigger='workflow_call'

  if grep -Eq '^[[:space:]]{2}workflow_dispatch:[[:space:]]*$' "${file}"; then
    trigger='workflow_dispatch'
  fi

  actual="$(awk -v input="${input}" -v trigger="${trigger}" '
    $0 ~ "^[[:space:]]{2}" trigger ":[[:space:]]*$" { in_trigger = 1; next }
    in_trigger && $0 ~ "^[[:space:]]{2}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_trigger && $0 ~ "^[[:space:]]{6}" input ":[[:space:]]*$" { in_input = 1; next }
    in_input && $0 ~ "^[[:space:]]{6}[A-Za-z0-9_-]+:[[:space:]]*$" { exit }
    in_input && $0 ~ "^[[:space:]]+type:[[:space:]]*" {
      sub(/^.*type:[[:space:]]*/, "")
      gsub(/[[:space:]\047"]/, "")
      type = $0
    }
    in_input && $0 ~ "^[[:space:]]+default:[[:space:]]*" {
      sub(/^.*default:[[:space:]]*/, "")
      gsub(/[[:space:]\047"]/, "")
      default_value = $0
    }
    END {
      if (type != "" && default_value != "") print type "|" default_value
    }
  ' "${file}")"

  [[ "${actual}" == "${expected_type}|${expected_default}" ]] ||
    fail "${file}: ${input} input expected ${expected_type}|${expected_default}, got ${actual:-missing}"
}

test_reusable_workflow() {
  [[ -f "${REUSABLE}" ]] || fail "missing reusable ROM workflow: ${REUSABLE}"
  assert_contains \
    "${REUSABLE}" \
    'workflow_call:' \
    "shared ROM workflow must be reusable"
  assert_contains \
    "${REUSABLE}" \
    'rom-family:' \
    "shared ROM workflow must accept a ROM family"
  assert_dispatch_default "${REUSABLE}" boot-animation true
  assert_dispatch_default "${WORKFLOW_DIR}/multi-release.yml" boot-animation true
  assert_dispatch_default "${REUSABLE}" afsr true
  assert_contains \
    "${REUSABLE}" \
    'ADDITIONALS_BOOT_ANIMATION' \
    "shared workflow must pass the boot-animation selection"
  assert_contains \
    "${REUSABLE}" \
    'ADDITIONALS_AFSR' \
    "shared workflow must pass the AFSR selection"
  local contract input expected_type expected_default
  for contract in \
    'afsr:boolean:true' \
    'alterinstaller:boolean:true' \
    'bcr:boolean:true' \
    'custota:boolean:true' \
    'msd:boolean:true' \
    'oemunlockonboot:boolean:true' \
    'microg:boolean:false' \
    'disable-system-updater:boolean:false' \
    'fdroid-privileged-extension:boolean:false'; do
    IFS=: read -r input expected_type expected_default <<<"${contract}"
    assert_workflow_input_contract \
      "${REUSABLE}" "${input}" "${expected_type}" "${expected_default}"
  done
  assert_contains \
    "${REUSABLE}" \
    'uses:[[:space:]]*actions/checkout@' \
    "shared ROM workflow must own checkout"
  assert_contains \
    "${REUSABLE}" \
    'MODULE_SELECTION_FINGERPRINT' \
    "shared workflow must retain the full selection fingerprint as metadata"
  assert_contains \
    "${REUSABLE}" \
    '"artifact_name": artifact_name' \
    "selection metadata must bind the exact output filename"
  assert_contains \
    "${REUSABLE}" \
    '"grapheneos_version": version' \
    "selection metadata must bind the GrapheneOS version"
  assert_contains \
    "${REUSABLE}" \
    '"schema_version": 2' \
    "selection metadata must use the variant-aware schema"
  assert_contains \
    "${REUSABLE}" \
    'OUTPUT_SCOPE' \
    "shared workflow must set an explicit output scope"
  assert_contains \
    "${REUSABLE}" \
    'enforce_output_policy' \
    "shared workflow must enforce policy before release or upload"
  assert_contains \
    "${REUSABLE}" \
    'enforce_publication_evidence' \
    "shared workflow must enforce publication evidence"
  assert_contains \
    "${REUSABLE}" \
    'MODULE_SELECTION_FINGERPRINT="\$\{MODULE_SELECTION_FINGERPRINT_ROOTLESS\}"' \
    "paired rootless cleanup must bind the rootless fingerprint"
  assert_contains \
    "${REUSABLE}" \
    'MODULE_SELECTION_FINGERPRINT="\$\{MODULE_SELECTION_FINGERPRINT_MAGISK\}"' \
    "paired Magisk cleanup must bind the Magisk fingerprint"
  # Workflow credentials have no shell test equivalent. Keep these two
  # boundary assertions here rather than duplicate the complete workflow,
  # helper pins, or source code in another integration contract suite.
  assert_contains "${REUSABLE}" \
    "GH_TOKEN:.*inputs\\.publish.*inputs\\.release-type != 'build-only'.*secrets\\.GH_TOKEN" \
    "build-only must not receive the separate release credential"
  assert_not_contains "${REUSABLE}" \
    'GH_TOKEN:[[:space:]]*\$\{\{ secrets\.GH_TOKEN \}\}' \
    "release credential must not be unconditional"
  # The sole upload uses the explicitly allowlisted device-acceptance stage,
  # verified behaviorally below.
  assert_contains "${REUSABLE}" \
    'path:.*steps\.candidate_export\.outputs\.stage' \
    "device-acceptance upload must use only the allowlisted staging output"
  assert_not_contains \
    "${REUSABLE}" \
    '(^|[[:space:]])ref:[[:space:]]*['\"]?lineage['\"]?([[:space:]#]|$)' \
    "shared ROM workflow must not check out a divergent lineage branch"
}

# Credential checks stay with the existing workflow suite. Generate real disposable
# material to check the boundary's behavior, rather than freezing shell text.
test_build_only_signing() (
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "${temp_dir}"' EXIT
  GITHUB_ENV="${temp_dir}/env" RUNNER_TEMP="${temp_dir}" \
    GITHUB_STEP_SUMMARY="${temp_dir}/summary" \
    bash src/ci/setup_test_signing.sh >/dev/null
  # Split only the assignment separator: read with IFS='=' strips a single
  # trailing '=' padding byte; older GNU base64 rejects the truncated value.
  local binding name encoded
  while IFS= read -r binding; do
    name="${binding%%=*}"
    encoded="${binding#*=}"
    case "${name}" in
      KEYS_AVB_BASE64) printf '%s' "${encoded}" | base64 -d > "${temp_dir}/avb.key" ;;
      KEYS_OTA_BASE64) printf '%s' "${encoded}" | base64 -d > "${temp_dir}/ota.key" ;;
      KEYS_CERT_OTA_BASE64) printf '%s' "${encoded}" | base64 -d > "${temp_dir}/ota.crt" ;;
      *) fail "Unexpected build-only signing export: ${name}" ;;
    esac
  done < "${temp_dir}/env"
  openssl pkey -in "${temp_dir}/avb.key" -check -noout >/dev/null
  openssl pkey -in "${temp_dir}/ota.key" -pubout -out "${temp_dir}/key.pub"
  openssl x509 -in "${temp_dir}/ota.crt" -pubkey -noout > "${temp_dir}/cert.pub"
  cmp "${temp_dir}/key.pub" "${temp_dir}/cert.pub" || fail 'Disposable OTA signer and certificate differ'
  if find "${temp_dir}" -type d -name 'modos-test-signing.*' | grep -q .; then
    fail 'Disposable signing scratch directory was retained'
  fi
)

test_build_only_permissions() {
  local workflow
  for workflow in "${REUSABLE}" "${RELEASE}" \
    "${WORKFLOW_DIR}/release-lineage.yml" "${WORKFLOW_DIR}/multi-release.yml"; do
    # Build-only routes must have their own read-only token and omit secret
    # forwarding. Shared steps/inputs are anchors, not duplicated pipelines.
    awk '
      /^  (build_only|manual_build_only):$/ { active = 1; next }
      active && /^  [A-Za-z0-9_-]+:$/ { active = 0 }
      active && /contents: read/ { readonly = 1 }
      active && /secrets:/ { forbidden = 1 }
      END { exit !(readonly && !forbidden) }
    ' "${workflow}" || fail "Build-only credential boundary failed: ${workflow}"
  done
  # A read-only multi-device caller must reach the shared builder directly;
  # the dispatch/publishing wrapper also contains write-scoped jobs.
  local multi_route
  multi_route="$(awk '/^  build_only:$/ { active=1; next } active { print }' \
    "${WORKFLOW_DIR}/multi-release.yml")"
  [[ "${multi_route}" == *'uses: ./.github/workflows/build-rom.yml'* ]] || \
    fail 'Multi-device build-only route must call the shared builder directly'
  assert_dispatch_default "${REUSABLE}" rom-family grapheneos
  assert_dispatch_default "${REUSABLE}" publish false
}

# Evaluate the actual workflow routing/credential expressions across signing
# modes; keep ordinary build-only on disposable keys and candidate API tokens off.
test_device_candidate_signing_routes() {
  python3 - <<'PYTEST'
from pathlib import Path
import re

build = Path(".github/workflows/build-rom.yml").read_text()
lineage = Path(".github/workflows/release-lineage.yml").read_text()

def block(text, name, indent):
    match = re.search(r"^" + " " * indent + re.escape(name) + r":\n(.*?)(?=^" +
                      " " * indent + r"[A-Za-z0-9_-]+:|\Z)", text, re.M | re.S)
    assert match, name
    return match.group(1)

def condition(text):
    return re.search(r"^\s+if: (.+)$", text, re.M).group(1)

def step_condition(name):
    section = build.split("      - name: " + name + "\n", 1)[1]
    return condition(section.split("      - name:", 1)[0])

def value(expression, inputs, secrets=None):
    expression = expression.removeprefix("${{ ").removesuffix(" }}")
    expression = re.sub(r"inputs\.([a-z-]+)", lambda m: repr(inputs[m[1]]), expression)
    expression = re.sub(r"secrets\.([A-Z_]+)", lambda m: repr((secrets or {})[m[1]]), expression)
    expression = expression.replace("github.event_name", repr("workflow_dispatch"))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression)
    return eval(expression.strip(), {"__builtins__": {}}, {})

def binding(name):
    return re.search(r"^\s+" + name + r": (\$\{\{.*?\}\})$", build, re.M)[1]

routes = {name: block(lineage, name, 2) for name in
          ("manual_build", "manual_build_only", "manual_device_candidate")}
# GitHub validates every reusable job's permission request before evaluating
# its if-condition. Inherit publisher permissions from the selected caller;
# keep the executed build-only job read-only even under a write-capable caller.
assert not re.search(r"^permissions:", build, re.M)
assert "permissions:" not in block(build, "build", 2)
assert "contents: write" in routes["manual_build"]
signer = step_condition("Setup signing environment")
disposable = step_condition("Generate disposable build-only signing keys")
assert "default: disposable" in block(block(lineage, "workflow_dispatch", 2), "signing-mode", 6)
assert "secrets:" not in routes["manual_build_only"]
candidate_bindings = re.findall(r"^      ([A-Z_]+):", routes["manual_device_candidate"], re.M)
assert set(candidate_bindings) == {"AVB_KEY", "CERT_OTA", "OTA_KEY", "PASSPHRASE_AVB", "PASSPHRASE_OTA"}
assert "contents: read" in routes["manual_device_candidate"]
assert "persist-credentials: ${{ inputs.publish" in build
for release_type in ("default", "build-only"):
    for mode in ("disposable", "repository"):
        inputs = {"release-type": release_type, "signing-mode": mode,
                  "publish": release_type != "build-only", "source-artifact-id": ""}
        selected = [name for name, section in routes.items() if value(condition(section), inputs)]
        expected = "manual_build" if inputs["publish"] else (
            "manual_device_candidate" if mode == "repository" else "manual_build_only")
        assert selected == [expected], (inputs, selected)
        transport_inputs = {**inputs, "source-artifact-id": "123"}
        assert not any(value(condition(section), transport_inputs) for section in routes.values())
        assert value(condition(block(lineage, "transport_plan", 2)), transport_inputs)
        assert not value(condition(block(lineage, "transport_plan", 2)), inputs)
        repository_signer = inputs["publish"] or mode == "repository"
        assert bool(value(signer, inputs)) == repository_signer
        assert bool(value(disposable, inputs)) != repository_signer
        assert value(binding("GH_TOKEN"), inputs, {"GH_TOKEN": "api-token"}) == (
            "api-token" if inputs["publish"] else "")
        for name in ("PASSPHRASE_AVB", "PASSPHRASE_OTA"):
            assert value(binding(name), inputs, {name: "signing-passphrase"}) == (
                "signing-passphrase" if repository_signer else "")
for name in ("transport_plan", "transport_part"):
    section = block(lineage, name, 2)
    assert "contents: read" in section and "actions: read" in section
    assert "secrets." not in section and "secrets:" not in section
    assert "persist-credentials: false" in section
assert "inputs.source-artifact-id == ''" in block(lineage, "scheduled_build", 2)
for name in ("source-artifact-id", "expected-source-zip-sha256"):
    assert 'default: ""' in block(block(lineage, "workflow_dispatch", 2), name, 6)
PYTEST
  python3 tests/transport_candidate_artifact_test.py
}

test_encrypted_device_candidate_request_gate() {
  python3 - <<'PYTEST'
from pathlib import Path
import os
import re
import subprocess
import tempfile

workflow = Path('.github/workflows/build-rom.yml').read_text()
step = workflow.split('      - name: Validate build request\n', 1)[1].split('      - name:', 1)[0]
script = step.split('        run: |\n', 1)[1]
script = '\n'.join(line[10:] for line in script.splitlines())
base = {**os.environ, 'EXPORT_DEVICE_CANDIDATE': 'true',
        'DEVELOPMENT_PROFILE': 'care-map-pdx235', 'SIGNING_MODE': 'repository',
        'RELEASE_TYPE': 'build-only', 'PUBLISH_REQUEST': 'false',
        'DEVICE_CANDIDATE_RECIPIENT_BASE64': 'public-recipient-tested-separately',
        'ROOT_MODE_INPUT': 'rootless', 'ADDITIONALS_ROOT': 'false', 'MAGISK_PREINIT_REQUEST': ''}
with tempfile.TemporaryDirectory() as scratch:
    base['GITHUB_ENV'] = str(Path(scratch) / 'env')
    assert subprocess.run(['bash', '-euc', script], env=base, capture_output=True).returncode == 0
    assert subprocess.run(['bash', '-euc', script], env={**base, 'DEVICE_CANDIDATE_RECIPIENT_BASE64': ''},
                          capture_output=True).returncode == 0
    for name, forbidden in [('DEVELOPMENT_PROFILE', 'none'), ('SIGNING_MODE', 'disposable'),
                            ('RELEASE_TYPE', 'default'), ('PUBLISH_REQUEST', 'true')]:
        assert subprocess.run(['bash', '-euc', script], env={**base, name: forbidden},
                              capture_output=True).returncode != 0, name
# The one artifact destination is the export script's fresh allowlisted stage,
# after normal patch/postflight and metadata steps; no working-directory glob.
upload = workflow.split('      - name: Upload device candidate\n', 1)[1].split('      - name:', 1)[0]
assert workflow.count('uses: actions/upload-artifact@') == 1
assert 'name: ${{ steps.candidate_export.outputs.artifact-name }}' in upload
assert 'if: inputs.export-device-candidate' in upload
assert re.search(r'^          path: \$\{\{ steps.candidate_export.outputs.stage \}\}$', upload, re.M)
assert workflow.index('      - name: Record build metadata') < workflow.index('      - name: Stage device candidate')
PYTEST
  python3 tests/encrypted_device_candidate_test.py
}

test_development_profile_activation() (
  source src/util_functions.sh
  local original_pin="${VERSION[AVBROOT_SETUP]}" fixture repository_root
  repository_root="$(pwd -P)"
  PIXENEOS_DEVELOPMENT_PROFILE=none
  activate_development_profile
  [[ "${VERSION[AVBROOT_SETUP]}" == "${original_pin}" ]] || fail 'Default profile changed helper pin'
  PIXENEOS_DEVELOPMENT_PROFILE=unreviewed
  if validate_development_profile local-unpublished 2>/dev/null; then fail 'Unknown development profile accepted'; fi
  PIXENEOS_DEVELOPMENT_PROFILE=care-map-pdx235
  ROM_FAMILY=lineageos DEVICE_NAME=pdx235 OUTPUT_SCOPE=local-unpublished
  validate_development_profile
  for OUTPUT_SCOPE in private shared published; do
    if validate_development_profile 2>/dev/null; then fail 'Development shared scope accepted'; fi
  done
  OUTPUT_SCOPE=local-unpublished DEVICE_NAME=other
  if validate_development_profile 2>/dev/null; then fail 'Development wrong device accepted'; fi
  DEVICE_NAME=pdx235 ROM_FAMILY=grapheneos
  if validate_development_profile 2>/dev/null; then fail 'Development wrong family accepted'; fi
  ROM_FAMILY=lineageos
  fixture="$(mktemp -d)"
  trap 'rm -rf "${fixture}"' EXIT
  git init -q "${fixture}"
  cd "${fixture}"
  if activate_development_profile 2>/dev/null; then fail 'Missing reviewed metadata accepted'; fi
  mkdir -p tools/compat
  cp "${repository_root}/tools/compat/avbroot_setup_compat.py" tools/compat/
  python3 - "${repository_root}/tools/compat/avbroot_setup_compat.json" <<'PYTEST'
import json, pathlib, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text())
manifest["revision"] = "a" * 40
pathlib.Path("tools/compat/avbroot_setup_care_map_pdx235.json").write_text(json.dumps(manifest))
PYTEST
  git add tools/compat
  git -c user.name=Fixture -c user.email=fixture@example.invalid -c core.hooksPath=/dev/null commit -qm fixture
  activate_development_profile
  [[ "${VERSION[AVBROOT_SETUP]}" == aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ]] || fail 'Reviewed development helper not selected'
)

test_release_triggers() {
  local lineage="${WORKFLOW_DIR}/release-lineage.yml"

  assert_thin_trigger "${RELEASE}" grapheneos

  [[ -f "${lineage}" ]] || fail "missing LineageOS release trigger: ${lineage}"
  assert_contains "${lineage}" 'schedule:' "LineageOS release must remain scheduled"
  assert_contains "${lineage}" 'uses:[[:space:]]*\./\.github/workflows/build-rom\.yml'     "LineageOS release must call the shared workflow"
  assert_contains "${lineage}" 'rom-family:[[:space:]]*lineageos'     "LineageOS manual build must select the LineageOS profile"
  assert_contains "${lineage}" 'SCHEDULE_DEFINITION:[[:space:]]*\.github/schedules/lineageos-pdx235\.toml'     "LineageOS schedule must use the pdx235 definition"
  assert_contains "${RELEASE}" '^[[:space:]]+- build-only[[:space:]]*$'     "GrapheneOS manual release must expose build-only mode"
  assert_contains "${lineage}" '^[[:space:]]+- build-only[[:space:]]*$'     "LineageOS manual release must expose build-only mode"

  assert_dispatch_default "${RELEASE}" device-id shiba
  assert_dispatch_default "${RELEASE}" root true
  assert_dispatch_default "${RELEASE}" alterinstaller true
  assert_dispatch_default "${RELEASE}" bcr true
  assert_dispatch_default "${RELEASE}" custota true
  assert_dispatch_default "${RELEASE}" msd true
  assert_dispatch_default "${RELEASE}" oemunlockonboot true
  assert_dispatch_default "${RELEASE}" disable-system-updater false
  assert_dispatch_default "${RELEASE}" fdroid-privileged-extension false
  assert_dispatch_default "${RELEASE}" boot-animation true
  assert_dispatch_default "${RELEASE}" boot-animation true workflow_call
  assert_dispatch_default "${RELEASE}" compatible-sepolicy-patching false
  assert_dispatch_default "${lineage}" device-id pdx235
  assert_dispatch_default "${lineage}" root true
  assert_dispatch_default "${lineage}" compatible-sepolicy-patching true
  assert_dispatch_default "${lineage}" microg false
  assert_dispatch_default "${lineage}" disable-system-updater false
  assert_dispatch_default "${lineage}" boot-animation true
  assert_contains "${lineage}" 'boot-animation:[[:space:]]*\$\{\{ inputs\.boot-animation \}\}' "LineageOS manual boot animation input must be forwarded"
}

test_config_loading_isolated_to_scheduled_releases() {
  local lineage="${WORKFLOW_DIR}/release-lineage.yml"

  assert_contains "${RELEASE}"     'SCHEDULE_DEFINITION:[[:space:]]*\.github/schedules/grapheneos-shiba\.toml'     "GrapheneOS schedule must use the shiba definition"
  assert_contains "${lineage}"     'SCHEDULE_DEFINITION:[[:space:]]*\.github/schedules/lineageos-pdx235\.toml'     "LineageOS schedule must use the pdx235 definition"
  assert_not_contains "${lineage}" 'env\.toml'     "LineageOS schedule must not inherit repository-wide local env.toml"
}

test_release_configuration_forwarding() {
  local loader="src/ci/load_schedule_definition.sh"

  assert_contains "${RELEASE}"     'name:[[:space:]]*Load scheduled build definition'     "scheduled GrapheneOS release must load a checked-in definition"
  assert_contains "${RELEASE}"     'if:[[:space:]]*github\.event_name == .schedule.'     "scheduled configuration must be schedule-only"
  assert_contains "${RELEASE}"     'run:[[:space:]]*bash src/ci/load_schedule_definition\.sh'     "GrapheneOS schedule must use the shared definition loader"
  assert_contains "${loader}"     'check_toml_env "\$\{SCHEDULE_DEFINITION\}"'     "schedule loader must use the typed TOML parser"
  assert_contains "${loader}"     'toml_config_has "\$\{key\}"'     "schedule loader must require every declared schedule key"
  assert_contains "${loader}"     'disable_system_updater="\$\(toml_resolve_value disable_system_updater'     "schedule loader must resolve updater removal"
  assert_contains "${loader}"     'boot_animation="\$\(toml_resolve_value boot_animation'     "schedule loader must resolve the boot-animation selection"
  assert_contains "${loader}"     'compatible_sepolicy_patching="\$\(toml_resolve_value compatible_sepolicy_patching'     "schedule loader must resolve compatible-SEPolicy selection"
  assert_contains "${loader}"     'force_update="\$\(toml_resolve_value force_update'     "schedule loader must resolve FORCE_UPDATE"
  assert_contains "${RELEASE}"     'name:[[:space:]]*Resolve expected selection identity'     "release preflight must resolve the canonical selection identity"
  assert_contains "${RELEASE}"     'module_selection_fingerprint'     "release preflight must use the shared selection fingerprint"
  assert_contains "${RELEASE}"     'expected_variant:[[:space:]]*\$\{\{ steps\.selection_identity\.outputs\.expected_variant \}\}'     "release preflight must expose the resolved identity"
  assert_contains "${RELEASE}"     'EXPECTED_VARIANT:[[:space:]]*\$\{\{ steps\.selection_identity\.outputs\.expected_variant \}\}'     "existing-build preflight must receive the resolved identity"
  assert_contains "${RELEASE}"     'force_update:[[:space:]]*\$\{\{ steps\.scheduled_config\.outputs\.force_update \}\}'     "preflight must expose scheduled FORCE_UPDATE as a job output"
  assert_contains "${RELEASE}"     'FORCE_UPDATE:[[:space:]]*\$\{\{ github\.event_name == .schedule. && steps\.scheduled_config\.outputs\.force_update \|\| false \}\}'     "existing-build preflight must receive scheduled FORCE_UPDATE"
  assert_contains "${RELEASE}"     'device-id:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.device_id \|\| inputs\.device-id \}\}'     "manual device input must remain authoritative"
  assert_contains "${RELEASE}"     'disable-system-updater:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.disable_system_updater == .true. \|\| inputs\.disable-system-updater \}\}'     "updater removal must be forwarded from scheduled definition or manual input"
  assert_contains "${RELEASE}"     'boot-animation:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.boot_animation == .true. \|\| github\.event_name != .schedule. && inputs\.boot-animation \}\}'     "boot animation must respect scheduled and manual explicit false values"
  assert_contains "${RELEASE}"     'ADDITIONALS_BOOT_ANIMATION:[[:space:]]*\$\{\{ github\.event_name == .schedule. && steps\.scheduled_config\.outputs\.boot_animation == .true. \|\| github\.event_name != .schedule. && inputs\.boot-animation \}\}'     "preflight identity must respect scheduled and manual boot animation opt-outs"
  assert_contains "${RELEASE}"     'compatible-sepolicy-patching:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.compatible_sepolicy_patching == .true.'     "compatible SEPolicy must be forwarded from the scheduled definition"
  assert_contains "${RELEASE}"     'root:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.root == .true. \|\| inputs\.root \}\}'     "root must explicitly coerce the scheduled string output to boolean"
  assert_contains "${RELEASE}"     'magisk-preinit-device:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.magisk_preinit_device'     "scheduled preinit configuration must be forwarded"
  assert_contains "${RELEASE}"     'update-channel:[[:space:]]*\$\{\{ github\.event_name == .schedule. && needs\.preflight\.outputs\.update_channel'     "scheduled update channel must be forwarded"
}

test_microg_lineage_forwarding() {
  local lineage="${WORKFLOW_DIR}/release-lineage.yml"

  assert_contains "${REUSABLE}"     'ADDITIONALS_MICROG:[[:space:]].*inputs\.microg'     "reusable workflow must map MICROG from its input"
  assert_contains "${lineage}"     'microg:[[:space:]]*\$\{\{ inputs\.microg \}\}'     "LineageOS manual build must forward microG"
  assert_not_contains "${RELEASE}"     'inputs\.microg'     "GrapheneOS workflow must not expose a microG switch"
}

test_module_forwarding_contract() {
  local contract input config output
  local loader="src/ci/load_schedule_definition.sh"

  for contract in \
    'afsr:AFSR:afsr' \
    'alterinstaller:ALTERINSTALLER:alterinstaller' \
    'bcr:BCR:bcr' \
    'custota:CUSTOTA:custota' \
    'msd:MSD:msd' \
    'oemunlockonboot:OEMUNLOCKONBOOT:oemunlockonboot' \
    'disable-system-updater:DISABLE_SYSTEM_UPDATER:disable_system_updater' \
    'fdroid-privileged-extension:FDROID_PRIVILEGED_EXTENSION:fdroid_privileged_extension'; do
    IFS=: read -r input config output <<<"${contract}"

    assert_contains \
      "${loader}" \
      "toml_resolve_value ${output}" \
      "schedule loader must resolve ${config} through the typed contract"
    assert_contains \
      "${loader}" \
      "echo \"${output}=" \
      "schedule loader must expose ${output}"
    assert_contains \
      "${RELEASE}" \
      "steps\\.scheduled_config\\.outputs\\.${output}" \
      "preflight must expose ${output}"
    assert_contains \
      "${RELEASE}" \
      "ADDITIONALS_${config}:[[:space:]].*steps\\.scheduled_config\\.outputs\\.${output}.*inputs\\.${input}" \
      "expected identity must use scheduled/manual ${input}"
    assert_contains \
      "${RELEASE}" \
      "needs\\.preflight\\.outputs\\.${output}[[:space:]]*==[[:space:]]*['\"]true['\"].*inputs\\.${input}" \
      "build invocation must use scheduled/manual ${input}"
    assert_contains \
      "${REUSABLE}" \
      "ADDITIONALS_${config}:[[:space:]].*inputs\\.${input}" \
      "reusable build must map ${config} from input ${input}"
  done
}

test_publication_identity_is_step_scoped() {
  assert_contains \
    "${WORKFLOW_DIR}/build-rom.yml" \
    'GIT_COMMIT_EMAIL:[[:space:]]*\$\{\{ secrets\.EMAIL \}\}' \
    "publication identity email must be step-scoped"
  assert_contains \
    "${WORKFLOW_DIR}/build-rom.yml" \
    'GIT_COMMIT_NAME:[[:space:]]*\$\{\{ github\.repository_owner \}\}' \
    "publication identity name must be step-scoped"
  assert_contains \
    "src/ci/publish_ota.sh" \
    'git config user\.email "\$\{GIT_COMMIT_EMAIL-\}"' \
    "publication helper must configure local email from the step environment"
  assert_contains \
    "src/ci/publish_ota.sh" \
    'git config user\.name "\$\{GIT_COMMIT_NAME-\}"' \
    "publication helper must configure local name from the step environment"
  assert_not_contains \
    "${WORKFLOW_DIR}/build-rom.yml" \
    'git config user\.email .*secrets\.EMAIL' \
    "publication must not interpolate the email secret in shell source"
}

test_workflow_inventory_is_intentional() (
  local expected actual

  expected="$(printf '%s\n' \
    build-rom.yml \
    ci.yml \
    multi-release.yml \
    release-lineage.yml \
    release.yml \
    renovate.yml)"

  actual="$(find "${WORKFLOW_DIR}" -maxdepth 1 -type f \
    \( -name '*.yml' -o -name '*.yaml' \) -exec basename {} \; | sort)"

  [[ "${actual}" == "${expected}" ]] || {
    printf 'unexpected workflow inventory\nexpected:\n%s\nactual:\n%s\n' \
      "${expected}" "${actual}" >&2
    exit 1
  }
)

test_no_lineage_checkout_anywhere() {
  local file

  while IFS= read -r -d '' file; do
    assert_not_contains \
      "${file}" \
      '(^|[[:space:]])ref:[[:space:]]*['\"]?lineage['\"]?([[:space:]#]|$)' \
      "workflows must use the main branch implementation"
  done < <(find "${WORKFLOW_DIR}" -maxdepth 1 -type f \
    \( -name '*.yml' -o -name '*.yaml' \) -print0)
}

test_reusable_workflow
test_build_only_signing
test_build_only_permissions
test_device_candidate_signing_routes
test_encrypted_device_candidate_request_gate
test_development_profile_activation
test_release_triggers
test_config_loading_isolated_to_scheduled_releases
test_release_configuration_forwarding
test_microg_lineage_forwarding
test_module_forwarding_contract
test_publication_identity_is_step_scoped
test_workflow_inventory_is_intentional
test_no_lineage_checkout_anywhere

echo "Workflow contract tests passed"
