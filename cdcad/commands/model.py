"""Quick modeling commands that compile to recipes."""
import argparse
import json
from pathlib import Path

from ._output import add_output, add_units
from .._common import CADError
from ..schema import SOLID_OPS


def register(s):
    make = s.add_parser("make", help="Create a dimensioned structural part directly from CLI flags")
    make.set_defaults(handler=make_part)
    shapes = make.add_subparsers(dest="make_op", required=True, metavar="SHAPE")
    descriptions = {
        "arch": "Arch with a dimensioned opening and optional straight legs",
        "wall": "Wall with optional cutouts",
        "panel": "Rectangular panel with optional cutouts",
        "frame": "Open rectangular frame",
        "beam": "Rectangular beam between XYZ endpoints",
        "box": "Box centered on the origin",
        "cylinder": "Cylinder centered on the origin, along Z",
        "cone": "Cone or frustum centered on the origin, along Z",
        "sphere": "Sphere centered on the origin",
        "torus": "Torus around the Z axis, centered on the origin",
    }
    for op, description in descriptions.items():
        shape = shapes.add_parser(op, help=description, description=description)
        required, props = SOLID_OPS[op]
        for key, rule in props.items():
            if key == "note":
                continue
            settings = {"required": key in required, "default": argparse.SUPPRESS, "help": rule.get("description")}
            if rule.get("type") == "array" and rule.get("minItems") == rule.get("maxItems") and rule.get("items", {}).get("type") == "number":
                settings.update(nargs=rule["minItems"], type=float, metavar=("X", "Y", "Z")[:rule["minItems"]])
            elif rule.get("type") == "number":
                settings["type"] = float
            elif rule.get("type") == "integer":
                settings["type"] = int
            elif "enum" in rule:
                settings["choices"] = rule["enum"]
            else:
                settings.update(type=json.loads, metavar="JSON")
            shape.add_argument("--"+key.replace("_", "-"), **settings)
        add_output(shape)
        add_units(shape)
    e = s.add_parser("extrude", help="Extrude a planar drawing or SVG path")
    e.set_defaults(handler=extrude)
    e.add_argument("input", help=".svg, .dxf, or literal SVG path data with --path-data")
    e.add_argument("--path-data", action="store_true", help="Treat the positional input as literal SVG path data")
    e.add_argument("--height", type=float, required=True)
    e.add_argument("--z", type=float, default=0, help="Base elevation (default: 0)")
    e.add_argument("--tolerance", type=float, help="DXF curve flattening tolerance (default: 0.05)")
    add_units(e)
    e.add_argument("--step", type=float, default=.5, help="SVG maximum sample spacing")
    e.add_argument("--width", type=float, help="Stroke width for --path-data")
    e.add_argument("--select", action="append", help="SVG filled shape ID or DXF layer; repeatable")
    add_output(e)


def make_part(args):
    from ..build import build
    from ..recipe import resolve_recipe

    node = {"op": args.make_op, **{key: getattr(args, key) for key in SOLID_OPS[args.make_op][1] if hasattr(args, key)}}
    raw = {"version": 1, "units": args.units, "solids": {"part": node},
           "exports": [{"solid": "part", "path": str(Path(args.output).resolve()), "allow_open": args.allow_open}]}
    return build(resolve_recipe(raw), Path.cwd(), force=args.force)


def extrude(args):
    from ..build import build
    from ..recipe import resolve_recipe

    if args.path_data:
        if args.select or args.tolerance is not None:
            raise CADError("--select and --tolerance require a drawing file")
        profile = {"op": "path", "d": args.input, "step": args.step}
        if args.width is not None:
            profile["width"] = args.width
    else:
        if args.width is not None:
            raise CADError("--width requires --path-data")
        suffix = Path(args.input).suffix.lower()
        if suffix not in (".svg", ".dxf"):
            raise CADError("Quick extrude accepts SVG or DXF; use --path-data for literal paths")
        profile = {"op": suffix[1:], "path": str(Path(args.input).resolve())}
        if suffix == ".svg":
            profile["step"] = args.step
        if args.tolerance is not None:
            if suffix != ".dxf":
                raise CADError("--tolerance requires a DXF drawing")
            profile["tolerance"] = args.tolerance
        if args.select:
            profile["ids" if suffix == ".svg" else "layers"] = args.select
    raw = {"version": 1, "units": args.units, "profiles": {"outline": profile},
           "solids": {"part": {"op": "extrude", "profile": "outline", "height": args.height, "z": args.z}},
           "exports": [{"solid": "part", "path": str(Path(args.output).resolve()), "allow_open": args.allow_open}]}
    return build(resolve_recipe(raw), Path.cwd(), force=args.force)
