#!/usr/bin/env python3
"""Exercise the helper's dynamic import and module-registration contract."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_SOURCE = REPOSITORY_ROOT / "src" / "fsck_sysfs_sepolicy.py"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fsck-sysfs-helper-import-") as temporary:
        helper_root = Path(temporary)
        modules = helper_root / "lib" / "modules"
        modules.mkdir(parents=True)
        (helper_root / "lib" / "__init__.py").write_text("")
        (helper_root / "lib" / "modules" / "__init__.py").write_text(
            "from dataclasses import dataclass\n"
            "class LegacyCliModule: pass\n"
            "class MissingArgs(Exception): pass\n"
            "@dataclass\n"
            "class ModuleRequirements:\n"
            "    boot_images: set[str]\n"
            "    ext_images: set[str]\n"
            "    selinux_patching: bool\n"
        )
        (helper_root / "lib" / "filesystem.py").write_text(
            "class CpioFs: pass\nclass ExtFs: pass\n"
        )
        (helper_root / "lib" / "linux.py").write_text(
            "def linux_run(*args, **kwargs): return None\n"
        )
        (modules / "fsck_sysfs_sepolicy.py").write_bytes(ADAPTER_SOURCE.read_bytes())
        (modules / "registry.py").write_text(
            "def legacy_cli_module_types():\n"
            "    from lib.modules.fsck_sysfs_sepolicy import FsckSysfsSePolicyMod\n"
            "    return (FsckSysfsSePolicyMod,)\n"
        )

        helper_contract = """
import os
from lib.modules import LegacyCliModule
from lib.modules.registry import legacy_cli_module_types
os.environ['PIXENEOS_ROM_FAMILY'] = 'lineageos'
os.environ['PIXENEOS_DEVICE_NAME'] = 'pdx235'
modules = legacy_cli_module_types()
assert len(modules) == 1
module = modules[0]
assert module.__name__ == 'FsckSysfsSePolicyMod'
assert issubclass(module, LegacyCliModule)
assert module.NAME == 'fsck-sysfs-sepolicy'
requirements = module().requirements()
assert requirements.selinux_patching is True
assert requirements.boot_images == set()
assert requirements.ext_images == set()
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(helper_root)
        result = subprocess.run(
            [sys.executable, "-c", helper_contract],
            cwd=helper_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                "helper-style module import/registration failed:\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

    print("fsck sysfs SELinux helper import and registration contract passed")


if __name__ == "__main__":
    main()
