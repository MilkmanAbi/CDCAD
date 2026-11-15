"""Geometry generated from numbers and formulas: fields, parametric curves and paths,
sweeps, twisted extrusions, text and simple primitives."""
import functools
import math
from pathlib import Path

import numpy as np
import trimesh
from shapely import affinity, make_valid
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from ._common import CADError
from ._profiles import loops_fill, polygonal, polygons
from .expr import Field

FONT = Path(__file__).parent/"fonts"/"DejaVuSans-subset.ttf"
MAX_IMPLICIT_CELLS = 4_000_000
MAX_GRAPH_SAMPLES = 2048*2048


# ---- profiles -------------------------------------------------------------------------

def _region(points):
    """Polygon from a point ring; a self-intersecting ring is made valid rather than rejected."""
    ring = Polygon(points)
    if not ring.is_valid:
        ring = make_valid(ring)
    return polygonal(ring)


def function_profile(node):
    var = node.get("var", "x")
    start, end = node["range"]
    if not end > start:
        raise CADError("function range must increase: [start, end] with end > start")
    t = np.linspace(start, end, node.get("samples", 128))
    upper = Field(node["f"], (var,))(t)
    base = node.get("base", 0)
    if isinstance(base, str):
        lower_t, lower = t, Field(base, (var,))(t)
    else:  # a straight base needs only its two end points
        lower_t, lower = np.array([start, end]), np.full(2, float(base))
    if var == "x":
        points = np.r_[np.column_stack([t, upper]), np.column_stack([lower_t, lower])[::-1]]
    else:
        points = np.r_[np.column_stack([upper, t]), np.column_stack([lower, lower_t])[::-1]]
    return _region(points)


def curve_profile(node):
    start, end = node.get("t", [0, math.tau])
    t = np.linspace(start, end, node.get("samples", 256), endpoint=False)
    x = Field(node["x"], ("t",))(t)
    y = Field(node["y"], ("t",))(t)
    return _region(np.column_stack([x, y]))


def regular_polygon(node):
    sides = node["sides"]
    if ("radius" in node) == ("across_flats" in node):
        raise CADError("regular_polygon needs exactly one of radius or across_flats")
    radius = node["radius"] if "radius" in node else node["across_flats"]/(2*math.cos(math.pi/sides))
    angles = np.radians(node.get("rotate", 0)) + np.arange(sides)*math.tau/sides
    cx, cy = node.get("center", [0, 0])
    return polygonal(Polygon(np.column_stack([cx + radius*np.cos(angles), cy + radius*np.sin(angles)])))


def profile_array(source, node):
    cx, cy = node.get("center", [0, 0])
    dx, dy = node.get("step", [0, 0])
    copies = [affinity.translate(affinity.rotate(source, i*node.get("rotate", 0), origin=(cx, cy)), i*dx, i*dy)
              for i in range(node["count"])]
    return unary_union(copies)


@functools.lru_cache(maxsize=8)
def _font(path):
    from fontTools.ttLib import TTFont, TTLibError
    try:
        return TTFont(str(path), lazy=True)
    except (TTLibError, OSError) as exc:
        raise CADError(f"Cannot read font {path}: {exc}") from exc


def _cap_height(font, glyphs, cmap, em):
    """Height of capital letters in font units: measured on H when the font has it, else
    the OS/2 table's value, else 0.7 em."""
    from fontTools.pens.boundsPen import BoundsPen
    if ord("H") in cmap:
        pen = BoundsPen(glyphs)
        glyphs[cmap[ord("H")]].draw(pen)
        if pen.bounds and pen.bounds[3] > 0:
            return pen.bounds[3]
    os2 = font["OS/2"] if "OS/2" in font else None
    cap = getattr(os2, "sCapHeight", 0) if os2 is not None else 0
    return cap if cap and cap > 0 else 0.7*em


