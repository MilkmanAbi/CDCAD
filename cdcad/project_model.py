"""Project manifests and compilation to standalone recipes."""
import copy
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from ._common import CADError, UNITS, document_path
from .graph import validate_graph
from .recipe import expression, resolve_parameters, resolve_recipe
from .schema import ANCHOR, BOOL, NUM, STR, array, validate_resolved, vector


NAME = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]*$"}
RAW_NUMBER = {"oneOf": [NUM, {"type": "string", "pattern": "^="}]}
RAW_V3 = vector(3, RAW_NUMBER)
PARAMS = {"type": "object", "additionalProperties": RAW_NUMBER}


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def project_schema():
    placement = obj({"translate": RAW_V3, "rotate": RAW_V3, "scale": RAW_V3})
    attach = obj({"target": NAME, "anchor": ANCHOR, "target_anchor": ANCHOR, "offset": RAW_V3}, ["target"])
    segment = obj({"path": STR, "solid": STR, "layer": NAME, "enabled": BOOL,
                   "params": PARAMS, "placement": placement, "attach": attach}, ["path", "solid", "layer"])
    assembly = obj({"members": array(NAME), "layers": array(NAME),
                    "mode": {"enum": ["union", "combine"]}, "enabled": BOOL})
    assembly["anyOf"] = [{"required": ["members"]}, {"required": ["layers"]}]
    result = obj({"project_version": {"const": 1}, "name": STR,
                  "units": {"enum": list(UNITS)}, "params": PARAMS,
                  "layers": {"type": "object", "propertyNames": NAME,
                             "additionalProperties": obj({"enabled": BOOL})},
                  "segments": {"type": "object", "propertyNames": NAME, "additionalProperties": segment},
                  "assemblies": {"type": "object", "propertyNames": NAME, "additionalProperties": assembly}},
                 ["project_version", "name", "units", "layers", "segments", "assemblies"])
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "CDCAD project v1", **result}


def safe_name(value):
    if not re.fullmatch(NAME["pattern"], value):
        raise CADError(f"Invalid name {value!r}; use a letter followed by letters, digits, _ or -")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value, re.IGNORECASE):
        raise CADError(f"Project name is reserved on Windows: {value!r}")
    return value


def inside(root, relative):
    path = (root/document_path(relative)).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise CADError(f"Path escapes project: {relative}")
    return path


def segment_file(root, relative):
    path = inside(root, relative)
    if not path.is_relative_to((root/"segments").resolve()):
        raise CADError("Segment file must remain inside the segments directory")
    return path


def check_manifest(manifest):
    error = next(Draft202012Validator(project_schema()).iter_errors(manifest), None)
    if error:
        raise CADError(f"Project schema at {'/'.join(map(str, error.absolute_path))}: {error.message}")
    names = set(manifest["segments"])
    if names & set(manifest["assemblies"]):
        raise CADError("Segment and assembly names must be unique across both namespaces")
    all_names = list(manifest["segments"])+list(manifest["assemblies"])
    if len({n.casefold() for n in all_names}) != len(all_names):
        raise CADError("Part names must also be unique ignoring case for portable projects")
    if len({n.casefold() for n in manifest["layers"]}) != len(manifest["layers"]):
        raise CADError("Layer names must be unique ignoring case")
    for name in (*all_names, *manifest["layers"]):
        safe_name(name)
    paths = set()
    for name, entry in manifest["segments"].items():
        if entry["layer"] not in manifest["layers"]:
            raise CADError(f"Unknown layer for {name}: {entry['layer']}")
        rel = document_path(entry["path"])
        if rel.is_absolute() or not rel.parts or rel.parts[0] != "segments" or rel.suffix.lower() != ".json":
            raise CADError("Segment documents must be .json files below segments/")
        key = rel.as_posix().lower()
        if key in paths:
            raise CADError("Two segments cannot share one source file; use duplicate")
        paths.add(key)


def resolve_values(value, params):
    if isinstance(value, str) and value.startswith("="):
        return expression(value[1:], params)
    if isinstance(value, list):
        return [resolve_values(v, params) for v in value]
    if isinstance(value, dict):
        return {k: resolve_values(v, params) for k, v in value.items()}
    return value


