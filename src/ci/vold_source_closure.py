#!/usr/bin/env python3
"""Conservative, auditable source-dependency probing for unmodified pdx235 vold.

A seed is NOT an authoritative Soong closure. Derive candidate projects from
locked source and build diagnostics, then verify exact Git HEADs. Missing
proprietary inputs and unknown module providers are reported, not fabricated.
Never loosen the byte-for-byte comparison or call the timeout patch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "locks/lineage-vold-source-probe-20261016.json"
BASELINE = ROOT / "locks/lineage-vold-baseline-20261016.json"
PATH_IN_LOG = re.compile(
    r"(?<![A-Za-z0-9_.+-])"
    r"((?:android|art|bionic|bootable|build|device|development|external|"
    r"frameworks|hardware|kernel|libcore|packages|prebuilts|system|tools|vendor)"
    r"(?:/[A-Za-z0-9_.+-]+)+)"
)
MISSING_MODULE = re.compile(
    r"(?:undefined module|missing module|module not found|depends on undefined module)"
    r'[: ]+["\x27]?([A-Za-z0-9_.+@-]+)',
    re.I,
)
QUOTED_MODULE = re.compile(
    r'(?:module|dependency)\s+["\x27]([A-Za-z0-9_.+@-]+)["\x27]'
    r'\s+(?:(?:is|was)\s+)?(?:missing(?!\s+dependenc(?:y|ies))|undefined|not found)\b', re.I,
)
MISSING_DEPENDENCIES = re.compile(r"\bmissing dependencies:\s*(.*)$", re.I)
ERROR_MARKERS = ("error:", "not found", "no such file", "missing", "undefined", "cannot find")
BLUEPRINT_BLOCK = re.compile(r"(?m)^\s*([A-Za-z][A-Za-z0-9_]*)\s*\{")
BLUEPRINT_NAME = re.compile(r'\bname\s*:\s*"([^"]+)"')
BP_ARRAY = re.compile(r"\b([a-z_]+)\s*:\s*\[([^\]]*)\]", re.S)
BP_ITEM = re.compile(r'"([^"]+)"')
BP_DEP_FIELDS = {
    "defaults", "static_libs", "shared_libs", "whole_static_libs",
    "header_libs", "export_header_lib_headers", "export_shared_lib_headers",
    "export_static_lib_headers", "required", "vintf_fragment_modules",
    "generated_headers", "generated_sources", "export_generated_headers",
    "tools", "runtime_libs", "host_required", "target_required",
    "rustlibs", "rlibs", "dylibs", "proc_macros",
}
BP_MODULE_REFERENCE_FIELDS = {"srcs", "exclude_srcs", "tool_files", "data"}
BP_SINGLE_MODULE_FIELDS = {"aconfig_declarations"}
BP_STRING = re.compile(r'\b([a-z_]+)\s*:\s*"((?:[^"\\]|\\.)*)"')


class ClosureError(ValueError):
    pass


def verified_contract() -> tuple[dict, dict[str, str], dict]:
    seed = json.loads(SEED.read_text())
    baseline = json.loads(BASELINE.read_text())
    raw = (ROOT / baseline["manifest"]["path"]).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != baseline["manifest"]["sha256"] or seed.get("manifest_sha256") != digest:
        raise ClosureError("source subset plan does not match the immutable 1,243-project manifest")
    if seed.get("base_build_id") != baseline.get("base_build_id") or seed.get("schema_version") != 1:
        raise ClosureError("source subset plan has an unexpected target or schema")
    try:
        xml = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ClosureError("pinned project manifest cannot be parsed") from exc
    projects: dict[str, str] = {}
    for element in xml.findall("project"):
        # Repo defaults omitted project paths to the project name.
        # The official pinned manifest uses that form for 44 projects.
        path, revision = element.get("path", element.get("name")), element.get("revision")
        # One-segment roots (eg. art, bionic) are valid. Any path escaping
        # source root or any branch/tag-based revision is not.
        if (
            not path
            or not re.fullmatch(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", path)
            or any(part in (".", "..") for part in path.split("/"))
            or not revision
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
        ):
            raise ClosureError("manifest contains an invalid project identity")
        if path in projects:
            raise ClosureError("source manifest contains duplicate project paths")
        projects[path] = revision
    if len(projects) != 1243:
        raise ClosureError("pinned source manifest does not contain exactly 1,243 projects")
    groups = seed.get("candidate_groups")
    if not isinstance(groups, dict) or not groups:
        raise ClosureError("source seed has no reviewed dependency groups")
    for title, selected in groups.items():
        if not isinstance(title, str) or not isinstance(selected, list) or not selected:
            raise ClosureError("seed group is empty or malformed")
        if any(not isinstance(path, str) or path not in projects for path in selected):
            raise ClosureError(f"seed group {title} references non-pinned projects")
    if seed.get("direct_module_to_project_hints") is None or any(
        path not in projects for path in seed["direct_module_to_project_hints"].values()
    ):
        raise ClosureError("module provider hints are not bound to immutable manifest projects")
    for vendor in seed["known_unpinned_vendor_inputs"]:
        if not vendor.startswith("vendor/sony/"):
            raise ClosureError("unexpected unreviewed vendor-input exception")
        if any(vendor == p or vendor.startswith(p + "/") for p in projects):
            raise ClosureError("vendor-input exception unexpectedly became manifest-pinned")
    return seed, projects, baseline


def initial_projects(seed: dict) -> list[str]:
    return sorted({path for group in seed["candidate_groups"].values() for path in group})


def validate_selection(selection: dict, projects: dict[str, str], seed: dict) -> list[str]:
    if not isinstance(selection, dict) or selection.get("schema_version") != 1:
        raise ClosureError("invalid dependency probe selection")
    if selection.get("manifest_sha256") != seed["manifest_sha256"]:
        raise ClosureError("dependency selection has the wrong source revision")
    paths = selection.get("selected_projects")
    if not isinstance(paths, list) or not paths or len(paths) > len(projects):
        raise ClosureError("dependency selection contains an invalid project list")
    if any(not isinstance(path, str) or path not in projects for path in paths) or len(paths) != len(set(paths)):
        raise ClosureError("dependency selection contains unknown, unpinned or duplicate projects")
    if not set(initial_projects(seed)).issubset(paths):
        raise ClosureError("dependency selection may not omit reviewed seed projects")
    return sorted(paths)


def selection_record(paths: list[str], seed: dict) -> dict:
    return {
        "schema_version": 1,
        "kind": "unmodified-vold-provisional-source-selection",
        "manifest_sha256": seed["manifest_sha256"],
        "selected_projects": sorted(paths),
        "warning": "Not complete or qualified. Must run the real unmodified build and pass a byte-exact reference check.",
    }


def owner(path: str, projects: dict[str, str]) -> str | None:
    clean = path.strip(".,:;()[]{}'\"")
    # A file in system/fs/fs_mgr belongs to that project, not 'system'.
    matches = [root for root in projects if clean == root or clean.startswith(root + "/")]
    return max(matches, key=len) if matches else None


def missing_dependencies(
    text: str, selected: list[str], projects: dict[str, str], seed: dict
) -> dict:
    pinned_missing: set[str] = set()
    unpinned: set[str] = set()
    unresolved_modules: set[str] = set()
    modules: set[str] = set()
    unmapped_errors: list[str] = []
    hints = seed["direct_module_to_project_hints"]
    for line in text.splitlines():
        low = line.lower()
        if not any(token in low for token in ERROR_MARKERS):
            continue
        line_modules = set()
        for pattern in (MISSING_MODULE, QUOTED_MODULE):
            line_modules.update(pattern.findall(line))
        missing_list = MISSING_DEPENDENCIES.search(line)
        if missing_list:
            line_modules.update(re.findall(r"[A-Za-z0-9_.+@-]+", missing_list[1]))
        modules.update(line_modules)
        actionable_path = False
        for match in PATH_IN_LOG.finditer(line):
            path = match[1]
            project = owner(path, projects)
            if project is not None and project not in selected:
                pinned_missing.add(project)
                actionable_path = True
            elif project is None and path.startswith(("vendor/", "device/")):
                unpinned.add(path)
                actionable_path = True
        if not line_modules and not actionable_path and len(unmapped_errors) < 100:
            unmapped_errors.append(line[:1000])
    for module in modules:
        mapped = hints.get(module)
        if mapped is None:
            unresolved_modules.add(module)
        elif mapped not in selected:
            pinned_missing.add(mapped)
    return {
        "proposed_project_additions": sorted(pinned_missing),
        "unpinned_missing_paths": sorted(unpinned),
        "modules_without_verified_owner": sorted(unresolved_modules),
        "modules_observed": sorted(modules),
        "modules_with_selected_owner": sorted(module for module in modules if hints.get(module) in selected),
        "unmapped_error_lines": unmapped_errors,
        "statically_unpinned_vendor_inputs": seed["known_unpinned_vendor_inputs"],
    }


def without_blueprint_comments(raw: str) -> str:
    """Erase comments without interpreting comment markers inside quoted strings."""
    return re.sub(
        r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/',
        lambda match: match[0] if match[0].startswith('"') else re.sub(r"[^\n]", " ", match[0]),
        raw,
        flags=re.S,
    )


def module_reference(value: str) -> str:
    """Normalize an explicit :module{.output_tag}, not an arbitrary source path."""
    return re.sub(r"\{[^{}]+\}$", "", value.removeprefix(":"))


def iter_blueprint_blocks(raw: str):
    """Extract balanced module declarations, without assuming nested blocks are flat."""
    raw = without_blueprint_comments(raw)
    for candidate in BLUEPRINT_BLOCK.finditer(raw):
        start = candidate.end()
        depth = 1
        quote = False
        escape = False
        index = start
        while index < len(raw) and depth:
            char = raw[index]
            if quote:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    quote = False
            else:
                if char == '"':
                    quote = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
            index += 1
        if depth == 0:
            body = raw[start:index - 1]
            name_match = BLUEPRINT_NAME.search(body)
            if name_match:
                yield name_match[1], body


def blueprint_module_graph(source_root: Path, selected: list[str]) -> dict:
    """Best-effort static Soong graph of synced projects only.

    Does NOT evaluate Soong product conditionals or generated modules.
    """
    if source_root.is_symlink():
        raise ClosureError("unsafe symbolic-link source checkout")
    source_root = source_root.resolve(strict=True)
    found: dict[str, list[dict]] = {}
    count_files = 0
    scanned = set()
    for project in selected:
        directory = source_root / project
        if directory.is_symlink() or not directory.is_dir():
            continue
        for bp in directory.rglob("Android.bp"):
            if bp.is_symlink() or not bp.is_file() or bp.stat().st_size > 4_000_000:
                continue
            if bp in scanned:
                continue
            scanned.add(bp)
            count_files += 1
            if count_files > 12000:
                raise ClosureError("source contains unexpectedly many Blueprint files")
            try:
                raw = bp.read_text(errors="replace")
            except OSError as exc:
                raise ClosureError("unable to read checked-out module metadata") from exc
            for name, body in iter_blueprint_blocks(raw):
                deps = set()
                for field, values in BP_ARRAY.findall(body):
                    if field in BP_DEP_FIELDS:
                        deps.update(module_reference(x) for x in BP_ITEM.findall(values))
                    if field in BP_MODULE_REFERENCE_FIELDS:
                        deps.update(module_reference(x) for x in BP_ITEM.findall(values) if x.startswith(":"))
                for field, value in BP_STRING.findall(body):
                    if field in BP_SINGLE_MODULE_FIELDS:
                        deps.add(module_reference(value))
                found.setdefault(name, []).append({
                    "path": bp.relative_to(source_root).as_posix(),
                    "deps": sorted(deps),
                })
    reachable = set()
    stack = ["vold"]
    unresolved = set()
    ambiguous = set()
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        matches = found.get(current, [])
        if not matches:
            unresolved.add(current)
        if len(matches) > 1:
            ambiguous.add(current)
        for match in matches:
            stack.extend(d for d in match["deps"] if d not in reachable)
    return {
        "kind": "partial-static-soong-graph",
        "scanned_android_bp_files": count_files,
        "indexed_module_names": len(found),
        "reachable_module_names": len(reachable),
        "reachable_module_sample": sorted(reachable)[:160],
        "unresolved_module_names": sorted(unresolved),
        "ambiguous_module_names": sorted(ambiguous),
        "warning": "Partial approximation with omissions and conditional false positives: variables, namespaces, product-selected modules and generated dependencies still need a real Soong evaluation.",
    }


def next_projects_after_index(selected: list[str], report: dict, seed: dict) -> dict | None:
    """Use reviewed provider hints only; unresolved static names are not build failures."""
    hints = seed["direct_module_to_project_hints"]
    additions = sorted({
        hints[module] for module in report["unresolved_module_names"]
        if module in hints and hints[module] not in selected
    })
    report["proposed_project_additions"] = additions
    report["modules_without_verified_owner"] = sorted(
        module for module in report["unresolved_module_names"] if module not in hints
    )
    if len(additions) > 30:
        raise ClosureError("too many static additions: require independent review")
    if not additions:
        return None
    return selection_record(sorted(set(selected + additions)), seed)


def next_projects_after_diagnostic(
    selected: list[str], diagnostic: dict, seed: dict,
) -> dict | None:
    """Stop expensive source iteration when no safe bounded next step exists."""
    if diagnostic["unpinned_missing_paths"] or diagnostic["modules_without_verified_owner"]:
        return None
    additions = diagnostic["proposed_project_additions"]
    if len(additions) > 30:
        raise ClosureError("too many automatic additions: require independent review")
    if not additions:
        return None
    return selection_record(sorted(set(selected + additions)), seed)


def write_json(path: Path, report: dict) -> None:
    if path.is_symlink():
        raise ClosureError("refusing to replace a symbolic-link source report")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    cmd_plan = sub.add_parser("plan", help="generate a small immutable-manifest selection without downloading")
    cmd_plan.add_argument("--output", type=Path, required=True)
    cmd_index = sub.add_parser("index", help="index checked-out Android.bp module references")
    cmd_index.add_argument("--selection", type=Path, required=True)
    cmd_index.add_argument("--source-root", type=Path, required=True)
    cmd_index.add_argument("--output", type=Path, required=True)
    cmd_index.add_argument("--next-selection", type=Path)
    cmd_diag = sub.add_parser("diagnose", help="map build errors to additional pinned paths")
    cmd_diag.add_argument("--selection", type=Path, required=True)
    cmd_diag.add_argument("--log", type=Path, required=True)
    cmd_diag.add_argument("--output", type=Path, required=True)
    cmd_diag.add_argument("--next-selection", type=Path)
    args = parser.parse_args()
    try:
        seed, projects, baseline = verified_contract()
        if args.action == "plan":
            selected = initial_projects(seed)
            write_json(args.output, selection_record(selected, seed))
            print(json.dumps({
                "selected_projects": len(selected),
                "total_manifest_projects": len(projects),
                "source_ota_sha256": baseline["source_ota_sha256"],
                "known_external_vendor_inputs": seed["known_unpinned_vendor_inputs"],
                "selection_path": str(args.output),
            }, sort_keys=True))
        else:
            selected = validate_selection(json.loads(args.selection.read_text()), projects, seed)
            if args.action == "index":
                report = blueprint_module_graph(args.source_root, selected)
                expansion = next_projects_after_index(selected, report, seed)
                if args.next_selection is not None:
                    if expansion is not None:
                        write_json(args.next_selection, expansion)
                    report["next_selection"] = str(args.next_selection) if expansion is not None else None
            else:
                report = missing_dependencies(args.log.read_text(errors="replace"), selected, projects, seed)
                if args.next_selection is not None:
                    expansion = next_projects_after_diagnostic(selected, report, seed)
                    if expansion is not None:
                        write_json(args.next_selection, expansion)
                        report["next_selection"] = str(args.next_selection)
                    else:
                        report["next_selection"] = None
                        report["blocked_on_unresolved_inputs"] = bool(
                            report["unpinned_missing_paths"]
                            or report["modules_without_verified_owner"]
                        )
            write_json(args.output, report)
            print(json.dumps(report, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
