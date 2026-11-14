"""Mesh I/O, analysis, transforms, and constructive geometry."""
import io
import math
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon

from ._common import CADError, EXPORT_FORMATS
from ._profiles import polygons


def clean_mesh(mesh, fill_holes=True):
    mesh = mesh.copy()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices()
    if fill_holes:
        trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_normals(mesh, multibody=True)
    return mesh


def valid_mesh(mesh, solid=True):
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise CADError("Mesh is empty")
    if not np.isfinite(mesh.vertices).all():
        raise CADError("Mesh contains non-finite vertices")
    if solid and not mesh.is_volume:
        raise CADError("Mesh is not a watertight, consistently wound positive volume; inspect/repair it first")
    return mesh


def boolean(op, meshes):
    for m in meshes:
        valid_mesh(m)
    if len(meshes) == 1:
        return meshes[0].copy()
    return valid_mesh(getattr(trimesh.boolean, op)(meshes, engine="manifold", check_volume=True))


def cut_mesh(mesh, origin, normal, cap=True):
    normal = np.asarray(normal, dtype=float)
    length = np.linalg.norm(normal)
    if not math.isfinite(length) or length == 0:
        raise CADError("Cut plane normal must be finite and nonzero")
    normal = normal/length
    if not cap:
        return valid_mesh(mesh.slice_plane(origin, normal, cap=False), solid=False)
    # Manifold trims closed solids directly, including hollow/disconnected caps.
    # A boundary-polygon cap can introduce T-junctions on nested contours.
    import manifold3d
    valid_mesh(mesh)
    source = manifold3d.Manifold(manifold3d.Mesh(
        vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
        tri_verts=np.asarray(mesh.faces, dtype=np.uint32)))
    if source.status() != manifold3d.Error.NoError:
        raise CADError(f"Manifold rejected cut input: {source.status()}")
    trimmed = source.trim_by_plane(normal.tolist(), float(np.dot(normal, origin)))
    result = trimmed.to_mesh()
    return valid_mesh(trimesh.Trimesh(vertices=result.vert_properties, faces=result.tri_verts, process=False))


def planar_solid(geom, depth, plane="xy", center=(0, 0, 0)):
    mesh = trimesh.util.concatenate([trimesh.creation.extrude_polygon(p, depth, engine="earcut") for p in polygons(geom)])
    mesh.apply_translation([0, 0, -depth/2])
    matrix = np.eye(4)
    if plane == "xz":
        matrix[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    elif plane == "yz":
        matrix[:3, :3] = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]
    matrix[:3, 3] = center
    mesh.apply_transform(matrix)
    return mesh


def bounds_anchor(mesh, name):
    anchor = mesh.bounds.mean(axis=0)
    faces = {"left": (0, 0), "right": (0, 1), "front": (1, 0),
             "back": (1, 1), "bottom": (2, 0), "top": (2, 1)}
    if name in faces:
        axis, end = faces[name]
        anchor[axis] = mesh.bounds[end, axis]
    return anchor


def transform_mesh(mesh, node):
    result = mesh.copy()
    if "matrix" in node:
        if any(k in node for k in ("scale", "rotate", "translate")):
            raise CADError("Use matrix OR scale/rotate/translate")
        matrix = np.asarray(node["matrix"], dtype=float)
        if not np.allclose(matrix[3], [0, 0, 0, 1]):
            raise CADError("Matrix must be an affine 4x4 transform")
    else:
        scale = np.diag([*node.get("scale", [1, 1, 1]), 1.0])
        angles = np.radians(node.get("rotate", [0, 0, 0]))
        rotation = trimesh.transformations.euler_matrix(*angles, axes="sxyz")
        translation = trimesh.transformations.translation_matrix(node.get("translate", [0, 0, 0]))
        matrix = translation @ rotation @ scale
    if abs(np.linalg.det(matrix[:3, :3])) < 1e-15:
        raise CADError("Transform must be invertible (nonzero scale)")
    result.apply_transform(matrix)
    return result


def loft(section_geoms, zs, samples):
    """Resample one outer ring per section; sew sides and triangulated end caps."""
    from shapely.geometry.polygon import orient
    if any(b <= a for a, b in zip(zs, zs[1:])):
        raise CADError("Loft section Z coordinates must be strictly increasing")
    rings = []
    for geom in section_geoms:
        if not isinstance(geom, Polygon) or len(geom.interiors):
            raise CADError("Loft requires one hole-free polygon per section; subtract holes afterward")
        boundary = orient(geom, sign=1).exterior
        ring = np.array([[boundary.interpolate(i / samples, normalized=True).x,
                          boundary.interpolate(i / samples, normalized=True).y] for i in range(samples)])
        # Align ring starts to avoid a twisted seam from different source vertex starts.
        if rings:
            costs = [np.sum((np.roll(ring, -k, axis=0) - rings[-1])**2) for k in range(samples)]
            ring = np.roll(ring, -int(np.argmin(costs)), axis=0)
        rings.append(ring)
    vertices = np.vstack([np.column_stack([ring, np.full(samples, z)]) for ring, z in zip(rings, zs)])
    faces = []
    for level in range(len(rings)-1):
        a, b = level*samples, (level+1)*samples
        for i in range(samples):
            j = (i+1) % samples
            faces.extend([[a+i, a+j, b+j], [a+i, b+j, b+i]])
    for level, reverse in [(0, True), (len(rings)-1, False)]:
        v, f = trimesh.creation.triangulate_polygon(Polygon(rings[level]), engine="earcut")
        lookup = {tuple(p): i for i, p in enumerate(rings[level])}
        mapped = np.array([lookup[tuple(p)] + level*samples for p in v])[f]
        faces.extend(mapped[:, ::-1] if reverse else mapped)
    return valid_mesh(trimesh.Trimesh(vertices=vertices, faces=faces, process=True))


