"""Planar contours, drawing imports, and structural profiles."""
import math

import numpy as np
from shapely import affinity, make_valid
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, LineString, Point, box
from shapely.ops import unary_union

from ._common import CADError


def polygonal(geom):
    if not geom.is_valid:
        raise CADError("Invalid 2D geometry; fix self intersections or hole boundaries")
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        polys = [p for p in geom.geoms if isinstance(p, Polygon) and p.area > 0]
    else:
        polys = []
    if not polys or sum(p.area for p in polys) <= 1e-12:
        raise CADError("Profile is empty or has zero area")
    return unary_union(polys)


def polygons(geom):
    return [geom] if isinstance(geom, Polygon) else list(geom.geoms)


def loops_fill(loops, rule="evenodd"):
    """Polygonize nested contours with even-odd or true winding-number fill."""
    from shapely.ops import polygonize
    rings = []
    for pts in loops:
        xy = np.asarray(pts, dtype=float)
        tolerance = max(float(np.ptp(xy, axis=0).max())*1e-12, 1e-12)
        # SVG curves often finish a few ulps away from their start. Do not let
        # the tiny closing edge turn into collapsed triangles after triangulation.
        keep = np.r_[True, np.linalg.norm(np.diff(xy, axis=0), axis=1) > tolerance]
        xy = xy[keep]
        while len(xy) > 1 and np.linalg.norm(xy[-1]-xy[0]) <= tolerance:
            xy = xy[:-1]
        if len(xy) < 3:
            raise CADError("Closed contour has fewer than three distinct points")
        p = Polygon(xy).simplify(tolerance, preserve_topology=True)
        if not p.is_valid or p.area <= 1e-12:
            raise CADError("Input contains a degenerate or self-intersecting closed contour")
        xy = np.asarray(p.exterior.coords)
        signed = np.sum(xy[:-1, 0]*xy[1:, 1] - xy[1:, 0]*xy[:-1, 1])
        rings.append((p, 1 if signed > 0 else -1))
    if not rings:
        raise CADError("No closed contours found")
    regions = polygonize(unary_union([p.boundary for p, _ in rings]))
    selected = []
    for region in regions:
        test = region.representative_point()
        winding = sum(sign if rule == "nonzero" else 1 for p, sign in rings if p.covers(test))
        if (winding != 0 if rule == "nonzero" else winding % 2):
            selected.append(region)
    return polygonal(unary_union(selected))


def svg_path_geometry(path, step=0.5, width=None, rule="evenodd"):
    from svgelements import Close, Move
    loops, strokes = [], []
    for sub in path.as_subpaths():
        points = []
        closed = False
        for seg in sub:
            if isinstance(seg, Move):
                points.append([seg.end.x, seg.end.y])
                continue
            length = seg.length(error=1e-5)
            count = max(1, math.ceil(length / step))
            if count > 200000:
                raise CADError("SVG segment exceeds sampling limit; increase step")
            if not points:
                points.append([seg.start.x, seg.start.y])
            for i in range(1, count + 1):
                p = seg.point(i/count)
                points.append([p.x, p.y])
            closed = closed or isinstance(seg, Close)
        if len(points) < 2:
            continue
        if width is not None:
            strokes.append(LineString(points).buffer(width / 2, quad_segs=16))
        elif closed or np.allclose(points[0], points[-1], atol=1e-9, rtol=0):
            loops.append(points)
        else:
            raise CADError("Open SVG path; close it with Z or use a path profile with width")
    return polygonal(unary_union(strokes)) if width is not None else loops_fill(loops, rule)


def read_svg(file, node):
    from svgelements import SVG, Shape, Path as SVGPath
    svg = SVG.parse(str(file), reify=True, ppi=96)
    selected = set(node.get("ids", []))
    found, geometries = set(), []
    for element in svg.elements():
        if not isinstance(element, Shape):
            continue
        eid = element.values.get("id")
        if selected and eid not in selected:
            continue
        # Hidden and unfilled shapes aren't treated as filled CAD outlines.
        if element.values.get("visibility") == "hidden" or element.values.get("display") == "none":
            continue
        if getattr(element.fill, "value", None) is None:
            continue
        if eid:
            found.add(eid)
        rule = node.get("fill_rule", element.values.get("fill-rule", "nonzero"))
        if rule not in ("evenodd", "nonzero"):
            raise CADError(f"Unsupported SVG fill rule: {rule}")
        geometries.append(svg_path_geometry(SVGPath(element), node.get("step", .5), rule=rule))
    if selected - found:
        raise CADError(f"SVG filled shape IDs not found: {sorted(selected-found)}")
    geom = polygonal(unary_union(geometries))
    if node.get("flip_y", True):
        geom = affinity.scale(geom, yfact=-1, origin=(0, 0))
    return geom