def text_profile(node, font_path=None):
    from fontTools.pens.basePen import BasePen

    font = _font(str(font_path or FONT))
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap() or {}
    em = font["head"].unitsPerEm
    cap = _cap_height(font, glyphs, cmap, em)
    scale = node.get("size", 10)/cap
    steps = node.get("segments", 6)

    class Pen(BasePen):
        def __init__(self, glyph_set):
            super().__init__(glyph_set)
            self.contours, self.current = [], None

        def _moveTo(self, p):
            self.current = [p]

        def _lineTo(self, p):
            self.current.append(p)

        def _curveToOne(self, p1, p2, p3):
            p0 = np.asarray(self.current[-1], dtype=float)
            u = np.linspace(0, 1, steps+1)[1:, None]
            pts = ((1-u)**3*p0 + 3*(1-u)**2*u*np.asarray(p1) + 3*(1-u)*u**2*np.asarray(p2) + u**3*np.asarray(p3))
            self.current.extend(map(tuple, pts))

        def _qCurveToOne(self, p1, p2):
            p0 = np.asarray(self.current[-1], dtype=float)
            u = np.linspace(0, 1, steps+1)[1:, None]
            pts = (1-u)**2*p0 + 2*(1-u)*u*np.asarray(p1) + u**2*np.asarray(p2)
            self.current.extend(map(tuple, pts))

        def _closePath(self):
            if self.current and len(self.current) >= 3:
                self.contours.append(self.current)
            self.current = None

        _endPath = _closePath

    loops, missing = [], []
    line_height = node.get("line_spacing", 1.2)*em
    for row, line in enumerate(node["text"].split("\n")):
        x, line_loops = 0.0, []
        for char in line:
            name = cmap.get(ord(char))
            if name is None:
                missing.append(char)
                continue
            pen = Pen(glyphs)
            glyphs[name].draw(pen)
            for contour in pen.contours:
                pts = np.asarray(contour, dtype=float)
                line_loops.append(np.column_stack([pts[:, 0] + x, pts[:, 1] - row*line_height]))
            x += glyphs[name].width*node.get("spacing", 1)
        shift = {"left": 0.0, "center": -x/2, "right": -x}[node.get("halign", "left")]
        loops.extend(loop + [shift, 0.0] for loop in line_loops)
    if missing:
        raise CADError(f"The font has no glyphs for {''.join(sorted(set(missing)))!r}",
                       hint="the bundled font covers Latin, Greek and common math symbols; give path to another font")
    if not loops:
        raise CADError("Text has no visible glyphs")
    geom = loops_fill([loop*scale for loop in loops], "nonzero")
    valign = node.get("valign", "baseline")
    if valign != "baseline":
        _, ymin, _, ymax = geom.bounds
        geom = affinity.translate(geom, 0, {"bottom": -ymin, "top": -ymax, "center": -(ymin+ymax)/2}[valign])
    return polygonal(geom)


# ---- stacking rings into solids -------------------------------------------------------

def polygon_rings(poly):
    """Exterior (counter-clockwise) and holes (clockwise) without the closing point."""
    poly = orient(poly, sign=1.0)
    return [np.asarray(poly.exterior.coords)[:-1, :2]] + [np.asarray(r.coords)[:-1, :2] for r in poly.interiors]


def cap_faces(rings):
    """Triangles filling the polygon (rings concatenated), counter-clockwise in 2D."""
    import mapbox_earcut
    vertices = np.vstack(rings).astype(np.float64)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    faces = np.asarray(mapbox_earcut.triangulate_float64(vertices, ends), dtype=np.int64).reshape(-1, 3)
    a, b, c = (vertices[faces[:, k]] for k in range(3))
    clockwise = (b[:, 0]-a[:, 0])*(c[:, 1]-a[:, 1]) - (b[:, 1]-a[:, 1])*(c[:, 0]-a[:, 0]) < 0
    faces[clockwise] = faces[clockwise][:, ::-1]
    return faces


def stack_solid(rings, layers, closed=False):
    """Solid through ``layers``: a list of (M, 3) arrays, each the polygon's ring points
    (rings concatenated, M points) placed in 3D. Consecutive layers are joined by side
    walls; an open stack gets end caps, a closed one joins the last layer to the first.
    Points that coincide (on a revolve axis, at a cone tip) are merged."""
    sizes = [len(r) for r in rings]
    total = sum(sizes)
    count = len(layers)
    vertices = np.vstack(layers)
    joins = count if closed else count-1
    faces = []
    start = 0
    for size in sizes:
        i = np.arange(size)
        j = (i+1) % size
        for level in range(joins):
            a = level*total + start
            b = ((level+1) % count)*total + start
            faces.append(np.column_stack([a+i, a+j, b+j]))
            faces.append(np.column_stack([a+i, b+j, b+i]))
        start += size
    if not closed:
        cap = cap_faces(rings)
        faces.append(cap[:, ::-1])
        faces.append(cap + (count-1)*total)
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.concatenate(faces), process=True)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    if mesh.is_watertight and mesh.volume < 0:
        mesh.invert()
    return mesh


