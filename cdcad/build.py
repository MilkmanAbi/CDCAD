"""Plan and build recipe exports."""
import hashlib
from pathlib import Path

from ._common import CADError, EXPORT_FORMATS, UNITS, VERSION, atomic_bytes, document_path
from .engine import Engine
from .mesh import export_bytes, mesh_report


def recipe_source_paths(recipe, base):
    return {(Path(base)/document_path(node["path"])).resolve()
            for kind in ("profiles", "solids")
            for node in recipe.get(kind, {}).values() if "path" in node}


def build(recipe, base, out_dir=None, targets=None, force=False, dry_run=False, cache_dir=None, *, protected_paths=()):
    engine = Engine(recipe, base, cache_dir=cache_dir)
    exports = [x for x in recipe["exports"] if not targets or x["solid"] in targets]
    if targets and set(targets)-{x["solid"] for x in exports}:
        raise CADError("--target must name an exported solid")
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
            raise CADError(f"Unsupported export extension: {path.suffix}")
        paths.append(path)
    if dry_run:
        return {"ok": True, "dry_run": True, "units": engine.unit,
                "exports": [{"solid": e["solid"], "path": str(p)} for e, p in zip(exports, paths)],
                "note": "Schema and references checked; geometry and source files have not been evaluated."}
    pending, reports = [], []
    for exp, path in zip(exports, paths):
        mesh = engine.get("solids", exp["solid"]).copy()
        mesh.apply_scale(UNITS[engine.unit])
        data = export_bytes(mesh, path, exp.get("allow_open", False))
        pending.append((path, data))
        reports.append({"solid": exp["solid"], "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                        **mesh_report(mesh)})
    # Evaluate and validate every selected export before writing any model.
    for path, data in pending:
        atomic_bytes(path, data)
    return {"ok": True, "version": VERSION, "units": "mm", "exports": reports, "trace": engine.trace}
