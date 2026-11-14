"""Project source files, cooperative locking, revisions, and recovery."""
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path

from ._common import CADError, atomic_bytes, document_path, json_bytes, read_json
from .project_model import check_manifest, compile_project, inside, safe_name, segment_file

METADATA_DIR = ".cdcad"
LEGACY_METADATA_DIR = ".agentcad"  # metadata directory of projects created before the CDCAD rename


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def revision(state):
    return digest(state)


def load_state(root, allow_pending=False):
    root = Path(root).resolve()
    if not allow_pending and (root/METADATA_DIR/"pending.json").exists():
        raise CADError("Unfinished project write; use project recover DIR --action rollback or commit")
    manifest = read_json(root/"project.json")
    check_manifest(manifest)
    documents = {name: read_json(segment_file(root, entry["path"])) for name, entry in manifest["segments"].items()}
    return {"project": manifest, "segments": documents}


@contextmanager
def project_lock(root):
    root = Path(root).resolve()
    if not (root/"project.json").is_file() and not (root/METADATA_DIR/"pending.json").is_file():
        raise CADError(f"No project.json at {root}; initialize a project first")
    if (root/LEGACY_METADATA_DIR/"pending.json").exists():
        raise CADError(f"Unfinished write from AgentCAD in {LEGACY_METADATA_DIR}/pending.json; with the project idle, "
                       f"rename {LEGACY_METADATA_DIR} to {METADATA_DIR} and run project recover")
    lock = root/METADATA_DIR/"project.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        try:
            owner = lock.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            owner = ""
        holder = f" (process {owner})" if owner.isdigit() else ""
        raise CADError(f"Project is locked by another command{holder}; after a crashed process "
                       "remove .cdcad/project.lock manually")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as lock_file:
            lock_file.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def state_files(state):
    m = state["project"]
    return {"project.json": m, **{document_path(e["path"]).as_posix(): state["segments"][n] for n, e in m["segments"].items()}}


def commit(root, before, after, action):
    compile_project(root, after)
    old, new = state_files(before), state_files(after)
    writes = {p: value for p, value in new.items() if p not in old or old[p] != value}
    if not writes:
        return {"ok": True, "changed": False, "revision": revision(before)}
    for relative, value in writes.items():
        path = inside(root, relative)
        if relative not in old and path.exists() and read_json(path) != value:
            raise CADError(f"Untracked source file exists and differs: {relative}; move it aside or choose another part name")
    # Keep removed segment source files for recovery/reuse. Removal edits the manifest.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    history = root/METADATA_DIR/"history"/f"{stamp}-{revision(before)[:12]}.json"
    atomic_bytes(history, json_bytes({"action": action, "before": before, "after_revision": revision(after)}))
    backups = {p: old[p] if p in old else read_json(inside(root, p)) if inside(root, p).is_file() else None for p in writes}
    journal = {"action": action, "before": backups, "after": writes}
    journal_path = root/METADATA_DIR/"pending.json"
    atomic_bytes(journal_path, json_bytes(journal))
    for relative in sorted(writes, key=lambda p: p == "project.json"):
        atomic_bytes(inside(root, relative), json_bytes(writes[relative]))
    journal_path.unlink()
    return {"ok": True, "changed": True, "revision": revision(after), "written": list(writes),
            "history": str(history.relative_to(root))}


HISTORY_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}")


def history_ids(root):
    """Ids of the saved before-edit snapshots, oldest first (ids start with a UTC timestamp)."""
    folder = Path(root).resolve()/METADATA_DIR/"history"
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if HISTORY_ID.fullmatch(p.stem))