def read_dxf(file, node):
    import ezdxf
    from ezdxf.path import make_path
    from shapely.ops import polygonize_full
    doc = ezdxf.readfile(str(file))
    layer_filter = set(node.get("layers", []))
    lines = []
    allowed = {"LWPOLYLINE", "POLYLINE", "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
    for entity in doc.modelspace():
        if layer_filter and entity.dxf.layer not in layer_filter:
            continue
        if entity.dxftype() not in allowed:
            raise CADError(f"Unsupported DXF entity {entity.dxftype()}; select contour layers or explode it first")
        pts = np.asarray([tuple(p) for p in make_path(entity).flattening(node.get("tolerance", .05))])
        if len(pts) < 2:
            continue
        if np.max(np.abs(pts[:, 2])) > 1e-7:
            raise CADError("DXF must lie in the XY plane at Z=0")
        lines.append(LineString(pts[:, :2]))
    if not lines:
        raise CADError("No supported DXF contours in selected layers")
    # polygonize accepts line chains; reject dangling/open geometry instead of losing it.
    result, cuts, dangles, invalid = polygonize_full(unary_union(lines))
    if any(not g.is_empty for g in (cuts, dangles, invalid)):
        raise CADError("DXF has open, dangling or invalid contour segments")
    rings = {}
    for p in result.geoms:
        for ring in [p.exterior, *p.interiors]:
            poly = Polygon(ring)
            rings[poly.normalize().wkb_hex] = list(ring.coords)
    return loops_fill(list(rings.values()))


def arch_opening(node):
    span = node["span"]
    rise, leg = node.get("rise", span/2), node.get("leg", 0)
    angles = np.unique(np.r_[np.linspace(0, np.pi, int(node.get("segments", 64))+1), np.pi/2])[::-1]
    curve = np.column_stack([span/2*np.cos(angles), leg+rise*np.sin(angles)])
    curve[0], curve[-1] = [-span/2, leg], [span/2, leg]
    return polygonal(Polygon([[-span/2, 0], *curve.tolist(), [span/2, 0]]))


def arch_profile(node):
    opening = arch_opening(node)
    t = node["thickness"]
    # Explicit outer ellipse gives predictable overall dimensions. Thickness
    # adds to the horizontal/vertical radii; elliptical normal thickness varies.
    outer = arch_opening({**node, "span": node["span"]+2*t,
                          "rise": node.get("rise", node["span"]/2)+t})
    return polygonal(outer.difference(opening))


def frame_profile(size, border):
    w, h = size
    if border*2 >= min(w, h):
        raise CADError("Frame border must be less than half its smaller dimension")
    return box(-w/2, -h/2, w/2, h/2).difference(box(-w/2+border, -h/2+border, w/2-border, h/2-border))


def slot_profile(size, center=(0, 0)):
    w, h = size
    r = min(w, h)/2
    a, b = ([-w/2+r, 0], [w/2-r, 0]) if w >= h else ([0, -h/2+r], [0, h/2-r])
    geom = Point(0, 0).buffer(r, quad_segs=24) if w == h else LineString([a, b]).buffer(r, quad_segs=24)
    return affinity.translate(geom, *center)


def panel_cutouts(geom, cutouts):
    holes = []
    for cut in cutouts:
        shape = cut["shape"]
        if shape == "rectangle":
            if "size" not in cut:
                raise CADError("Rectangle cutout requires size")
            w, h = cut["size"]
            r = cut.get("radius", 0)
            if r > min(w, h)/2:
                raise CADError("Cutout radius is too large")
            hole = slot_profile([w, h]) if r == min(w, h)/2 else box(-w/2+r, -h/2+r, w/2-r, h/2-r).buffer(r) if r else box(-w/2, -h/2, w/2, h/2)
        elif shape == "circle":
            if "radius" not in cut:
                raise CADError("Circle cutout requires radius")
            hole = Point(0, 0).buffer(cut["radius"], quad_segs=24)
        elif shape == "slot":
            if "size" not in cut:
                raise CADError("Slot cutout requires size")
            hole = slot_profile(cut["size"])
        else:
            if "span" not in cut:
                raise CADError("Arch cutout requires span")
            hole = arch_opening(cut)
            h = cut.get("leg", 0)+cut.get("rise", cut["span"]/2)
            hole = affinity.translate(hole, yoff=-h/2)
        hole = affinity.rotate(hole, cut.get("rotate", 0), origin=(0, 0))
        holes.append(affinity.translate(hole, *cut.get("center", [0, 0])))
    return polygonal(geom.difference(unary_union(holes))) if holes else polygonal(geom)
