"""CLI for project folders and source edits."""
import copy
import json
import os
from pathlib import Path

from ._output import add_units
from .._common import CADError, EXPORT_FORMATS, atomic_bytes, document_path, json_bytes, parse_sets, read_json, read_stdin_json
from ..project_build import project_build
from ..project_model import compile_project, inside, project_schema, safe_name, segment_file
from ..project_store import (
    METADATA_DIR, apply_patch, init_project, load_state, mutate, pointer_parent,
    project_lock, recover as recover_project, revision, segment_recipe,
)
from ..schema import ANCHOR


def add_parser(subparsers):
    p = subparsers.add_parser("project", help="Editable project folders with layers, segments and assemblies")
    p.set_defaults(handler=execute)
    s = p.add_subparsers(dest="project_command", required=True)
    s.add_parser("schema", help="Print the project manifest schema")
    init = s.add_parser("init", help="Create an empty or working demo project")
    init.add_argument("directory")
    init.add_argument("--name")
    add_units(init)
    init.add_argument("--template", choices=["blank", "arch-demo"], default="blank")
    for name, help_text in (("info", "Show the project manifest and revision"),
                            ("check", "Validate project sources and references")):
        sp = s.add_parser(name, help=help_text)
        sp.add_argument("directory")
    for name in ("build", "plan"):
        sp = s.add_parser(name, help="Incremental build" if name == "build" else "Explain which outputs need rebuilding")
        sp.add_argument("directory")
        sp.add_argument("--segment", action="append")
        sp.add_argument("--assembly", action="append")
        sp.add_argument("--layer", action="append")
        sp.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="Override a declared dimension; repeatable")
        sp.add_argument("--out-dir")
        sp.add_argument("--format", choices=EXPORT_FORMATS, default="stl")
        sp.add_argument("--rebuild", action="store_true", help="Rewrite selected outputs while still using geometry cache")
    compile_p = s.add_parser("compile", help="Flatten the project into a standalone recipe")
    compile_p.add_argument("directory")
    compile_p.add_argument("-o", "--output", required=True)
    compile_p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                           help="Override a declared dimension; repeatable")
    edits = {
        "layer": "Create, enable, or disable a layer",
        "put": "Add or replace a segment",
        "duplicate": "Copy a segment into a new part",
        "place": "Set a segment transform",
        "attach": "Align a segment to another part by bounds anchors",
        "detach": "Remove a bounds attachment",
        "stitch": "Define a named assembly",
        "set": "Edit a value by JSON pointer",
        "patch": "Apply and validate a group of source edits",
        "remove": "Remove a segment or assembly from the manifest",
    }
    for name, help_text in edits.items():
        sp = s.add_parser(name, help=help_text)
        sp.add_argument("directory")
        sp.add_argument("--if-revision", help="Reject an edit unless this project revision is current")
        if name in ("layer", "put", "place", "attach", "detach", "stitch", "remove"):
            sp.add_argument("name")
        if name == "layer":
            sp.add_argument("--enabled", choices=["true", "false"], default="true")
        elif name == "put":
            group = sp.add_mutually_exclusive_group(required=True)
            group.add_argument("--file", help="Segment recipe JSON; - reads stdin")
            group.add_argument("--spec", help="One solid operation as JSON; wrapped as a part recipe")
            sp.add_argument("--solid", default="part")
            sp.add_argument("--layer", default="structure")
            sp.add_argument("--replace", action="store_true")
            sp.add_argument("--translate", nargs=3, type=float)
        elif name == "duplicate":
            sp.add_argument("source")
            sp.add_argument("name")
            sp.add_argument("--layer")
            sp.add_argument("--translate", nargs=3, type=float)
        elif name == "place":
            for field in ("translate", "rotate", "scale"):
                sp.add_argument("--"+field, nargs=3, type=float)
        elif name == "attach":
            sp.add_argument("target")
            sp.add_argument("--anchor", choices=ANCHOR["enum"], default="bottom")
            sp.add_argument("--target-anchor", choices=ANCHOR["enum"], default="top")
            sp.add_argument("--offset", nargs=3, type=float, default=[0, 0, 0])
        elif name == "stitch":
            group = sp.add_mutually_exclusive_group(required=True)
            group.add_argument("--members", nargs="+")
            group.add_argument("--layers", nargs="+")
            sp.add_argument("--mode", choices=["union", "combine"], default="union")
        elif name == "set":
            sp.add_argument("pointer", help="JSON pointer into project.json, or a segment document with --segment")
            sp.add_argument("value", help="JSON literal; bare =expression is also accepted")
            sp.add_argument("--segment")
        elif name == "patch":
            sp.add_argument("--file", required=True, help="Patch array against {project: manifest, segments: documents}; - for stdin")
        elif name == "remove":
            sp.add_argument("--cascade", action="store_true", help="Remove dependent assemblies/attachments too")
    recovery = s.add_parser("recover", help="Resolve a write interrupted by a crash")
    recovery.add_argument("directory")
    recovery.add_argument("--action", choices=["rollback", "commit"], required=True)
    return s


