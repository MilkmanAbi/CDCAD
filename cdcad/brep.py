"""Exact solids from OpenCascade (the B-rep kernel behind FreeCAD and CadQuery).

The hybrid engine uses this module for solids whose whole history is made of the operations
below: primitives, extrusions of plain profiles, booleans, uniform scale/rotate/translate,
and all-edge fillets and chamfers. Fillets here are exact rolling-ball surfaces on the
real edges, so they are fast even on unions. Anything else stays on the triangle-mesh path.

Shapes are built in the recipe's units. A shape becomes a mesh (for checks, STL and the
renders) by tessellation, and becomes a STEP file with its exact curved faces.

The OCP package (cadquery-ocp) is an optional install: ``pip install cdcad[brep]``.
"""
import math
import os
import tempfile

import numpy as np
import trimesh

from ._common import CADError

class _quiet:
    """Silence OpenCascade's own console messages: they are written to the process's
    standard output, which must carry only the command's JSON result."""

    def __enter__(self):
        import sys
        sys.stdout.flush()
        self.saved = os.dup(1)
        self.null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.null, 1)
        return self

    def __exit__(self, *exc):
        import sys
        sys.stdout.flush()
        os.dup2(self.saved, 1)
        os.close(self.saved)
        os.close(self.null)
        return False

BREP_PRIMITIVES = ("box", "cylinder", "cone", "sphere", "torus")
BREP_OPS = BREP_PRIMITIVES + ("extrude", "union", "difference", "intersection", "transform", "fillet", "chamfer")


def available():
    """True when the OpenCascade bindings can be imported."""
    try:
        import OCP.BRepPrimAPI  # noqa: F401
        return True
    except ImportError:
        return False


def _require():
    try:
        import OCP  # noqa: F401
    except ImportError as exc:
        raise CADError("This solid needs the OpenCascade B-rep kernel, which is not installed",
                       hint="pip install cdcad[brep]  (or set edges to concave/convex to use the mesh method)") from exc


def _pnt(x, y, z):
    from OCP.gp import gp_Pnt
    return gp_Pnt(float(x), float(y), float(z))


def _ax2(origin=(0, 0, 0), direction=(0, 0, 1)):
    from OCP.gp import gp_Ax2, gp_Dir
    return gp_Ax2(_pnt(*origin), gp_Dir(*[float(v) for v in direction]))


def _move(shape, x, y, z):
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(float(x), float(y), float(z)))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _polygon_face(points, z):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in points:
        poly.Add(_pnt(x, y, z))
    poly.Close()
    return BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()


def _prism(face, height):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, float(height)), True).Shape()


def _cut(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    op = BRepAlgoAPI_Cut(a, b)
    if not op.IsDone():
        raise CADError("OpenCascade could not subtract these solids", hint="check that they overlap properly")
    return op.Shape()


def _fuse(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    op = BRepAlgoAPI_Fuse(a, b)
    if not op.IsDone():
        raise CADError("OpenCascade could not fuse these solids")
    return op.Shape()


def _common(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    op = BRepAlgoAPI_Common(a, b)
    if not op.IsDone():
        raise CADError("OpenCascade could not intersect these solids")
    return op.Shape()


def primitive(node):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone, \
        BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeTorus
    op = node["op"]
    if op == "box":
        x, y, z = node["size"]
        return _move(BRepPrimAPI_MakeBox(float(x), float(y), float(z)).Shape(), -x/2, -y/2, -z/2)
    if op == "cylinder":
        h = node["height"]
        return BRepPrimAPI_MakeCylinder(_ax2((0, 0, -h/2)), float(node["radius"]), float(h)).Shape()
    if op == "cone":
        h = node["height"]
        return BRepPrimAPI_MakeCone(_ax2((0, 0, -h/2)), float(node["radius"]), float(node.get("radius_top", 0)),
                                    float(h)).Shape()
    if op == "sphere":
        return BRepPrimAPI_MakeSphere(_pnt(0, 0, 0), float(node["radius"])).Shape()
    if op == "torus":
        if node["minor"] >= node["major"]:
            raise CADError("torus minor radius must be less than the major radius")
        return BRepPrimAPI_MakeTorus(_ax2(), float(node["major"]), float(node["minor"])).Shape()
    raise CADError(f"Not a B-rep primitive: {op}")


def extruded(geom, node):
    """Prism of a shapely (multi)polygon from z = node['z'] up by node['height']."""
    z0 = float(node.get("z", 0))
    polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    result = None
    for poly in polygons:
        solid = _prism(_polygon_face(list(poly.exterior.coords)[:-1], z0), node["height"])
        for hole in poly.interiors:
            solid = _cut(solid, _prism(_polygon_face(list(hole.coords)[:-1], z0), node["height"]))
        result = solid if result is None else _fuse(result, solid)
    return result


def transformed(shape, node):
    from OCP.gp import gp_Trsf, gp_Pnt, gp_Dir, gp_Ax1
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    scale = node.get("scale", [1, 1, 1])
    if not (scale[0] == scale[1] == scale[2]):
        raise CADError("B-rep transforms need one uniform scale factor",
                       hint="use the same value for all three scale components")
    trsf_scale = gp_Trsf()
    trsf_scale.SetScale(gp_Pnt(0, 0, 0), float(scale[0]))
    rx, ry, rz = (math.radians(a) for a in node.get("rotate", [0, 0, 0]))
    trsf_rotation = gp_Trsf()
    # Same convention as the mesh path: euler_matrix(sxyz) applies x, then y, then z.
    for angle, axis in ((rx, (1, 0, 0)), (ry, (0, 1, 0)), (rz, (0, 0, 1))):
        step = gp_Trsf()
        step.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)), angle)
        trsf_rotation = step.Multiplied(trsf_rotation)
    tx, ty, tz = node.get("translate", [0, 0, 0])
    from OCP.gp import gp_Vec
    trsf_move = gp_Trsf()
    trsf_move.SetTranslation(gp_Vec(float(tx), float(ty), float(tz)))
    total = trsf_move.Multiplied(trsf_rotation).Multiplied(trsf_scale)
    return BRepBuilderAPI_Transform(shape, total, True).Shape()