def _checked(mesh, what):
    if not mesh.is_volume:
        raise CADError(f"{what} did not produce a closed solid; the shape probably passes through itself",
                       hint="reduce twist, scale or profile size relative to the path's curvature")
    return mesh


def extrude(geom, node):
    height = node["height"]
    z0 = node.get("z", 0)
    twist = node.get("twist", 0)
    scale = node.get("scale", 1)
    sx, sy = (scale, scale) if isinstance(scale, (int, float)) else scale
    if twist == 0 and sx == 1 and sy == 1:
        meshes = [trimesh.creation.extrude_polygon(p, height, engine="earcut") for p in polygons(geom)]
        mesh = trimesh.util.concatenate(meshes)
        mesh.apply_translation([0, 0, z0])
        return mesh
    slices = node.get("slices") or max(1, math.ceil(abs(twist)/5))
    parts = []
    for poly in polygons(geom):
        rings = polygon_rings(poly)
        flat = np.vstack(rings)
        layers = []
        for k in range(slices+1):
            t = k/slices
            angle = math.radians(twist*t)
            x, y = flat[:, 0]*(1 + (sx-1)*t), flat[:, 1]*(1 + (sy-1)*t)
            c, s = math.cos(angle), math.sin(angle)
            layers.append(np.column_stack([c*x - s*y, s*x + c*y, np.full(len(flat), z0 + height*t)]))
        parts.append(_checked(stack_solid(rings, layers), "Twisted/scaled extrusion"))
    return trimesh.util.concatenate(parts)


def revolve(geom, node):
    if not isinstance(geom, Polygon) or len(geom.interiors) or geom.bounds[0] < 0:
        raise CADError("Revolve profile must be one hole-free polygon with X >= 0 (X=radius, Y=Z)",
                       hint="keep the profile on the X >= 0 side; subtract holes from the solid afterwards")
    angle = node.get("angle", 360)
    segments = node.get("segments", 96)
    if angle >= 360:
        return trimesh.creation.revolve(np.asarray(orient(geom, sign=1).exterior.coords), sections=segments)
    rings = polygon_rings(geom)
    flat = np.vstack(rings)
    steps = max(1, math.ceil(segments*angle/360))
    layers = []
    for k in range(steps+1):
        theta = math.radians(angle*k/steps)
        layers.append(np.column_stack([flat[:, 0]*math.cos(theta), flat[:, 0]*math.sin(theta), flat[:, 1]]))
    return _checked(stack_solid(rings, layers), "Partial revolve")


# ---- sweeps ---------------------------------------------------------------------------

def _unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v/np.where(n == 0, 1, n)


def _rotate_about(vectors, axes, angles):
    """Rodrigues rotation of each vector about its axis (unit) by its angle."""
    c, s = np.cos(angles)[:, None], np.sin(angles)[:, None]
    dot = np.einsum("ij,ij->i", axes, vectors)[:, None]
    return vectors*c + np.cross(axes, vectors)*s + axes*dot*(1-c)


def path_points(node):
    if ("points" in node) == ("curve" in node):
        raise CADError("sweep needs exactly one of points or curve")
    closed = node.get("closed", False)
    if "points" in node:
        points = np.asarray(node["points"], dtype=float)
    else:
        curve = node["curve"]
        start, end = curve.get("t", [0, math.tau])
        t = np.linspace(start, end, int(curve.get("samples", 200)), endpoint=not closed)
        points = np.column_stack([Field(curve[axis], ("t",))(t) for axis in "xyz"])
    keep = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-9]
    points = points[keep]
    if closed and len(points) > 2 and np.linalg.norm(points[-1]-points[0]) <= 1e-9:
        points = points[:-1]
    if len(points) < (3 if closed else 2):
        raise CADError("sweep path needs at least two distinct points (three for a closed path)")
    return points, closed


