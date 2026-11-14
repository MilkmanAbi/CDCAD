"""Evaluate a recipe dependency graph and cache geometry by content."""
import functools
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import time

import numpy as np
import trimesh
from shapely import affinity
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union

from ._common import CADError, UNITS, atomic_bytes, dependency_versions, document_path
from ._profiles import (
    arch_opening, arch_profile, frame_profile, panel_cutouts, polygonal, polygons,
    read_dxf, read_svg, slot_profile, svg_path_geometry,
)
from .graph import validate_graph
from .mesh import (
    boolean, bounds_anchor, clean_mesh, cut_mesh, heightfield, load_mesh,
    loft, planar_solid, transform_mesh, valid_mesh,
)


@functools.lru_cache(maxsize=1)
def geometry_cache_salt():
    sources = Path(__file__).parent
    code = {name: hashlib.sha256((sources/name).read_bytes()).hexdigest()
            for name in ("engine.py", "_profiles.py", "mesh.py", "_common.py")}
    return {"code": code, "versions": dependency_versions(), "platform": [platform.system(), platform.machine()]}


class Engine:
    def __init__(self, recipe, base=None, cache_dir=None):
        self.recipe, self.base = recipe, Path(base) if base is not None else Path.cwd()
        self.memo, self.content_memo, self.trace = {}, {}, []
        self.unit = recipe.get("units", "mm")
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else None
        self.keys, self.file_hashes = {}, {}
        if self.cache_dir:
            self.cache_salt = geometry_cache_salt()
        validate_graph(recipe)

    def source(self, value):
        return (self.base / document_path(value)).resolve()

    def get(self, kind, name):
        key = (kind, name)
        if key in self.memo:
            return self.memo[key]
        start = time.perf_counter()
        node = self.recipe[kind][name]
        hit = False
        try:
            cache_file = None
            content_key = None
            value = None
            if self.cache_dir:
                content_key = self.node_key(kind, name)
                cache_file = self.cache_dir/(content_key + (".wkb" if kind == "profiles" else ".npz"))
                if content_key in self.content_memo:
                    value = self.content_memo[content_key]
                    hit = True
                elif cache_file.exists():
                    try:
                        if kind == "profiles":
                            from shapely import from_wkb
                            value = polygonal(from_wkb(cache_file.read_bytes()))
                        else:
                            with np.load(cache_file, allow_pickle=False) as archive:
                                value = valid_mesh(trimesh.Trimesh(vertices=archive["vertices"], faces=archive["faces"], process=False), solid=False)
                        hit = True
                    except Exception:
                        value = None  # Damaged cache entries are recomputed, never unpickled.
            if value is None:
                value = self.profile(node) if kind == "profiles" else self.solid(node)
                if cache_file:
                    if kind == "profiles":
                        data = value.wkb
                    else:
                        buffer = io.BytesIO()
                        np.savez_compressed(buffer, vertices=value.vertices, faces=value.faces)
                        data = buffer.getvalue()
                    atomic_bytes(cache_file, data)
        except Exception as exc:
            raise CADError(f"{kind}/{name} ({node['op']}): {exc}") from exc
        self.memo[key] = value
        if content_key:
            self.content_memo[content_key] = value
        self.trace.append({"kind": kind, "name": name, "op": node["op"],
                           "cache_hit": hit, "seconds": round(time.perf_counter()-start, 6)})
        return value

    def node_key(self, kind, name):
        key = (kind, name)
        if key in self.keys:
            return self.keys[key]
        node = self.recipe[kind][name]
        normalized = dict(node)
        for field in ("input", "target"):
            if field in node:
                normalized[field] = self.node_key(kind, node[field])
        if "inputs" in node:
            normalized["inputs"] = [self.node_key(kind, ref) for ref in node["inputs"]]
        if "profile" in node:
            normalized["profile"] = self.node_key("profiles", node["profile"])
        if "sections" in node:
            normalized["sections"] = [{**s, "profile": self.node_key("profiles", s["profile"])} for s in node["sections"]]
        content = {"kind": kind, "node": normalized, "units": self.unit, "runtime": self.cache_salt}
        if "path" in node:
            path = self.source(node["path"])
            if path not in self.file_hashes:
                with path.open("rb") as f:
                    self.file_hashes[path] = hashlib.file_digest(f, "sha256").hexdigest()
            content["source_hash"] = self.file_hashes[path]
            content["source_suffix"] = path.suffix.lower()
            normalized["path"] = "<source-bytes>"
        result = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        self.keys[key] = result
        return result

    def profile(self, n):
        op = n["op"]
        get = lambda name: self.get("profiles", name)
        if op == "arch_opening":
            geom = arch_opening(n)
        elif op == "arch":
            geom = arch_profile(n)
        elif op == "frame":
            geom = frame_profile(n["size"], n["border"])
        elif op == "slot":
            geom = slot_profile(n["size"], n.get("center", [0, 0]))
        elif op == "grid":
            if math.prod(n["counts"]) > 10000:
                raise CADError("Profile grid exceeds 10000 instances")
            source = get(n["input"])
            geom = unary_union([affinity.translate(source, i*n["step"][0], j*n["step"][1])
                                for i in range(n["counts"][0]) for j in range(n["counts"][1])])
        elif op == "rectangle":
            w, h = n["size"]
            cx, cy = n.get("center", [0, 0])
            r = n.get("radius", 0)
            if r > min(w, h)/2:
                raise CADError("Corner radius exceeds half the smaller dimension")
            if r:
                geom = box(cx-w/2+r, cy-h/2+r, cx+w/2-r, cy+h/2-r).buffer(r, quad_segs=16)
                # A zero-width inset box degenerates; use a line or point instead.
                if w == 2*r and h == 2*r:
                    geom = Point(cx, cy).buffer(r, quad_segs=16)
                elif w == 2*r or h == 2*r:
                    geom = LineString([(cx-w/2+r, cy-h/2+r), (cx+w/2-r, cy+h/2-r)]).buffer(r)
            else:
                geom = box(cx-w/2, cy-h/2, cx+w/2, cy+h/2)
        elif op in ("circle", "ellipse"):
            seg = n.get("segments", 96)
            angles = np.arange(seg) * (2*np.pi/seg)
            radii = [n["radius"]]*2 if op == "circle" else n["radii"]
            xy = np.column_stack([np.cos(angles), np.sin(angles)])*radii + n.get("center", [0, 0])
            geom = Polygon(xy)
        elif op == "polygon":
            geom = Polygon(n["points"], n.get("holes", []))
        elif op == "stroke":
            pts = list(n["points"])
            if n.get("closed"):
                pts.append(pts[0])
            geom = LineString(pts).buffer(n["width"]/2, cap_style=n.get("cap", "round"),
                                          join_style=n.get("join", "round"), quad_segs=16)
        elif op == "path":
            from svgelements import Path as SVGPath
            geom = svg_path_geometry(SVGPath(n["d"]), n.get("step", .5), n.get("width"), n.get("fill_rule", "evenodd"))
        elif op == "svg":
            geom = read_svg(self.source(n["path"]), n)
            factor = 25.4/96/UNITS[self.unit]
            geom = affinity.scale(geom, xfact=factor, yfact=factor, origin=(0, 0))
        elif op == "dxf":
            geom = read_dxf(self.source(n["path"]), n)
        elif op in ("union", "difference", "intersection"):
            shapes = [get(ref) for ref in n["inputs"]]
            geom = unary_union(shapes) if op == "union" else shapes[0]
            if op != "union":
                for shape in shapes[1:]:
                    geom = getattr(geom, op)(shape)
        elif op == "offset":
            geom = get(n["input"]).buffer(n["distance"], join_style=n.get("join", "round"), quad_segs=16)
        elif op == "transform":
            geom = get(n["input"])
            sx, sy = n.get("scale", [1, 1])
            if sx == 0 or sy == 0:
                raise CADError("Profile scale must be nonzero")
            geom = affinity.scale(geom, xfact=sx, yfact=sy, origin=(0, 0))
            geom = affinity.rotate(geom, n.get("rotate", 0), origin=(0, 0))
            tx, ty = n.get("translate", [0, 0])
            geom = affinity.translate(geom, tx, ty)
        else:
            raise CADError(f"Unknown profile operation: {op}")
        return polygonal(geom)

    def solid(self, n):
        op = n["op"]
        get = lambda name: self.get("solids", name)
        if op == "arch":
            mesh = planar_solid(arch_profile(n), n["depth"], "xz", n.get("origin", [0, 0, 0]))
        elif op == "wall":
            w, h = n["length"], n["height"]
            geom = panel_cutouts(box(-w/2, 0, w/2, h), n.get("cutouts", []))
            mesh = planar_solid(geom, n["thickness"], "xz", n.get("origin", [0, 0, 0]))
        elif op == "panel":
            geom = self.profile({"op": "rectangle", "size": n["size"], "radius": n.get("radius", 0)})
            mesh = planar_solid(panel_cutouts(geom, n.get("cutouts", [])), n["thickness"],
                                n.get("plane", "xy"), n.get("center", [0, 0, 0]))
        elif op == "frame":
            mesh = planar_solid(frame_profile(n["size"], n["border"]), n["depth"],
                                n.get("plane", "xz"), n.get("center", [0, 0, 0]))
        elif op == "beam":
            start, end = np.asarray(n["start"], dtype=float), np.asarray(n["end"], dtype=float)
            direction = end-start
            length = np.linalg.norm(direction)
            if length <= 1e-12:
                raise CADError("Beam start and end must differ")
            mesh = trimesh.creation.box([*n["size"], length])
            rotation = trimesh.geometry.align_vectors([0, 0, 1], direction/length)
            roll = trimesh.transformations.rotation_matrix(math.radians(n.get("roll", 0)), [0, 0, 1])
            mesh.apply_transform(rotation @ roll)
            mesh.apply_translation((start+end)/2)
        elif op == "align":
            mesh = get(n["input"]).copy()
            target = get(n["target"])
            shift = bounds_anchor(target, n.get("target_anchor", "top"))-bounds_anchor(mesh, n.get("anchor", "bottom"))
            mesh.apply_translation(shift + np.asarray(n.get("offset", [0, 0, 0])))
        elif op == "drill":
            tool = trimesh.creation.cylinder(n["radius"], n["depth"], sections=n.get("segments", 64))
            axis = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[n.get("axis", "z")]
            tool.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], axis))
            cutters = []
            for position in n["positions"]:
                cut = tool.copy()
                cut.apply_translation(position)
                cutters.append(cut)
            mesh = boolean("difference", [get(n["input"]), *cutters])
        elif op == "repeat":
            if math.prod(n["counts"]) > 10000:
                raise CADError("Repeat exceeds 10000 instances")
            source = get(n["input"])
            meshes = []
            for index in np.ndindex(*n["counts"]):
                part = source.copy()
                part.apply_translation(np.asarray(index)*n["step"])
                meshes.append(part)
            mesh = boolean("union", meshes) if n.get("fuse", True) else trimesh.util.concatenate(meshes)
        elif op == "extrude":
            geom = self.get("profiles", n["profile"])
            meshes = [trimesh.creation.extrude_polygon(p, n["height"], engine="earcut") for p in polygons(geom)]
            mesh = trimesh.util.concatenate(meshes)
            mesh.apply_translation([0, 0, n.get("z", 0)])
        elif op == "loft":
            mesh = loft([self.get("profiles", s["profile"]) for s in n["sections"]],
                        [s["z"] for s in n["sections"]], n.get("samples", 96))
        elif op == "revolve":
            geom = self.get("profiles", n["profile"])
            if not isinstance(geom, Polygon) or len(geom.interiors) or geom.bounds[0] < 0:
                raise CADError("Revolve profile must be one hole-free polygon with X >= 0 (X=radius, Y=Z)")
            from shapely.geometry.polygon import orient
            mesh = trimesh.creation.revolve(np.asarray(orient(geom, sign=1).exterior.coords), sections=n.get("segments", 96))
        elif op == "box":
            mesh = trimesh.creation.box(n["size"])
        elif op == "cylinder":
            mesh = trimesh.creation.cylinder(n["radius"], n["height"], sections=n.get("segments", 96))
        elif op == "sphere":
            mesh = trimesh.creation.icosphere(subdivisions=n.get("subdivisions", 3), radius=n["radius"])
        elif op == "import":
            mesh = load_mesh(self.source(n["path"]))
            mesh.apply_scale(UNITS[n.get("units", self.unit)]/UNITS[self.unit])
            if n.get("repair", False):
                mesh = clean_mesh(mesh)
        elif op == "heightfield":
            mesh = heightfield(self.source(n["path"]), n)
        elif op in ("union", "difference", "intersection", "combine"):
            meshes = [get(name) for name in n["inputs"]]
            mesh = trimesh.util.concatenate(meshes) if op == "combine" else boolean(op, meshes)
        elif op == "transform":
            mesh = transform_mesh(get(n["input"]), n)
        elif op == "array":
            source = get(n["input"])
            meshes = []
            for i in range(n["count"]):
                m = source.copy()
                m.apply_translation(np.asarray(n["step"])*i)
                meshes.append(m)
            mesh = boolean("union", meshes) if n.get("fuse", True) else trimesh.util.concatenate(meshes)
        elif op == "repair":
            mesh = clean_mesh(get(n["input"]), n.get("fill_holes", True))
        elif op == "hull":
            mesh = get(n["input"]).convex_hull
        elif op == "simplify":
            source = get(n["input"])
            mesh = source.copy() if n["faces"] >= len(source.faces) else source.simplify_quadric_decimation(face_count=n["faces"])
        elif op == "cut":
            mesh = cut_mesh(get(n["input"]), n["origin"], n["normal"], n.get("cap", True))
        else:
            raise CADError(f"Unknown solid operation: {op}")
        return valid_mesh(mesh, solid=False)
