"""Preview pictures of meshes and recipes."""
from pathlib import Path

from .._common import CADError, atomic_bytes


def register(s):
    r = s.add_parser("render", help="Draw a PNG preview of a mesh file or of a solid in a recipe (no OpenGL needed)",
                     description="Draw a PNG preview. A .json input is a recipe: the solid is built in memory and "
                                 "nothing else is written.")
    r.set_defaults(handler=render)
    r.add_argument("input", help="Mesh file (.stl .3mf .obj .ply .off .glb) or recipe .json (- for stdin)")
    r.add_argument("-o", "--output", required=True, help="PNG file to write")
    r.add_argument("--view", default="grid", help="grid (iso, front, top, right), iso, front, back, left, right, top or bottom")
    r.add_argument("--size", type=int, default=900, help="Image width and height in pixels (default 900)")
    r.add_argument("--solid", help="Recipe solid to draw (default: the first export)")
    r.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="Override a recipe parameter")
    r.add_argument("--base-dir", help="Resolve recipe input paths here (default: the recipe's folder)")
    r.add_argument("--force", action="store_true", help="Replace an existing image")


def load_subject(input_path, solid=None, sets=(), base_dir=None):
    """(mesh in mm, label) for a mesh file or a recipe's solid."""
    from .._common import UNITS
    from ..mesh import load_mesh
    if input_path == "-" or Path(input_path).suffix.lower() == ".json":
        from ..engine import Engine
        from ..recipe import read_recipe
        recipe = read_recipe(input_path, sets)
        base = Path(base_dir).resolve() if base_dir else (Path.cwd() if input_path == "-" else Path(input_path).resolve().parent)
        names = [e["solid"] for e in recipe["exports"]]
        name = solid or (names[0] if names else None)
        if name is None:
            raise CADError("The recipe exports nothing; name a solid with --solid")
        if name not in recipe["solids"]:
            from .._common import suggest
            raise CADError(f"No solid named {name!r}", hint=suggest(name, recipe["solids"]))
        mesh = Engine(recipe, base).get("solids", name).copy()
        mesh.apply_scale(UNITS[recipe.get("units", "mm")])
        return mesh, name
    if sets or solid:
        raise CADError("--solid and --set apply to recipe (.json) inputs")
    return load_mesh(Path(input_path)), Path(input_path).name


def render(args):
    from ..render import render_png
    output = Path(args.output).resolve()
    if output.suffix.lower() != ".png":
        raise CADError("render writes PNG files; give an output ending in .png")
    if output.exists() and not args.force:
        raise CADError(f"Output exists: {output}; use --force to replace")
    mesh, label = load_subject(args.input, args.solid, args.set, args.base_dir)
    data = render_png(mesh, args.view, args.size, title=label)
    atomic_bytes(output, data)
    return {"ok": True, "path": str(output), "view": args.view, "size": args.size, "subject": label,
            "faces": len(mesh.faces), "extents": mesh.extents.tolist(), "units": "mm"}