def execute(args):
    command = args.project_command
    if command == "schema":
        return project_schema()
    root = Path(args.directory).resolve()
    if command == "init":
        return init_project(root, args.name, args.template, args.units)
    if command == "recover":
        return recover_project(root, args.action)
    if command in ("build", "plan"):
        return project_build(
            root, segments=args.segment or (), assemblies=args.assembly or (), layers=args.layer or (),
            overrides=parse_sets(args.set), out_dir=args.out_dir, format=args.format,
            rebuild=args.rebuild, plan=command == "plan",
        )
    if command in ("info", "check", "compile"):
        with project_lock(root):
            state = load_state(root)
            recipe, selection, params = compile_project(root, state, parse_sets(getattr(args, "set", None)))
            if command == "compile":
                if not recipe["exports"]:
                    raise CADError("Project has no enabled outputs")
                output = Path(args.output).resolve()
                if output == root/"project.json" or any(output.is_relative_to((root/p).resolve()) for p in ("segments", METADATA_DIR, "assets")):
                    raise CADError("Compiled recipe cannot overwrite project source or metadata")
                atomic_bytes(output, json_bytes(recipe))
                return {"ok": True, "path": str(output), "note": "External asset paths are absolute"}
            return {"ok": True, "revision": revision(state), "project": state["project"],
                    "parameters": params, "enabled_outputs": selection,
                    "note": "Schema and dependencies valid; geometry not evaluated"}

    def change(state):
        m, documents = state["project"], state["segments"]
        if command == "layer":
            m["layers"][safe_name(args.name)] = {"enabled": args.enabled == "true"}
        elif command == "put":
            safe_name(args.name)
            safe_name(args.layer)
            if args.name in m["assemblies"]:
                raise CADError("Name is already an assembly")
            if args.name in m["segments"] and not args.replace:
                raise CADError("Segment exists; use --replace")
            if args.spec is not None:
                raw = segment_recipe(json.loads(args.spec))
            else:
                raw = read_stdin_json() if args.file == "-" else read_json(args.file)
            m["layers"].setdefault(args.layer, {"enabled": True})
            entry = {"path": f"segments/{args.layer}/{args.name}.json", "solid": args.solid, "layer": args.layer}
            if args.name in m["segments"]:
                entry = {**m["segments"][args.name], **entry}
            if args.file and args.file != "-":
                # Imported recipe paths belong to its original directory. Keep
                # their meaning when moving the document into this project.
                source_dir = Path(args.file).resolve().parent
                destination_dir = segment_file(root, entry["path"]).parent
                rebase_assets(raw, source_dir, destination_dir)
            if args.translate:
                entry.setdefault("placement", {})["translate"] = args.translate
            m["segments"][args.name] = entry
            documents[args.name] = raw
        elif command == "duplicate":
            safe_name(args.name)
            if args.name in m["segments"] or args.name in m["assemblies"]:
                raise CADError("Duplicate destination name already exists")
            entry = copy.deepcopy(m["segments"][args.source])
            layer = safe_name(args.layer or entry["layer"])
            m["layers"].setdefault(layer, {"enabled": True})
            entry.update(layer=layer, path=f"segments/{layer}/{args.name}.json")
            if args.translate:
                entry.setdefault("placement", {})["translate"] = args.translate
            m["segments"][args.name] = entry
            raw = copy.deepcopy(documents[args.source])
            source_dir = segment_file(root, m["segments"][args.source]["path"]).parent
            destination_dir = segment_file(root, entry["path"]).parent
            rebase_assets(raw, source_dir, destination_dir)
            documents[args.name] = raw
        elif command == "place":
            entry = m["segments"][args.name]
            for field in ("translate", "rotate", "scale"):
                if getattr(args, field) is not None:
                    entry.setdefault("placement", {})[field] = getattr(args, field)
        elif command == "attach":
            m["segments"][args.name]["attach"] = {"target": args.target, "anchor": args.anchor,
                "target_anchor": args.target_anchor, "offset": args.offset}
        elif command == "detach":
            m["segments"][args.name].pop("attach", None)
        elif command == "stitch":
            safe_name(args.name)
            if args.name in m["segments"]:
                raise CADError("Assembly name is already a segment")
            m["assemblies"][args.name] = {"mode": args.mode,
                **({"members": args.members} if args.members else {"layers": args.layers})}
        elif command == "set":
            try:
                value = json.loads(args.value)
            except json.JSONDecodeError:
                if not args.value.startswith("="):
                    raise CADError("Value must be JSON, or an =expression")
                value = args.value
            document = documents[args.segment] if args.segment else m
            parent, key = pointer_parent(document, args.pointer)
            op = "replace" if isinstance(parent, list) and key != "-" else "add"
            apply_patch(document, [{"op": op, "path": args.pointer, "value": value}])
        elif command == "patch":
            operations = read_stdin_json() if args.file == "-" else read_json(args.file)
            apply_patch(state, operations)
        elif command == "remove":
            name = args.name
            if name not in m["segments"] and name not in m["assemblies"]:
                raise CADError(f"Unknown part: {name}")
            queue = [name]
            while queue:
                current = queue.pop()
                dependent_assemblies = [n for n, e in m["assemblies"].items() if current in e.get("members", [])]
                attached = [n for n, e in m["segments"].items() if e.get("attach", {}).get("target") == current]
                if (dependent_assemblies or attached) and not args.cascade:
                    raise CADError(f"Dependent assemblies/attachments exist: {dependent_assemblies+attached}; use --cascade")
                queue.extend(dependent_assemblies)
                for n in attached:
                    m["segments"][n].pop("attach")
                m["segments"].pop(current, None)
                documents.pop(current, None)
                m["assemblies"].pop(current, None)
    return mutate(root, command, change, args.if_revision)


def rebase_assets(recipe, source_dir, destination_dir):
    if source_dir == destination_dir or not isinstance(recipe, dict):
        return
    for kind in ("profiles", "solids"):
        nodes = recipe.get(kind)
        if not isinstance(nodes, dict):
            continue
        for node in nodes.values():
            if not isinstance(node, dict) or not isinstance(node.get("path"), str):
                continue
            path = (source_dir/document_path(node["path"])).resolve()
            try:
                node["path"] = Path(os.path.relpath(path, destination_dir)).as_posix()
            except ValueError:
                # Windows cannot express a relative path between drives.
                node["path"] = path.as_posix()