def _unique_edges(shape):
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    edges, explorer = [], TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        if not any(edge.IsSame(seen) for seen in edges):
            edges.append(edge)
        explorer.Next()
    return edges


def edge_operation(shape, size, kind):
    """Round (fillet) or bevel (chamfer) every edge of a solid by ``size``."""
    if kind == "fillet":
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
        maker = BRepFilletAPI_MakeFillet(shape)
    else:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer
        maker = BRepFilletAPI_MakeChamfer(shape)
    edges = _unique_edges(shape)
    if not edges:
        raise CADError(f"The solid has no edges to {kind}")
    for edge in edges:
        maker.Add(float(size), edge)
    maker.Build()
    if not maker.IsDone():
        raise CADError(f"OpenCascade could not {kind} every edge at size {size:g}",
                       hint="the size may be too large for the thinnest part, or a face may be too narrow; "
                            "try a smaller size")
    return maker.Shape()


def build(node, children):
    """The shape of a B-rep solid node. ``children`` are shapes of its inputs, in order."""
    op = node["op"]
    if op in BREP_PRIMITIVES:
        return primitive(node)
    if op == "extrude":
        return extruded(node["_profile"], node)
    if op in ("union", "difference", "intersection"):
        result = children[0]
        for other in children[1:]:
            result = {"union": _fuse, "difference": _cut, "intersection": _common}[op](result, other)
        return result
    if op == "transform":
        return transformed(children[0], node)
    if op in ("fillet", "chamfer"):
        return edge_operation(children[0], node["radius" if op == "fillet" else "distance"], op)
    raise CADError(f"Not a B-rep operation: {op}")


def tessellate(shape, extent, angle=0.1):
    """Triangle mesh of a shape, with coordinates in its own units. ``extent`` is the largest
    size of the shape, used to pick the tessellation tolerance; ``angle`` (radians) is the
    largest turn allowed between neighbouring facets on curved faces."""
    _require()
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.TopLoc import TopLoc_Location
    deflection = max(extent, 1e-6)*1e-3
    with _quiet():
        BRepMesh_IncrementalMesh(shape, deflection, False, angle, True)
    vertices, faces, offset = [], [], 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            transform = location.Transformation()
            count = triangulation.NbNodes()
            for i in range(1, count+1):
                p = triangulation.Node(i).Transformed(transform)
                vertices.append((p.X(), p.Y(), p.Z()))
            reverse = face.Orientation() == TopAbs_REVERSED
            for i in range(1, triangulation.NbTriangles()+1):
                a, b, c = triangulation.Triangle(i).Get()
                tri = [a-1+offset, b-1+offset, c-1+offset]
                faces.append(tri[::-1] if reverse else tri)
            offset += count
        explorer.Next()
    if not faces:
        raise CADError("OpenCascade produced no triangles for the solid")
    mesh = trimesh.Trimesh(vertices=np.asarray(vertices, dtype=float), faces=np.asarray(faces, dtype=np.int64),
                           process=True)
    # Tessellation puts zero-area triangles at sharp corners; they leave edges open, so drop them.
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    return mesh


def extent(shape):
    """Largest side of a shape's bounding box."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    lo, hi = box.CornerMin(), box.CornerMax()
    return float(max(hi.X()-lo.X(), hi.Y()-lo.Y(), hi.Z()-lo.Z()))


def step_bytes(shape, scale, name):
    """STEP (AP214) bytes of a shape, with its coordinates multiplied by ``scale`` (to millimetres)."""
    _require()
    from OCP.gp import gp_Trsf, gp_Pnt
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    if scale != 1:
        trsf = gp_Trsf()
        trsf.SetScale(gp_Pnt(0, 0, 0), float(scale))
        shape = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
    writer = STEPControl_Writer()
    with _quiet():
        transferred = writer.Transfer(shape, STEPControl_AsIs)
    if transferred != IFSelect_RetDone:
        raise CADError("OpenCascade could not transfer the solid to STEP")
    handle, path = tempfile.mkstemp(suffix=".step", prefix="cdcad-")
    os.close(handle)
    try:
        with _quiet():
            written = writer.Write(path)
        if written != IFSelect_RetDone:
            raise CADError("OpenCascade could not write the STEP file")
        with open(path, "rb") as f:
            data = f.read()
    finally:
        os.remove(path)
    return data


def volume(shape):
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()
