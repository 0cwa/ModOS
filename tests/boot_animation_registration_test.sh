#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

set -euo pipefail

python3 - <<'PY'
import ast
import hashlib
import json
from pathlib import Path

root = Path.cwd()
assert (root / "src" / "boot_animation.py").is_file()
module_tree = ast.parse((root / "src/boot_animation.py").read_text())
module_class = next(
    node
    for node in ast.walk(module_tree)
    if isinstance(node, ast.ClassDef) and node.name == "BootAnimationMod"
)
assert [ast.unparse(base) for base in module_class.bases] == ["LegacyCliModule"]
method_names = {
    node.name for node in module_class.body if isinstance(node, ast.FunctionDef)
}
assert {"add_args", "from_args", "requirements", "inject"} <= method_names
inject = next(
    node
    for node in module_class.body
    if isinstance(node, ast.FunctionDef) and node.name == "inject"
)
assert [arg.arg for arg in inject.args.args] == [
    "self", "boot_fs", "ext_fs", "sepolicies", "compatible_sepolicy"
]
assert len(inject.args.defaults) == 1
assert ast.unparse(inject.args.defaults[0]) == "False"

util = (root / "src/util_functions.sh").read_text()
start = util.index("function prepare_boot_animation_module()")
end = util.index("\n# Resolve and acquire", start)
registration = util[start:end]
assert "return 0" in registration
assert "registry.py" not in registration
assert "legacy_cli_module_types" not in registration
extension = json.loads((root / "tools/modules/adapter_extension.json").read_text())
entry = next(item for item in extension["adapters"] if item["id"] == "boot-animation")
assert entry["kind"] == "legacy-cli"
assert entry["target"] == "_module_class"
assert entry["source_path"] == "src/boot_animation.py"
assert entry["source"] == "adapters/boot_animation.py"
assert entry["sha256"] == hashlib.sha256((root / entry["source_path"]).read_bytes()).hexdigest()
staging = util[util.index("function prepare_adapter_extension()"):
               util.index("function prepare_boot_animation_module()")]
patch = util[util.index("function patch_ota()"):
             util.index("function run_patch_helper_with_diagnostics()")]
assert "tools/modules/build_adapter_extension.py" in staging
assert 'args+=(--adapter-extension "${ADAPTER_EXTENSION_PATH}")' in patch
print("boot animation helper registration contract passed")
PY
