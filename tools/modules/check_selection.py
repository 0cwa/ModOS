#!/usr/bin/env python3
"""Check named ModOS selection consumers against the local policy index."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def check(root, index_path):
    data = json.loads(index_path.read_text())
    modules = data.get("modules", [])
    keys = [m.get("key") for m in modules]
    if len(keys) != len(set(keys)) or not keys:
        raise ValueError("module keys must be unique and nonempty")
    schema = (root / "src/config_schema.sh").read_text()
    workflow = (root / ".github/workflows/build-rom.yml").read_text()
    selection = (root / "src/rom_profiles.sh").read_text()
    health = (root / "src/device/modos_health/checks").resolve()
    for m in modules:
        key = m["key"]
        if m.get("route") not in {"helper-catalog", "locked-bundle", "local-adapter", "signed-zip"}:
            raise ValueError(f"{key}: unknown route")
        if f"_config_schema_define {m['config']} " not in schema:
            raise ValueError(f"{key}: missing typed config mapping")
        toggle = key.replace("_", "-")
        if f"{toggle}:" not in workflow:
            raise ValueError(f"{key}: missing workflow input")
        if m.get("health") and not (health / f"{m['health']}.py").is_file():
            raise ValueError(f"{key}: missing health check {m['health']}")
        field = m.get("selection_field", key.upper())
        if f"SELECTION_MODULE_{field}" not in selection and f"SELECTION_{field}" not in selection:
            raise ValueError(f"{key}: missing selection fingerprint field")
        if m.get("route") in {"helper-catalog", "locked-bundle"} and not m.get("helper_id"):
            raise ValueError(f"{key}: helper route needs catalog id")
        if m.get("route") == "locked-bundle" and not m.get("helper_id"):
            raise ValueError(f"{key}: locked bundle needs helper id")
    for m in modules:
        if m.get("exclusive") and m["exclusive"] not in keys:
            raise ValueError(f"{m['key']}: unknown exclusive module {m['exclusive']}")
    if "function module_selection_fingerprint" not in selection:
        raise ValueError("selection fingerprint implementation is missing")


if __name__ == "__main__":
    try:
        check(ROOT, pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tools/modules/selection.json")
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"module selection contract: {exc}", file=sys.stderr)
        sys.exit(1)
    print("module selection contract: ok")
