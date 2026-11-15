"""Incremental project output planning and export."""
import hashlib
from pathlib import Path
import time

from ._common import CADError, EXPORT_FORMATS, UNITS, VERSION, atomic_bytes, json_bytes, read_json
from .project_model import compile_project
from .project_store import METADATA_DIR, digest, load_state, project_lock, revision


def project_build(root, *, segments=(), assemblies=(), layers=(), overrides=None,
                  out_dir=None, format="stl", rebuild=False, plan=False):
    """Plan or build selected project outputs using the workspace cache."""
    from .build import recipe_source_paths
    from .engine import Engine
    from .mesh import export_bytes, mesh_report

    if format not in EXPORT_FORMATS:
        raise CADError(f"Unsupported project export format: {format}")
    root = Path(root).resolve()
    started = time.perf_counter()
    with project_lock(root):
        state = load_state(root)
        recipe, selection, params = compile_project(root, state, overrides)
        requested = set(segments) | set(assemblies)
        for name in segments:
            if name not in selection or selection[name]["kind"] != "segment":
                raise CADError(f"No enabled segment: {name}")
        for name in assemblies:
            if name not in selection or selection[name]["kind"] != "assembly":
                raise CADError(f"No enabled assembly: {name}")
        for layer in layers:
            if layer not in state["project"]["layers"]:
                raise CADError(f"Unknown layer: {layer}")
            requested.update(n for n, e in selection.items() if e.get("layer") == layer)
        if segments or assemblies or layers:
            selection = {n: e for n, e in selection.items() if n in requested}
        if not selection:
            raise CADError("No enabled outputs selected; add segments or enable the relevant layers")
        output_root = Path(out_dir).resolve() if out_dir else (root/"build").resolve()
        # Managed build output must never overwrite source or workspace metadata.
        protected = [(root/folder).resolve() for folder in ("segments", "assets", METADATA_DIR)]
        sources = recipe_source_paths(recipe, root)
        if output_root == root or any(output_root.is_relative_to(p) for p in protected):
            raise CADError("Use build/ or a separate output directory, not project source/metadata folders")
        engine = Engine(recipe, root, cache_dir=root/METADATA_DIR/"cache")
        state_path = root/METADATA_DIR/"build-state.json"
        warnings = []
        previous = {"outputs": {}}
        if state_path.exists():
            try:
                previous = read_json(state_path)
                if not isinstance(previous, dict) or not isinstance(previous.get("outputs", {}), dict):
                    raise CADError("unexpected structure")
            except CADError:
                # Build state only lets unchanged outputs be skipped; losing it means a full rebuild.
                previous = {"outputs": {}}
                warnings.append(f"{METADATA_DIR}/build-state.json was unreadable; rebuilding every selected output")
        next_outputs = dict(previous.get("outputs", {}))
        jobs, outputs, pending = [], [], []
        for name, entry in selection.items():
            relative = str(Path(entry["path"]).with_suffix("."+format))
            path = (output_root/relative).resolve()
            if not path.is_relative_to(output_root):
                raise CADError("Output path escapes build directory")
            if path in sources or any(path.is_relative_to(folder) for folder in protected):
                raise CADError(f"Export would overwrite or enter project source/metadata: {path}")
            key = digest({"node": engine.node_key("solids", entry["solid"]), "format": format,
                          "mm_factor": UNITS[recipe["units"]]})
            old = previous.get("outputs", {}).get(str(path))
            status = "build"
            if not rebuild and isinstance(old, dict) and old.get("key") == key and isinstance(old.get("sha256"), str) and path.is_file():
                with path.open("rb") as f:
                    if hashlib.file_digest(f, "sha256").hexdigest() == old["sha256"]:
                        status = "unchanged"
            jobs.append({"name": name, **entry, "path": str(path), "key": key, "status": status})
        if plan:
            return {"ok": True, "plan": True, "revision": revision(state), "units": "mm", "outputs": jobs,
                    "warnings": warnings,
                    "note": "Geometry not evaluated. Source-file bytes and output hashes checked."}
        for job in jobs:
            path = Path(job["path"])
            if job["status"] == "unchanged":
                outputs.append({**previous["outputs"][str(path)], "status": "unchanged"})
                continue
            mesh = engine.get("solids", job["solid"]).copy()
            mesh.apply_scale(UNITS[recipe["units"]])
            data = export_bytes(mesh, path)
            report = {**job, "status": "built", "sha256": hashlib.sha256(data).hexdigest(), **mesh_report(mesh)}
            pending.append((path, data))
            outputs.append(report)
            next_outputs[str(path)] = report
        for path, data in pending:
            atomic_bytes(path, data)
        report = {"ok": True, "version": VERSION, "revision": revision(state), "units": "mm",
                  "parameters": params, "outputs": outputs, "trace": engine.trace,
                  "built": len(pending), "unchanged": len(outputs)-len(pending), "warnings": warnings,
                  "seconds": round(time.perf_counter()-started, 6)}
        atomic_bytes(state_path, json_bytes({"outputs": next_outputs}))
        atomic_bytes(root/METADATA_DIR/"last-build.json", json_bytes(report))
        return report
