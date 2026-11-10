#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Exercise helper registration and the callable module API of the microG
# Companion signer patch. Java and the baksmali/smali download are stubbed.
set -eo pipefail
source src/util_functions.sh
set -u

fixture_root="$(mktemp -d)"
trap 'rm -rf "${fixture_root}"' EXIT
WORKDIR="${fixture_root}/work"
helper_root="${fixture_root}/helper"
real_python="$(command -v python3)"

cert="${fixture_root}/cert.pem"
"${real_python}" - "${cert}" <<'PY'
import base64, sys
der = b"\x30\x82\x01\x00" + bytes(range(256)) * 2
body = base64.b64encode(der).decode()
open(sys.argv[1], "w").write(
    "-----BEGIN CERTIFICATE-----\n" + body + "\n-----END CERTIFICATE-----\n"
)
PY

# Intercept the network download only; every other call runs for real.
python3() {
  if [[ "${1:-}" == 'src/microg_spoof_patch.py' && "${2:-}" == 'fetch-tools' ]]; then
    : >"${3}/baksmali-3.0.10-fat.jar"
    : >"${3}/smali-3.0.10-fat.jar"
    return 0
  fi
  "${real_python}" "$@"
}
mkdir -p "${fixture_root}/bin"
printf '#!/bin/sh\nexit 0\n' >"${fixture_root}/bin/java"
chmod +x "${fixture_root}/bin/java"
PATH="${fixture_root}/bin:${PATH}"

ROM_FAMILY=lineageos
ROM_OTA_API_LEVEL=37
MICROG_COMPANION_CERT="${cert}"

ADDITIONALS[MICROG_SPOOF_PATCH]=false
prepare_microg_spoof_patch_module "${helper_root}"
[[ ! -e "${helper_root}" && ! -e "${WORKDIR}" ]]

mkdir -p "${helper_root}/lib/modules"
cat > "${helper_root}/lib/modules/__init__.py" <<'PY'
from dataclasses import dataclass
class LegacyCliModule:
    pass
class MissingArgs(Exception):
    pass
@dataclass
class ModuleRequirements:
    boot_images: set
    ext_images: set
    selinux_patching: bool

def all_modules():
    from lib.modules.registry import legacy_cli_module_types
    return legacy_cli_module_types()
PY
cat > "${helper_root}/lib/modules/registry.py" <<'PY'
from lib.modules import LegacyCliModule

def legacy_cli_module_types():
    result: list[type[LegacyCliModule]] = []
    return tuple(result)
PY
cat > "${helper_root}/lib/filesystem.py" <<'PY'
class CpioFs:
    pass
class ExtFs:
    pass
PY

ADDITIONALS[MICROG_SPOOF_PATCH]=true
ADDITIONALS[MICROG]=true

# The ModOS Companion needs microG itself.
ADDITIONALS[MICROG]=false
if prepare_microg_spoof_patch_module "${helper_root}" 2>/dev/null; then
  echo "Error: the signer patch without microG must be rejected" >&2
  exit 1
fi
ADDITIONALS[MICROG]=true

# Only LineageOS is supported.
ROM_FAMILY=grapheneos
if prepare_microg_spoof_patch_module "${helper_root}" 2>/dev/null; then
  echo "Error: GrapheneOS must be rejected" >&2
  exit 1
fi
ROM_FAMILY=lineageos

# A missing certificate, java or SDK level is refused before anything is installed.
MICROG_COMPANION_CERT="${fixture_root}/missing.pem"
if prepare_microg_spoof_patch_module "${helper_root}" 2>/dev/null; then
  echo "Error: a missing certificate must be rejected" >&2
  exit 1
fi
MICROG_COMPANION_CERT="${cert}"
ROM_OTA_API_LEVEL=''
if prepare_microg_spoof_patch_module "${helper_root}" 2>/dev/null; then
  echo "Error: a missing SDK level must be rejected" >&2
  exit 1
fi
ROM_OTA_API_LEVEL=37
[[ ! -e "${helper_root}/lib/modules/microg_spoof_patch.py" ]]

prepare_microg_spoof_patch_module "${helper_root}"
prepare_microg_spoof_patch_module "${helper_root}"

[[ "${MODOS_MICROG_COMPANION_CERT}" == "${cert}" ]]
[[ "${MODOS_BAKSMALI_JAR}" == "${WORKDIR}/microg-spoof-tools/baksmali-3.0.10-fat.jar" ]]
[[ "${MODOS_SMALI_JAR}" == "${WORKDIR}/microg-spoof-tools/smali-3.0.10-fat.jar" ]]
[[ "${MODOS_MICROG_SPOOF_API_LEVEL}" == 37 ]]

PYTHONPATH="${helper_root}" "${real_python}" - <<'PY'
import argparse
from lib.modules import MissingArgs, all_modules

(module_type,) = all_modules()  # Repeated registration must remain idempotent.
assert module_type.NAME == "microg-spoof-patch"
parser = argparse.ArgumentParser()
module_type.add_args(parser)
try:
    module_type.from_args(parser.parse_args([]))
except MissingArgs:
    pass
else:
    raise AssertionError("disabled signer patch module unexpectedly selected")
module = module_type.from_args(parser.parse_args([
    "--module-microg-spoof-patch", "marker.zip",
    "--module-microg-spoof-patch-sig", "marker.zip.sig",
]))
requirements = module.requirements()
assert requirements.ext_images == {"system"}
assert requirements.boot_images == set()
assert not requirements.selinux_patching
PY

[[ -f "${WORKDIR}/modules/microg-spoof-patch.zip" ]]
[[ -f "${WORKDIR}/signatures/microg-spoof-patch.zip.sig" ]]

# The module is part of the standard enabled-module argument list.
ADDITIONALS[MICROG_SPOOF_PATCH]=true
declare -a module_args=()
apply_rom_module_policy() { :; }
append_enabled_module_arguments module_args
printf '%s\n' "${module_args[@]}" | grep -Fxq -- '--module-microg-spoof-patch'
printf '%s\n' "${module_args[@]}" | grep -Fxq -- '--module-microg-spoof-patch-sig'

# Both local adapters must coexist in the same helper registry. Re-register
# after adding vold to catch a merge that overwrites either complete function.
ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]=true
prepare_lineage_vold_exfat_timeout_module "${helper_root}"
prepare_microg_spoof_patch_module "${helper_root}"
prepare_lineage_vold_exfat_timeout_module "${helper_root}"
PYTHONPATH="${helper_root}" "${real_python}" - <<'PY'
from lib.modules import all_modules

modules = all_modules()
assert len(modules) == 2
assert {module.NAME for module in modules} == {
    "microg-spoof-patch", "lineage-vold-exfat-timeout"
}
for module_type in modules:
    assert module_type().requirements().ext_images == {"system"}
PY
declare -a combined_module_args=()
append_enabled_module_arguments combined_module_args
for module_name in microg-spoof-patch lineage-vold-exfat-timeout; do
  printf '%s\n' "${combined_module_args[@]}" | grep -Fxq -- "--module-${module_name}"
  printf '%s\n' "${combined_module_args[@]}" | grep -Fxq -- "--module-${module_name}-sig"
done

echo "microG Companion signer patch helper registration behavior passed"