def sweep_frames(points, closed, frame, up, twist):
    """Tangents and profile axes (X, Y) at each path point.

    "rmf" carries the frame with the double reflection method of Wang, Juttler, Zheng and
    Liu, "Computation of Rotation Minimizing Frames", ACM Trans. Graph. 27(1), 2008. On a
    closed path the leftover rotation after one lap is spread evenly so the ends meet.
    "up" keeps Y pointing as close to ``up`` as the path allows.
    """
    n = len(points)
    if closed:
        tangents = _unit(np.roll(points, -1, axis=0) - np.roll(points, 1, axis=0))
    else:
        tangents = _unit(np.gradient(points, axis=0))
    up = _unit(np.asarray(up, dtype=float))
    if frame == "up":
        y = up - np.einsum("ij,j->i", tangents, up)[:, None]*tangents
        if (np.linalg.norm(y, axis=1) < 1e-6).any():
            raise CADError("The sweep path runs parallel to the up vector", hint="choose a different up, or frame rmf")
        y = _unit(y)
        x = _unit(np.cross(tangents, y))
    else:
        y0 = up - np.dot(tangents[0], up)*tangents[0]
        if np.linalg.norm(y0) < 1e-6:
            y0 = np.cross(tangents[0], [1.0, 0, 0] if abs(tangents[0][0]) < 0.9 else [0, 1.0, 0])
        x = np.empty_like(points)
        x[0] = _unit(np.cross(tangents[0], _unit(y0)))
        steps = n if closed else n-1
        r = x[0]
        for i in range(steps):
            j = (i+1) % n
            v1 = points[j]-points[i]
            c1 = v1 @ v1
            r_l = r - (2/c1)*(v1 @ r)*v1
            t_l = tangents[i] - (2/c1)*(v1 @ tangents[i])*v1
            v2 = tangents[j]-t_l
            c2 = v2 @ v2
            r = r_l - (2/c2)*(v2 @ r_l)*v2 if c2 > 1e-24 else r_l
            r = _unit(r - (r @ tangents[j])*tangents[j])
            if j:
                x[j] = r
        if closed:
            # Angle from the transported frame back to the start, about the start tangent.
            gap = math.atan2(np.cross(r, x[0]) @ tangents[0], r @ x[0])
            x = _rotate_about(x, tangents, gap*np.arange(n)/n)
        y = _unit(np.cross(x, tangents))
    if twist:
        angles = np.radians(twist)*np.arange(n)/(n if closed else n-1)
        x, y = _rotate_about(x, tangents, angles), _rotate_about(y, tangents, angles)
    return tangents, x, y


def sweep(geom, node):
    points, closed = path_points(node)
    scale = node.get("scale", 1)
    if closed and scale != 1:
        raise CADError("A closed sweep cannot change scale along the path")
    _, x_axes, y_axes = sweep_frames(points, closed, node.get("frame", "rmf"), node.get("up", [0, 0, 1]),
                                     node.get("twist", 0))
    n = len(points)
    factors = 1 + (scale-1)*np.arange(n)/max(1, n-1)
    parts = []
    for poly in polygons(geom):
        rings = polygon_rings(poly)
        flat = np.vstack(rings)
        layers = [points[k] + factors[k]*(flat[:, :1]*x_axes[k] + flat[:, 1:2]*y_axes[k]) for k in range(n)]
        parts.append(_checked(stack_solid(rings, layers, closed=closed), "Sweep"))
    return trimesh.util.concatenate(parts)


# ---- fields ---------------------------------------------------------------------------

