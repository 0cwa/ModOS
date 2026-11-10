#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Exercise helper registration and the callable module API of the vold exFAT
# timeout patch. No Java, network or artifact is involved.
set -eo pipefail
source src/util_functions.sh
set -u

fixture_root="$(mktemp -d)"
trap 'rm -rf "${fixture_root}"' EXIT
WORKDIR="${fixture_root}/work"
helper_root="${fixture_root}/helper"
real_python="$(command -v python3)"
ROM_FAMILY=lineageos

ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]=false
prepare_lineage_vold_exfat_timeout_module "${helper_root}"
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

ADDITIONALS[LINEAGE_VOLD_EXFAT_TIMEOUT]=true

# Only LineageOS is supported, and nothing is installed when it is refused.
ROM_FAMILY=grapheneos
if prepare_lineage_vold_exfat_timeout_module "${helper_root}" 2>/dev/null; then
  echo "Error: GrapheneOS must be rejected" >&2
  exit 1
fi
ROM_FAMILY=lineageos
[[ ! -e "${helper_root}/lib/modules/lineage_vold.py" ]]

# A symlinked helper module path is refused.
ln -s /nonexistent "${helper_root}/lib/modules/lineage_vold.py"
if prepare_lineage_vold_exfat_timeout_module "${helper_root}" 2>/dev/null; then
  echo "Error: a symlinked helper module path must be rejected" >&2
  exit 1
fi
rm -- "${helper_root}/lib/modules/lineage_vold.py"

prepare_lineage_vold_exfat_timeout_module "${helper_root}"
prepare_lineage_vold_exfat_timeout_module "${helper_root}"  # idempotent
cmp -s src/lineage_vold.py "${helper_root}/lib/modules/lineage_vold.py"
[[ "${PIXENEOS_ROM_FAMILY}" == lineageos ]]

PYTHONPATH="${helper_root}" "${real_python}" - <<'PY'
import argparse
from lib.modules import MissingArgs, all_modules

(module_type,) = all_modules()  # Repeated registration must remain idempotent.
assert module_type.NAME == "lineage-vold-exfat-timeout"
parser = argparse.ArgumentParser()
module_type.add_args(parser)
try:
    module_type.from_args(parser.parse_args([]))
except MissingArgs:
    pass
else:
    raise AssertionError("disabled vold patch module unexpectedly selected")
module = module_type.from_args(parser.parse_args([
    "--module-lineage-vold-exfat-timeout", "marker.zip",
    "--module-lineage-vold-exfat-timeout-sig", "marker.zip.sig",
]))
requirements = module.requirements()
assert requirements.ext_images == {"system"}
assert requirements.boot_images == set()
assert not requirements.selinux_patching
PY

# inject() refuses a ROM family other than LineageOS before touching anything.
PIXENEOS_ROM_FAMILY=grapheneos PYTHONPATH="${helper_root}" "${real_python}" - <<'PY'
from lib.modules import all_modules
(module_type,) = all_modules()
try:
    module_type().inject({}, {"system": object()}, [])
except Exception as exc:
    assert "only for LineageOS" in str(exc), exc
else:
    raise AssertionError("GrapheneOS injection unexpectedly allowed")
PY

[[ -f "${WORKDIR}/modules/lineage-vold-exfat-timeout.zip" ]]
[[ -f "${WORKDIR}/signatures/lineage-vold-exfat-timeout.zip.sig" ]]

# The module is part of the standard enabled-module argument list.
declare -a module_args=()
apply_rom_module_policy() { :; }
append_enabled_module_arguments module_args
printf '%s\n' "${module_args[@]}" | grep -Fxq -- '--module-lineage-vold-exfat-timeout'
printf '%s\n' "${module_args[@]}" | grep -Fxq -- '--module-lineage-vold-exfat-timeout-sig'

echo "vold exFAT timeout helper registration behavior passed"
