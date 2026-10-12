#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Exercise helper registration and its callable module API, rather than source layout.
set -eo pipefail
source src/util_functions.sh
set -u

fixture_root="$(mktemp -d)"
trap 'rm -rf "${fixture_root}"' EXIT
WORKDIR="${fixture_root}/work"
helper_root="${fixture_root}/helper"

ADDITIONALS[DISABLE_SYSTEM_UPDATER]=false
prepare_disable_system_updater_module "${helper_root}"
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

ADDITIONALS[DISABLE_SYSTEM_UPDATER]=true
for ROM_FAMILY in grapheneos lineageos; do
  prepare_disable_system_updater_module "${helper_root}"
  prepare_disable_system_updater_module "${helper_root}"
  PYTHONPATH="${helper_root}" python3 - <<'PY'
import argparse
import os
from lib.modules import MissingArgs, all_modules

(module_type,) = all_modules()  # Repeated registration must remain idempotent.
assert module_type.NAME == "disable-system-updater"
parser = argparse.ArgumentParser()
module_type.add_args(parser)
try:
    module_type.from_args(parser.parse_args([]))
except MissingArgs:
    pass
else:
    raise AssertionError("disabled updater module unexpectedly selected")
module = module_type.from_args(parser.parse_args([
    "--module-disable-system-updater", "marker.zip",
    "--module-disable-system-updater-sig", "marker.zip.sig",
]))
expected_partition = "system" if os.environ["PIXENEOS_ROM_FAMILY"] == "grapheneos" else "system_ext"
requirements = module.requirements()
assert requirements.ext_images == {expected_partition}
assert requirements.boot_images == set()
assert not requirements.selinux_patching
PY
  [[ -f "${WORKDIR}/modules/disable-system-updater.zip" ]]
  [[ -f "${WORKDIR}/signatures/disable-system-updater.zip.sig" ]]
done

echo "system updater helper registration behavior passed"
