"""Mesh inspection, editing, boolean, component, and section commands."""
from pathlib import Path

from ._output import add_output
from .._common import CADError, atomic_bytes
from ..schema import TRANSFORM


def register(s):
    i = s.add_parser("inspect", help="Print mesh topology, bounds and volume; assumes coordinates are mm")
    i.set_defaults(handler=inspect)
    i.add_argument("input")
    i.add_argument("--require-solid", action="store_true")
    edit = s.add_parser("edit", help="Transform/repair/cut/simplify an existing mesh in that order")
    edit.set_defaults(handler=edit_mesh)
    edit.add_argument("input")
    edit.add_argument("--scale", nargs=3, type=float, metavar=("X", "Y", "Z"))
    edit.add_argument("--rotate", nargs=3, type=float, metavar=("X", "Y", "Z"), help="Euler degrees")
    edit.add_argument("--translate", nargs=3, type=float, metavar=("X", "Y", "Z"))
    edit.add_argument("--repair", action="store_true")
    edit.add_argument("--cut-origin", nargs=3, type=float)
    edit.add_argument("--cut-normal", nargs=3, type=float)
    edit.add_argument("--no-cap", action="store_true")
    edit.add_argument("--faces", type=int)
    edit.add_argument("--hull", action="store_true")
    add_output(edit)
    bo = s.add_parser("boolean", help="Solid mesh union/difference/intersection; first input is base for difference")
    bo.set_defaults(handler=boolean_meshes)
    bo.add_argument("operation", choices=["union", "difference", "intersection"])
    bo.add_argument("inputs", nargs="+")
    add_output(bo)
    sp = s.add_parser("split", help="Export disconnected components as separate STLs")
    sp.set_defaults(handler=split_mesh)
    sp.add_argument("input")
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--allow-open", action="store_true")
    sec = s.add_parser("section", help="Export a horizontal cross section as SVG (coordinates assumed mm)")
    sec.set_defaults(handler=section_mesh)
    sec.add_argument("input")
    sec.add_argument("--z", required=True, type=float)
    sec.add_argument("--output", "-o", required=True)
    sec.add_argument("--force", action="store_true")


def single_export(mesh, args):
    from ..mesh import export_bytes, mesh_report

    path = Path(args.output).resolve()
    if path.exists() and not args.force:
        raise CADError(f"Output exists: {path}; use --force")
    data = export_bytes(mesh, path, args.allow_open)
    atomic_bytes(path, data)
    return {"ok": True, "path": str(path), "units": "mm", **mesh_report(mesh)}


def inspect(args):
    from ..mesh import load_mesh, mesh_report, valid_mesh

    mesh = load_mesh(Path(args.input))
    if args.require_solid:
        valid_mesh(mesh)
    return {"ok": True, "units": "mm", **mesh_report(mesh)}


def boolean_meshes(args):
    from ..mesh import boolean, load_mesh

    if len(args.inputs) < 2:
        raise CADError("Boolean command requires at least two input files")
    return single_export(boolean(args.operation, [load_mesh(Path(x)) for x in args.inputs]), args)


def edit_mesh(args):
    from ..mesh import clean_mesh, cut_mesh, load_mesh, transform_mesh

    mesh = load_mesh(Path(args.input))
    mesh = transform_mesh(mesh, {k: getattr(args, k) for k in TRANSFORM if getattr(args, k, None) is not None})
    if args.repair:
        mesh = clean_mesh(mesh)
    if bool(args.cut_origin) != bool(args.cut_normal):
        raise CADError("Supply both --cut-origin and --cut-normal")
    if args.cut_origin:
        mesh = cut_mesh(mesh, args.cut_origin, args.cut_normal, cap=not args.no_cap)
    if args.faces is not None:
        if args.faces < 4:
            raise CADError("--faces must be >= 4")
        if args.faces < len(mesh.faces):
            mesh = mesh.simplify_quadric_decimation(face_count=args.faces)
    if args.hull:
        mesh = mesh.convex_hull
    return single_export(mesh, args)


def split_mesh(args):
    from ..mesh import export_bytes, load_mesh

    parts = load_mesh(Path(args.input)).split(only_watertight=False)
    parts = sorted(parts, key=lambda m: (-abs(m.volume), *m.centroid))
    pending = []
    for i, part in enumerate(parts):
        path = Path(args.out_dir).resolve()/f"part_{i:03d}.stl"
        if path.exists() and not args.force:
            raise CADError(f"Output exists: {path}; use --force")
        pending.append((path, export_bytes(part, path, args.allow_open)))
    for path, data in pending:
        atomic_bytes(path, data)
    return {"ok": True, "parts": [str(p) for p, _ in pending]}


def section_mesh(args):
    from .._profiles import loops_fill, polygons
    from ..mesh import load_mesh

    if Path(args.output).exists() and not args.force:
        raise CADError("Section output exists; use --force")
    section = load_mesh(Path(args.input)).section(plane_origin=[0, 0, args.z], plane_normal=[0, 0, 1])
    if section is None:
        raise CADError("Plane does not intersect mesh")
    xy_loops = [loop[:, :2] for loop in section.discrete]
    geom = loops_fill(xy_loops)
    xmin, ymin, xmax, ymax = geom.bounds
    paths = []
    for p in polygons(geom):
        for ring in [p.exterior, *p.interiors]:
            xy = list(ring.coords)
            paths.append("M " + " L ".join(f"{x:.10g},{y:.10g}" for x, y in xy) + " Z")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{xmax-xmin}mm" height="{ymax-ymin}mm" '
           f'viewBox="{xmin} {ymin} {xmax-xmin} {ymax-ymin}">'
           f'<path fill-rule="evenodd" d="{" ".join(paths)}"/></svg>\n')
    atomic_bytes(args.output, svg.encode())
    return {"ok": True, "path": str(Path(args.output).resolve()), "z": args.z, "area": geom.area}