def heightfield(file, node):
    from PIL import Image
    limit = node.get("max_samples", 256)
    if file.suffix.lower() == ".npy":
        values = np.load(file, allow_pickle=False)
        if values.ndim != 2 or min(values.shape) < 2:
            raise CADError("Heightfield .npy must be a 2D array at least 2x2")
        stride = max(1, math.ceil((max(values.shape)-1)/(limit-1)))
        iy = np.unique(np.r_[np.arange(0, values.shape[0], stride), values.shape[0]-1])
        ix = np.unique(np.r_[np.arange(0, values.shape[1], stride), values.shape[1]-1])
        x_fraction = ix/(values.shape[1]-1)
        y_fraction = iy/(values.shape[0]-1)
        values = values[np.ix_(iy, ix)].astype(float)
    else:
        with Image.open(file) as im:
            im = im.convert("L")
            im.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            values = np.asarray(im, dtype=float) / 255.0
        x_fraction = np.linspace(0, 1, values.shape[1])
        y_fraction = np.linspace(0, 1, values.shape[0])
    if min(values.shape) < 2 or not np.isfinite(values).all() or values.min() < 0 or values.max() > 1:
        raise CADError("Heightfield samples must be finite values in [0,1], at least 2x2")
    if node.get("invert", False):
        values = 1-values
    rows, cols = values.shape
    x = x_fraction*node["size"][0]
    y = y_fraction*node["size"][1]
    xx, yy = np.meshgrid(x, y)
    top = np.column_stack([xx.ravel(), yy.ravel(), (node.get("base", 1)+values*node["height"]).ravel()])
    bottom = top.copy()
    bottom[:, 2] = 0
    n = len(top)
    faces = []
    for j in range(rows-1):
        for i in range(cols-1):
            a, b = j*cols+i, j*cols+i+1
            c, d = (j+1)*cols+i, (j+1)*cols+i+1
            faces.extend([[a, b, d], [a, d, c], [n+a, n+d, n+b], [n+a, n+c, n+d]])
    border = [*range(cols), *[j*cols+cols-1 for j in range(1, rows)],
              *range((rows-1)*cols+cols-2, (rows-1)*cols-1, -1),
              *[j*cols for j in range(rows-2, 0, -1)]]
    for a, b in zip(border, border[1:]+border[:1]):
        faces.extend([[a, n+a, n+b], [a, n+b, b]])
    return valid_mesh(trimesh.Trimesh(vertices=np.vstack([top, bottom]), faces=faces, process=True))


def load_mesh(file):
    if file.suffix.lower() not in {".stl", ".obj", ".ply", ".off", ".glb", ".3mf"}:
        raise CADError("Mesh input must be STL, OBJ, PLY, OFF, GLB or 3MF")
    mesh = trimesh.load_scene(str(file), process=True).to_mesh()
    return valid_mesh(mesh, solid=False)


def mesh_report(mesh):
    valid_mesh(mesh, solid=False)
    return {"vertices": len(mesh.vertices), "faces": len(mesh.faces),
            "watertight": bool(mesh.is_watertight), "winding_consistent": bool(mesh.is_winding_consistent),
            "is_volume": bool(mesh.is_volume), "volume": float(mesh.volume), "area": float(mesh.area),
            "bounds": mesh.bounds.tolist(), "extents": mesh.extents.tolist(),
            "components": len(mesh.split(only_watertight=False)), "euler_number": int(mesh.euler_number)}


def export_bytes(mesh, path, allow_open=False):
    valid_mesh(mesh, solid=not allow_open)
    suffix = Path(path).suffix.lower()
    if suffix[1:] not in EXPORT_FORMATS:
        raise CADError("Export extension must be .stl, .ply, .obj, .glb or .off")
    data = mesh.export(file_type=suffix[1:])
    data = data.encode() if isinstance(data, str) else data
    if suffix == ".stl" and not allow_open:
        reloaded = trimesh.load_mesh(io.BytesIO(data), file_type="stl", process=True)
        if not reloaded.is_volume:
            raise CADError("Serialized STL is not solid after vertex merging/float32 rounding; "
                           "adjust tangent contacts or tiny features, or explicitly allow open export")
    return data
