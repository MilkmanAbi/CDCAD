"""Plan and build recipe exports, run recipe checks, and point out unused parts of a recipe."""
import hashlib
from pathlib import Path

from ._common import CADError, EXPORT_FORMATS, UNITS, VERSION, atomic_bytes, document_path
from .engine import Engine
from .graph import dependencies
from .mesh import export_bytes, mesh_report


def recipe_source_paths(recipe, base):
    return {(Path(base)/document_path(node["path"])).resolve()
            for kind in ("profiles", "solids")
            for node in recipe.get(kind, {}).values() if "path" in node}


def reachable(recipe):
    """(kind, name) of every node an export or a check depends on."""
    seen = set()
    stack = [("solids", e["solid"]) for e in recipe.get("exports", [])]
    stack += [("solids", c["solid"]) for c in recipe.get("checks", [])]
    while stack:
        key = stack.pop()
        if key in seen or key[1] not in recipe.get(key[0], {}):
            continue
        seen.add(key)
        stack.extend(dependencies(key[0], recipe[key[0]][key[1]]))
    return seen


def recipe_warnings(recipe, info=None):
    """Things that are valid but probably not intended."""
    warnings = []
    if info is not None:
        unused = [name for name in recipe.get("params", {}) if name not in info["used_parameters"]]
        if unused:
            warnings.append(f"Parameters not used by any node: {', '.join(unused)}")
    used = reachable(recipe)
    for kind in ("profiles", "solids"):
        idle = [name for name in recipe.get(kind, {}) if (kind, name) not in used]
        if idle:
            warnings.append(f"{kind.capitalize()} not used by any export or check: {', '.join(idle)}")
    if not recipe.get("exports"):
        warnings.append("The recipe has no exports, so build writes nothing")
    return warnings


CHECK_PROPERTIES = ("volume", "area", "mass", "components", "size_x", "size_y", "size_z")


def _measure(mesh, prop, unit, density):
    if prop == "volume":
        return float(mesh.volume)
    if prop == "area":
        return float(mesh.area)
    if prop == "components":
        return len(mesh.split(only_watertight=False))
    if prop == "mass":
        if density is None:
            raise CADError("A mass check needs the recipe's density (g/cm^3)")
        return float(mesh.volume)*UNITS[unit]**3*density/1000.0
    return float(mesh.extents["xyz".index(prop[-1])])


def _compare(value, rule):
    """'' when value satisfies rule, else a description of what was expected."""
    problems = []
    if "min" in rule and value < rule["min"]:
        problems.append(f">= {rule['min']:g}")
    if "max" in rule and value > rule["max"]:
        problems.append(f"<= {rule['max']:g}")
    if "approx" in rule:
        target = rule["approx"]
        tolerance = max(rule.get("abs", 0.0), rule.get("rel", 0.0 if "abs" in rule else 1e-3)*abs(target))
        if abs(value - target) > tolerance:
            problems.append(f"{target:g} +/- {tolerance:g}")
    return " and ".join(problems)


def run_checks(recipe, engine, solids=None):
    """Evaluate the recipe's checks (all, or only those on ``solids``). Raises CADError
    listing every failure; returns the results when all pass."""
    unit, density = recipe.get("units", "mm"), recipe.get("density")
    results, failures = [], []
    for check in recipe.get("checks", []):
        name = check["solid"]
        if solids is not None and name not in solids:
            continue
        if name not in recipe["solids"]:
            from ._common import suggest
            raise CADError(f"Check refers to unknown solid {name!r}", hint=suggest(name, recipe["solids"]))
        mesh = engine.get("solids", name)
        for prop in CHECK_PROPERTIES:
            if prop in check:
                value = _measure(mesh, prop, unit, density)
                expected = _compare(value, check[prop])
                results.append({"solid": name, "property": prop, "value": value, "ok": not expected})
                if expected:
                    failures.append(f"{name} {prop} is {value:.6g}, expected {expected}")
        if "is_volume" in check:
            ok = bool(mesh.is_volume) == check["is_volume"]
            results.append({"solid": name, "property": "is_volume", "value": bool(mesh.is_volume), "ok": ok})
            if not ok:
                failures.append(f"{name} is_volume is {bool(mesh.is_volume)}")
        if "fits" in check:
            extents = sorted(e*UNITS[unit] for e in mesh.extents)
            ok = all(e <= b + 1e-4 for e, b in zip(extents, sorted(check["fits"])))
            results.append({"solid": name, "property": "fits", "value": [e*UNITS[unit] for e in mesh.extents], "ok": ok})
            if not ok:
                failures.append(f"{name} ({', '.join(f'{e*UNITS[unit]:.4g}' for e in mesh.extents)} mm) does not fit "
                                f"{check['fits']} mm in any axis-aligned orientation")
    if failures:
        raise CADError("Check failed: " + "; ".join(failures),
                       hint="nothing was written; fix the model or the check (sizes, volumes and areas are in recipe units)")
    return results


def build(recipe, base, out_dir=None, targets=None, force=False, dry_run=False, cache_dir=None, *, protected_paths=(),
          warnings=None):
    engine = Engine(recipe, base, cache_dir=cache_dir)
    exports = [x for x in recipe["exports"] if not targets or x["solid"] in targets]
    if targets and set(targets)-{x["solid"] for x in exports}:
        raise CADError("--target must name an exported solid",
                       hint=f"exported solids: {', '.join(sorted({x['solid'] for x in recipe['exports']}))}")
    sources = recipe_source_paths(recipe, base) | {Path(p).resolve() for p in protected_paths}
    paths = []
    for exp in exports:
        path = (Path(out_dir or base)/document_path(exp["path"])).resolve()
        if out_dir and not path.is_relative_to(Path(out_dir).resolve()):
            raise CADError("Export path escapes --out-dir")
        if path in sources:
            raise CADError(f"Export would overwrite a source file: {path}")
        if path in paths:
            raise CADError("Multiple exports resolve to the same path")
        if path.exists() and not force and not dry_run:
            raise CADError(f"Output exists: {path}; use --force to replace")
        if path.suffix.lower()[1:] not in EXPORT_FORMATS:
            raise CADError(f"Unsupported export extension: {path.suffix}",
                           hint="use " + ", ".join("." + f for f in EXPORT_FORMATS))
        paths.append(path)
    extra = {"warnings": warnings} if warnings is not None else {}
    if dry_run:
        return {"ok": True, "dry_run": True, "units": engine.unit,
                "exports": [{"solid": e["solid"], "path": str(p)} for e, p in zip(exports, paths)], **extra,
                "note": "Schema and references checked; geometry, source files and checks have not been evaluated."}
    density = recipe.get("density")
    pending, reports = [], []
    for exp, path in zip(exports, paths):
        mesh = engine.get("solids", exp["solid"]).copy()
        mesh.apply_scale(UNITS[engine.unit])
        data = export_bytes(mesh, path, exp.get("allow_open", False))
        pending.append((path, data))
        reports.append({"solid": exp["solid"], "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                        **mesh_report(mesh, density)})
    checks = run_checks(recipe, engine, set(targets) if targets else None)
    # Evaluate and validate every selected export, and pass every check, before writing any model.
    for path, data in pending:
        atomic_bytes(path, data)
    result = {"ok": True, "version": VERSION, "units": "mm", "exports": reports}
    if recipe.get("checks"):
        result["checks"] = checks
    return {**result, **extra, "trace": engine.trace}