def history_entry(root, entry_id, current):
    try:
        record = read_json(Path(root).resolve()/METADATA_DIR/"history"/f"{entry_id}.json")
        stamp = datetime.strptime(entry_id.split("-")[0], "%Y%m%dT%H%M%S%fZ")
        entry = {"id": entry_id, "time": stamp.isoformat(timespec="microseconds")+"Z", "action": record["action"],
                 "before_revision": revision(record["before"]), "after_revision": record["after_revision"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {"id": entry_id, "damaged": True}
    return {**entry, "current": entry["after_revision"] == current}


def history_report(root, state, limit):
    if limit < 1:
        raise CADError("--limit must be at least 1")
    current, ids = revision(state), history_ids(root)
    return {"ok": True, "revision": current, "total": len(ids),
            "entries": [history_entry(root, entry_id, current) for entry_id in reversed(ids[-limit:])]}


def history_snapshot(root, entry):
    """Return (id, state) for the project as it was before an edit; entry is an id or 'last'."""
    root = Path(root).resolve()
    if entry == "last":
        ids = history_ids(root)
        if not ids:
            raise CADError("No history to restore")
        entry = ids[-1]
    elif not HISTORY_ID.fullmatch(entry):
        raise CADError("A history entry is an id shown by project history, or 'last'")
    path = root/METADATA_DIR/"history"/f"{entry}.json"
    if not path.is_file():
        raise CADError(f"No history entry: {entry}")
    try:
        before = read_json(path)["before"]
        intact = isinstance(before["project"], dict) and isinstance(before["segments"], dict)
    except (ValueError, KeyError, TypeError):
        intact = False
    if not intact:
        raise CADError(f"History entry is damaged: {entry}")
    return entry, before


def mutate(root, operation, change, expected=None):
    root = Path(root).resolve()
    with project_lock(root):
        before = load_state(root)
        if expected and expected != revision(before):
            raise CADError("Revision conflict: project changed since it was read")
        after = copy.deepcopy(before)
        change(after)
        return commit(root, before, after, operation)


def pointer_parent(document, pointer):
    if not pointer.startswith("/") or pointer == "/":
        raise CADError("Use a non-root JSON pointer, e.g. /params/span")
    if re.search(r"~(?![01])", pointer):
        raise CADError("JSON pointer escapes must use ~0 or ~1")
    keys = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    parent = document
    for key in keys[:-1]:
        if isinstance(parent, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key) or int(key) >= len(parent):
                raise CADError(f"Invalid array pointer index: {key}")
            parent = parent[int(key)]
        elif isinstance(parent, dict) and key in parent:
            parent = parent[key]
        else:
            raise CADError(f"JSON pointer parent does not exist: {pointer}")
    return parent, keys[-1]


def apply_patch(document, operations):
    if not isinstance(operations, list):
        raise CADError("Patch must be a JSON array of add/replace/remove/test operations")
    for index, change in enumerate(operations):
        if not isinstance(change, dict) or change.get("op") not in {"add", "replace", "remove", "test"}:
            raise CADError(f"Unsupported JSON patch operation at index {index}")
        if not isinstance(change.get("path"), str):
            raise CADError(f"Patch operation {index} needs a string path")
        if change["op"] != "remove" and "value" not in change:
            raise CADError(f"Patch operation {index} ({change['op']}) needs a value")
        parent, key = pointer_parent(document, change["path"])
        op = change["op"]
        if isinstance(parent, list):
            if key == "-" and op == "add":
                parent.append(copy.deepcopy(change["value"]))
                continue
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise CADError("Array pointer indices must be canonical nonnegative integers")
            key = int(key)
            if key >= len(parent) and not (op == "add" and key == len(parent)):
                raise CADError("Array patch index out of bounds")
        elif not isinstance(parent, dict):
            raise CADError("Patch parent must be an object or array")
        elif op != "add" and key not in parent:
            raise CADError(f"Patch target does not exist: {change['path']}")
        if op == "test":
            if parent[key] != change["value"]:
                raise CADError(f"Patch test failed: {change['path']}")
        elif op == "remove":
            del parent[key]
        elif isinstance(parent, list) and op == "add":
            parent.insert(key, copy.deepcopy(change["value"]))
        else:
            parent[key] = copy.deepcopy(change["value"])


def segment_recipe(node):
    return {"version": 1, "profiles": {}, "solids": {"part": node},
            "exports": [{"solid": "part", "path": "part.stl"}]}


def init_project(root, name, template, units="mm"):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise CADError("Project init requires an empty or nonexistent directory")
    if template == "arch-demo" and units != "mm":
        raise CADError("The arch-demo template uses millimeters; use --template blank for other units")
    manifest = {"project_version": 1, "name": name or root.name, "units": units, "params": {},
                "layers": {"structure": {"enabled": True}}, "segments": {}, "assemblies": {}}
    state = {"project": manifest, "segments": {}}
    if template == "arch-demo":
        manifest["params"] = {"span": 60, "rise": 30, "leg": 22, "rib": 6, "depth": 12}
        manifest["layers"] = {"foundation": {"enabled": True}, "structure": {"enabled": True}, "details": {"enabled": True}}
        nodes = {
            "base": {"op": "panel", "size": [110, 42], "thickness": 5, "center": [0, 0, 2.5],
                     "cutouts": [{"shape": "circle", "radius": 2.2, "center": [-45, 12]},
                                 {"shape": "circle", "radius": 2.2, "center": [45, 12]}]},
            "arch": {"op": "arch", "span": "=span", "rise": "=rise", "leg": "=leg", "thickness": "=rib", "depth": "=depth"},
            "brace": {"op": "beam", "start": [-36, 0, 8], "end": [-43, 16, 31], "size": [4, 4]},
            "panel": {"op": "wall", "length": 28, "height": 20, "thickness": 3,
                      "origin": [0, 0, 0], "cutouts": [{"shape": "slot", "size": [16, 5], "center": [0, 11]}]}}
        for n, node in nodes.items():
            layer = "foundation" if n == "base" else "details" if n == "panel" else "structure"
            manifest["segments"][n] = {"path": f"segments/{layer}/{n}.json", "solid": "part", "layer": layer}
            state["segments"][n] = segment_recipe(node)
        manifest["segments"]["arch"]["attach"] = {"target": "base", "anchor": "bottom", "target_anchor": "top", "offset": [0, 0, -0.3]}
        manifest["segments"]["panel"]["placement"] = {"translate": [0, -16, 4.7]}
        manifest["assemblies"]["demo"] = {"members": ["base", "arch", "brace", "panel"], "mode": "union"}
    compile_project(root, state)
    for folder in ("segments/structure", "assets", "build", ".cdcad/history"):
        (root/folder).mkdir(parents=True, exist_ok=True)
    for relative, value in state_files(state).items():
        atomic_bytes(inside(root, relative), json_bytes(value))
    # Build reports and state record absolute paths of this machine, so keep them out of version control.
    atomic_bytes(root/".gitignore", b".cdcad/cache/\n.cdcad/project.lock\n.cdcad/pending.json\n"
                                    b".cdcad/build-state.json\n.cdcad/last-build.json\nbuild/\n")
    atomic_bytes(root/"README.md", (f"# {manifest['name']}\n\nEditable CDCAD project. Source units: {units}. Export coordinates use millimeters.\n\n"
        "- project.json: parameters, layers, placements, attachments and assemblies.\n"
        "- segments/<layer>/<name>.json: local parametric geometry for each part.\n"
        "- assets/: drawings and imported meshes. Paths inside segments are relative to the segment file.\n"
        "- build/: managed STL/GLB outputs, one file per segment and assembly.\n"
        "- .cdcad/: content cache, build reports, revision history and recovery journal.\n\n"
        "Run `cdcad project build .`, then `cdcad project info .`.\n"
        "Use `cdcad project set . /params/span 70` to resize an arch-demo project.\n").encode())
    return {"ok": True, "path": str(root), "revision": revision(state), "template": template}

def recover(root, action):
    with project_lock(root):
        journal_path = root/METADATA_DIR/"pending.json"
        if not journal_path.is_file():
            raise CADError("No unfinished project write to recover")
        journal = read_json(journal_path)
        for relative, value in journal["before" if action == "rollback" else "after"].items():
            path = inside(root, relative)
            if value is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, json_bytes(value))
        state = load_state(root, allow_pending=True)
        compile_project(root, state)
        journal_path.unlink()
        return {"ok": True, "recovered": action, "revision": revision(state)}
