#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

set -euo pipefail

python3 - <<'PY'
import ast
from pathlib import Path

root = Path.cwd()
source = root / "src" / "disable_system_updater.py"
assert source.is_file()

tree = ast.parse(source.read_text())
module_class = next(
    node
    for node in ast.walk(tree)
    if isinstance(node, ast.ClassDef) and node.name == "DisableSystemUpdaterMod"
)
assert [ast.unparse(base) for base in module_class.bases] == ["LegacyCliModule"]
methods = {
    node.name for node in module_class.body if isinstance(node, ast.FunctionDef)
}
assert {"add_args", "from_args", "requirements", "inject"} <= methods

text = source.read_text()
assert "/system/priv-app/Updater/Updater.apk" in text
assert "/system_ext/priv-app/Updater/Updater.apk" in text
assert "app.seamlessupdate.client" in text
assert "org.lineageos.updater" in text

util = (root / "src/util_functions.sh").read_text()
start = util.index("function prepare_disable_system_updater_module()")
end = util.index("\n# Resolve and acquire", start)
registration = util[start:end]
assert "return 0" in registration
assert "DisableSystemUpdaterMod" in registration
assert "disable_system_updater.py" in registration
assert 'export PIXENEOS_ROM_FAMILY="${ROM_FAMILY}"' in registration

append_start = util.index("function append_enabled_module_arguments()")
append_end = util.index("\n# Validate and register", append_start)
append = util[append_start:append_end]
assert '"disable-system-updater:DISABLE_SYSTEM_UPDATER"' in append

print("system updater helper registration contract passed")
PY