def compile_project(root, state, overrides=None):
    """Merge independent segment recipes into a namespaced graph."""
    root = Path(root).resolve()
    m, documents = state["project"], state["segments"]
    check_manifest(m)
    if set(documents) != set(m["segments"]):
        raise CADError("Segment document names must match project segment entries")
    overrides = overrides or {}
    global_params = m.get("params", {})
    known_params = set(global_params)
    for name, entry in m["segments"].items():
        raw = documents[name]
        if not isinstance(raw, dict) or not isinstance(raw.get("params", {}), dict):
            raise CADError(f"Segment {name} must contain a recipe object with an optional params object")
        known_params.update(raw.get("params", {}))
        known_params.update(entry.get("params", {}))
    unknown = overrides.keys() - known_params
    if unknown:
        raise CADError(f"Unknown parameter override: {', '.join(sorted(unknown))}")
    globals_resolved = resolve_parameters(global_params, {
        name: value for name, value in overrides.items() if name in global_params
    })
    all_names = set(m["segments"]) | set(m["assemblies"])
    active_segments = {name for name, e in m["segments"].items()
                       if e.get("enabled", True) and m["layers"][e["layer"]].get("enabled", True)}
    assembly_members, active_memo, visiting = {}, {}, set()

    def active(name):
        if name not in all_names:
            raise CADError(f"Unknown assembly/attachment member: {name}")
        if name in m["segments"]:
            return name in active_segments
        if name in active_memo:
            return active_memo[name]
        if name in visiting:
            raise CADError(f"Cyclic assembly reference: {name}")
        visiting.add(name)
        entry = m["assemblies"][name]
        refs = list(entry.get("members", []))
        for layer in entry.get("layers", []):
            if layer not in m["layers"]:
                raise CADError(f"Unknown assembly layer: {layer}")
            refs.extend(n for n, e in m["segments"].items() if e["layer"] == layer)
        if len(refs) != len(set(refs)):
            raise CADError(f"Assembly {name} contains duplicate members (including layer selections)")
        enabled_refs = [ref for ref in refs if active(ref)]
        assembly_members[name] = enabled_refs
        value = bool(enabled_refs) and entry.get("enabled", True)
        active_memo[name] = value
        visiting.remove(name)
        return value

    for name in m["assemblies"]:
        active(name)
    result = {"version": 1, "units": m["units"], "profiles": {}, "solids": {}, "exports": []}
    selection = {}
    document_paths = set()
    ref_name = lambda name: f"@{name}"
    for name, entry in m["segments"].items():
        source_key = str(segment_file(root, entry["path"])).casefold()
        if source_key in document_paths:
            raise CADError("Segment paths resolve to the same file")
        document_paths.add(source_key)
        raw = copy.deepcopy(documents[name])
        if not isinstance(raw, dict):
            raise CADError(f"Segment {name} must contain a recipe object")
        if raw.get("units", m["units"]) != m["units"]:
            raise CADError(f"Segment {name} units differ from project units")
        raw["units"] = m["units"]
        # Segment defaults < project settings < per-segment settings < CLI overrides.
        raw["params"] = {**raw.get("params", {}), **m.get("params", {}),
                         **entry.get("params", {})}
        raw["params"].update({key: value for key, value in overrides.items() if key in raw["params"]})
        raw["exports"] = [{"solid": entry["solid"], "path": "unused.stl"}]
        resolved = resolve_recipe(raw)
        validate_graph(resolved)
        if name not in active_segments:
            continue
        prefix = f"{name}::"
        for kind in ("profiles", "solids"):
            for local, source in resolved.get(kind, {}).items():
                if not re.fullmatch("[A-Za-z_][A-Za-z0-9_-]*", local):
                    raise CADError(f"Segment-local node name uses reserved punctuation: {local}")
                node = copy.deepcopy(source)
                for field in ("input", "profile", "target"):
                    if field in node:
                        node[field] = prefix+node[field]
                if "inputs" in node:
                    node["inputs"] = [prefix+s for s in node["inputs"]]
                for section in node.get("sections", []):
                    section["profile"] = prefix+section["profile"]
                if "path" in node:
                    node["path"] = str((inside(root, entry["path"]).parent/document_path(node["path"])).resolve())
                result[kind][prefix+local] = node
        placed = prefix+entry["solid"]
        placement = resolve_values(entry.get("placement", {}), resolved["params"])
        if placement:
            result["solids"][f"{prefix}$placed"] = {"op": "transform", "input": placed, **placement}
            placed = f"{prefix}$placed"
        final = {"op": "transform", "input": placed}
        if "attach" in entry:
            a = resolve_values(entry["attach"], resolved["params"])
            if not active(a["target"]):
                raise CADError(f"Attachment target is disabled: {a['target']}")
            final = {"op": "align", "input": placed, "target": ref_name(a["target"]),
                     **{k: v for k, v in a.items() if k != "target"}}
        result["solids"][ref_name(name)] = final
        path = f"segments/{entry['layer']}/{name}.stl"
        result["exports"].append({"solid": ref_name(name), "path": path})
        selection[name] = {"kind": "segment", "layer": entry["layer"], "solid": ref_name(name), "path": path}
    for name, entry in m["assemblies"].items():
        if not active(name):
            continue
        result["solids"][ref_name(name)] = {"op": entry.get("mode", "union"),
                                         "inputs": [ref_name(n) for n in assembly_members[name]]}
        path = f"assemblies/{name}.stl"
        result["exports"].append({"solid": ref_name(name), "path": path})
        selection[name] = {"kind": "assembly", "solid": ref_name(name), "path": path}
    if result["exports"]:
        validate_resolved(result)
    validate_graph(result)
    return result, selection, globals_resolved
