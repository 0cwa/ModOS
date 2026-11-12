import importlib.util
import json
import pathlib
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("selection", root / "tools/modules/check_selection.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
index = json.loads((root / "tools/modules/selection.json").read_text())

with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "index.json"
    bad = json.loads(json.dumps(index))
    bad["modules"].append(dict(bad["modules"][0]))
    path.write_text(json.dumps(bad))
    try:
        checker.check(root, path)
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate module omission/miswire was accepted")
    bad = json.loads(json.dumps(index))
    bad["modules"][0]["config"] = "unknown_toggle"
    path.write_text(json.dumps(bad))
    try:
        checker.check(root, path)
    except ValueError as exc:
        assert "typed config" in str(exc)
    else:
        raise AssertionError("unknown config mapping was accepted")
checker.check(root, root / "tools/modules/selection.json")
print("module selection contract fixtures: 2 mutations rejected")