def implicit(node):
    import manifold3d
    lo, hi = (np.asarray(b, dtype=float) for b in node["bounds"])
    if not (hi > lo).all():
        raise CADError("implicit bounds must be [[xmin, ymin, zmin], [xmax, ymax, zmax]] with max > min")
    resolution = node.get("resolution") or float((hi-lo).max())/80
    cells = float(np.prod(np.ceil((hi-lo)/resolution)))
    if cells > MAX_IMPLICIT_CELLS:
        suggested = float(((np.prod(hi-lo))/MAX_IMPLICIT_CELLS)**(1/3))
        raise CADError(f"implicit grid has {cells:,.0f} cells (limit {MAX_IMPLICIT_CELLS:,})",
                       hint=f"use resolution {suggested:.3g} or more, or smaller bounds")
    field = Field(node["f"], ("x", "y", "z"))
    level = float(node.get("level", 0))
    # Check the field on a coarse grid with numpy first: errors come out as a clear message
    # instead of from inside the mesher's callback.
    probe = [np.linspace(lo[k], hi[k], 9) for k in range(3)]
    field(*np.meshgrid(*probe, indexing="ij"))
    scalar = field.scalar()
    problems = []

    def inside(x, y, z):  # Manifold's level set is positive inside
        try:
            value = scalar(x, y, z)
            if value != value or value in (math.inf, -math.inf):
                raise ValueError("not finite")
            return level - value
        except Exception as exc:  # noqa: BLE001  never raise through the C++ caller
            if not problems:
                problems.append((x, y, z, exc))
            return -1.0
    result = manifold3d.Manifold.level_set(inside, [*lo, *hi], resolution, 0.0)
    if problems:
        x, y, z, exc = problems[0]
        raise CADError(f"implicit field failed at ({x:.4g}, {y:.4g}, {z:.4g}): {exc}")
    mesh = result.to_mesh()
    if len(mesh.tri_verts) == 0:
        raise CADError("implicit field is not negative anywhere inside bounds", hint="f < 0 marks the inside")
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=mesh.tri_verts, process=True)


def graph(node):
    (x0, x1), (y0, y1) = node["x"], node["y"]
    if not (x1 > x0 and y1 > y0):
        raise CADError("graph x and y ranges must increase")
    nx, ny = node.get("samples", [128, 128])
    if nx*ny > MAX_GRAPH_SAMPLES:
        raise CADError(f"graph grid has {nx*ny:,} points (limit {MAX_GRAPH_SAMPLES:,})",
                       hint="fewer samples; 2048 x 2048 is the most a graph can take")
    xs, ys = np.linspace(x0, x1, nx), np.linspace(y0, y1, ny)
    xx, yy = np.meshgrid(xs, ys)
    z = Field(node["f"], ("x", "y"))(xx, yy)
    base = node.get("base", 0)
    low = np.argwhere(z <= base)
    if len(low):
        j, i = low[0]
        raise CADError(f"graph reaches the base z={base:g} at x={xs[i]:.4g}, y={ys[j]:.4g} (f={z[j, i]:.4g})",
                       hint="lower base or lift f so the surface stays above the flat bottom")
    from .mesh import grid_solid
    return grid_solid(xs, ys, z, base)


# ---- primitives -----------------------------------------------------------------------

def polyhedron(node):
    points = np.asarray(node["points"], dtype=float)
    if "faces" not in node:
        if np.linalg.matrix_rank(points - points[0]) < 3:
            raise CADError("polyhedron points are flat; a hull needs points spread in 3D")
        return trimesh.convex.convex_hull(points)
    triangles = []
    for face in node["faces"]:
        if max(face) >= len(points):
            raise CADError(f"polyhedron face {face} refers to a point that does not exist")
        triangles.extend([face[0], face[k], face[k+1]] for k in range(1, len(face)-1))
    mesh = trimesh.Trimesh(vertices=points, faces=triangles, process=True)
    trimesh.repair.fix_normals(mesh)
    return mesh


def cone(node):
    r, h, top = node["radius"], node["height"], node.get("radius_top", 0)
    profile = [[0, 0], [r, 0], [top, h], [0, h]] if top > 0 else [[0, 0], [r, 0], [0, h]]
    mesh = trimesh.creation.revolve(np.asarray(orient(Polygon(profile), sign=1).exterior.coords),
                                    sections=node.get("segments", 96))
    mesh.apply_translation([0, 0, -h/2])
    return mesh


def torus(node):
    major, minor = node["major"], node["minor"]
    if minor >= major:
        raise CADError("torus minor radius must be less than the major radius")
    n = node.get("minor_segments", 48)
    angles = np.arange(n)*math.tau/n
    ring = Polygon(np.column_stack([major + minor*np.cos(angles), minor*np.sin(angles)]))
    return trimesh.creation.revolve(np.asarray(orient(ring, sign=1).exterior.coords), sections=node.get("segments", 96))


def mirror_matrix(normal, origin):
    n = np.asarray(normal, dtype=float)
    length = np.linalg.norm(n)
    if not length > 0:
        raise CADError("mirror normal must be nonzero")
    n = n/length
    matrix = np.eye(4)
    matrix[:3, :3] -= 2*np.outer(n, n)
    matrix[:3, 3] = 2*np.dot(np.asarray(origin, dtype=float), n)*n
    return matrix
