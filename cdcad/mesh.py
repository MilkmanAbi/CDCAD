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


def image_values(file, limit):
    """Read a picture as floats in [0, 1], keeping 16-bit and float precision.

    The longer side is resampled to at most ``limit`` pixels. Rows run top to bottom,
    as the picture is stored.
    """
    from PIL import Image
    with Image.open(file) as im:
        im.load()
        mode = im.mode
        if mode in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
            values = np.asarray(im, dtype=np.float64)
            if values.min() < 0 or values.max() > 65535:
                raise CADError(f"{file.name}: 32-bit integer images must hold values 0..65535")
            values = values / 65535.0
        elif mode == "F":
            values = np.asarray(im, dtype=np.float64)
        else:
            # RGB and palette images use their luminance; any alpha channel is ignored.
            values = np.asarray(im.convert("L"), dtype=np.float64) / 255.0
    if values.ndim != 2 or min(values.shape) < 2:
        raise CADError("Heightfield images must be at least 2x2 pixels")
    if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1:
        raise CADError("Floating-point heightfield images must hold finite values in [0,1]")
    rows, cols = values.shape
    scale = min(1.0, limit/max(rows, cols))
    if scale < 1.0:
        size = (max(2, round(cols*scale)), max(2, round(rows*scale)))
        resampled = Image.fromarray(values.astype(np.float32)).resize(size, Image.Resampling.LANCZOS)
        # Lanczos rings slightly past the input range at sharp edges.
        values = np.clip(np.asarray(resampled, dtype=np.float64), 0.0, 1.0)
    return values


def grid_solid(x, y, top_z, bottom_z):
    """Closed solid between a top surface z = top_z[j, i] over the grid (x[i], y[j]) and a flat
    bottom at bottom_z. Faces are built with numpy, so large grids stay fast."""
    rows, cols = top_z.shape
    xx, yy = np.meshgrid(x, y)
    top = np.column_stack([xx.ravel(), yy.ravel(), top_z.ravel()])
    bottom = top.copy()
    bottom[:, 2] = bottom_z
    n = len(top)
    j, i = np.meshgrid(np.arange(rows-1), np.arange(cols-1), indexing="ij")
    a = (j*cols+i).ravel()
    b, c, d = a+1, a+cols, a+cols+1
    upper = np.concatenate([np.column_stack([a, b, d]), np.column_stack([a, d, c])])
    lower = np.concatenate([np.column_stack([a, d, b]), np.column_stack([a, c, d])]) + n
    border = np.r_[np.arange(cols), np.arange(1, rows)*cols+cols-1,
                   np.arange((rows-1)*cols+cols-2, (rows-1)*cols-1, -1), np.arange(rows-2, 0, -1)*cols]
    nxt = np.roll(border, -1)
    sides = np.concatenate([np.column_stack([border, border+n, nxt+n]), np.column_stack([border, nxt+n, nxt])])
    faces = np.concatenate([upper, lower, sides])
    # Every vertex is distinct by construction (the top stays above the bottom), so there is
    # nothing for trimesh's vertex merging to do.
    return trimesh.Trimesh(vertices=np.vstack([top, bottom]), faces=faces, process=False)


def heightfield(file, node):
    """Relief from a picture or a 2D .npy array of values in [0, 1].

    Pictures keep the orientation you see on screen: their top row is at the high-Y edge.
    Arrays follow numpy's meshgrid convention instead: row 0 is the low-Y edge. ``flip_y``
    overrides either default.
    """
    limit = node.get("max_samples", 256)
    is_array = file.suffix.lower() == ".npy"
    if is_array:
        values = np.load(file, allow_pickle=False)
        if values.ndim != 2 or min(values.shape) < 2:
            raise CADError("Heightfield .npy must be a 2D array at least 2x2")
        if not np.issubdtype(values.dtype, np.number) or np.issubdtype(values.dtype, np.complexfloating):
            raise CADError("Heightfield .npy must hold real numbers in [0,1]")
        stride = max(1, math.ceil((max(values.shape)-1)/(limit-1)))
        iy = np.unique(np.r_[np.arange(0, values.shape[0], stride), values.shape[0]-1])
        ix = np.unique(np.r_[np.arange(0, values.shape[1], stride), values.shape[1]-1])
        x_fraction = ix/(values.shape[1]-1)
        y_fraction = iy/(values.shape[0]-1)
        values = values[np.ix_(iy, ix)].astype(float)
    else:
        values = image_values(file, limit)
        x_fraction = np.linspace(0, 1, values.shape[1])
        y_fraction = np.linspace(0, 1, values.shape[0])
    if min(values.shape) < 2 or not np.isfinite(values).all() or values.min() < 0 or values.max() > 1:
        raise CADError("Heightfield samples must be finite values in [0,1], at least 2x2")
    if node.get("flip_y", not is_array):
        values = values[::-1]
        y_fraction = (1-y_fraction)[::-1]
    if node.get("invert", False):
        values = 1-values
    top = node.get("base", 1)+values*node["height"]
    mesh = grid_solid(x_fraction*node["size"][0], y_fraction*node["size"][1], top, 0.0)
    return valid_mesh(mesh)


MESH_INPUTS = (".stl", ".obj", ".ply", ".off", ".glb", ".3mf")

# 3MF is a zip of XML parts. Only the core specification's geometry is read: objects with
# meshes or components, build items and their transforms, and the model unit. Colours,
# materials and slicer settings are ignored. The production extension's p:path lets a
# component point into another model part, which Bambu Studio and PrusaSlicer use.
NS_3MF = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_3MF_PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL_3MF_MODEL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
UNITS_3MF = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}
MAX_3MF_PART = 1 << 30


def _matrix_3mf(text):
    """3MF transforms are 12 numbers for row vectors: [x y z 1] times a 4x3 matrix."""
    if not text:
        return np.eye(4)
    values = [float(v) for v in text.split()]
    if len(values) != 12 or not np.isfinite(values).all():
        raise CADError(f"Invalid 3MF transform: {text!r}")
    m = np.asarray(values).reshape(4, 3)
    matrix = np.eye(4)
    matrix[:3, :3] = m[:3].T
    matrix[:3, 3] = m[3]
    return matrix


def read_3mf(file):
    """Read a .3mf path or file object into one mesh in millimetres."""
    import xml.etree.ElementTree as ET
    import zipfile

    label = getattr(file, "name", "3MF data")
    try:
        archive = zipfile.ZipFile(file)
    except zipfile.BadZipFile as exc:
        raise CADError(f"{label} is not a 3MF (zip) package") from exc
    with archive:
        names = {name.lower(): name for name in archive.namelist()}

        def part(name):
            key = name.lstrip("/").lower()
            if key not in names:
                raise CADError(f"3MF package has no part {name}")
            info = archive.getinfo(names[key])
            if info.file_size > MAX_3MF_PART:
                raise CADError(f"3MF part {name} is larger than 1 GiB")
            return ET.fromstring(archive.read(info))

        main = None
        if "_rels/.rels" in names:
            for rel in part("_rels/.rels"):
                if rel.get("Type") == REL_3MF_MODEL:
                    main = rel.get("Target")
        main = (main or "3D/3dmodel.model").lstrip("/")
        models, objects = {}, {}

        def model(name):
            name = name.lstrip("/")
            if name not in models:
                root = part(name)
                models[name] = root
                for obj in root.iter(f"{{{NS_3MF}}}object"):
                    objects[(name.lower(), obj.get("id"))] = obj
            return models[name]

        root = model(main)
        unit = root.get("unit", "millimeter")
        if unit not in UNITS_3MF:
            raise CADError(f"Unsupported 3MF unit: {unit}")
        pieces = []

        def place(part_name, object_id, matrix, depth):
            if depth > 32:
                raise CADError("3MF components nest too deeply (cycle?)")
            model(part_name)
            obj = objects.get((part_name.lower(), object_id))
            if obj is None:
                raise CADError(f"3MF build references missing object {object_id}")
            mesh = obj.find(f"{{{NS_3MF}}}mesh")
            if mesh is not None:
                vertices = np.array([[float(v.get("x")), float(v.get("y")), float(v.get("z"))]
                                     for v in mesh.iter(f"{{{NS_3MF}}}vertex")], dtype=float).reshape(-1, 3)
                faces = np.array([[int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))]
                                  for t in mesh.iter(f"{{{NS_3MF}}}triangle")], dtype=np.int64).reshape(-1, 3)
                if len(faces) and (faces.min() < 0 or faces.max() >= len(vertices)):
                    raise CADError(f"3MF object {object_id} has triangle indices out of range")
                pieces.append(trimesh.Trimesh(vertices=vertices, faces=faces, process=False).apply_transform(matrix))
            components = obj.find(f"{{{NS_3MF}}}components")
            if components is not None:
                for component in components.findall(f"{{{NS_3MF}}}component"):
                    target = component.get(f"{{{NS_3MF_PRODUCTION}}}path") or part_name
                    place(target.lstrip("/"), component.get("objectid"),
                          matrix @ _matrix_3mf(component.get("transform")), depth+1)

        build = root.find(f"{{{NS_3MF}}}build")
        for item in [] if build is None else build.findall(f"{{{NS_3MF}}}item"):
            target = item.get(f"{{{NS_3MF_PRODUCTION}}}path") or main
            place(target.lstrip("/"), item.get("objectid"), _matrix_3mf(item.get("transform")), 0)
    if not pieces:
        raise CADError(f"{label} contains no build items with meshes")
    combined = trimesh.util.concatenate(pieces)
    combined.apply_scale(UNITS_3MF[unit])
    return trimesh.Trimesh(vertices=combined.vertices, faces=combined.faces, process=True)


def export_3mf(mesh):
    """A minimal core-specification 3MF in millimetres. Byte-identical for identical meshes."""
    import zipfile

    def fmt(v):
        return format(float(v), ".9g")
    vertices = "".join(f'<vertex x="{fmt(x)}" y="{fmt(y)}" z="{fmt(z)}"/>' for x, y, z in mesh.vertices)
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
    model = (f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" xmlns="{NS_3MF}">'
             f'<metadata name="Application">CDCAD</metadata><resources><object id="1" type="model"><mesh>'
             f"<vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object></resources>"
             f'<build><item objectid="1"/></build></model>\n')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="{REL_3MF_MODEL}"/></Relationships>\n')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in (("[Content_Types].xml", types), ("_rels/.rels", rels), ("3D/3dmodel.model", model)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text.encode("utf-8"))
    return buffer.getvalue()


def load_mesh(file):
    file = Path(file)
    if file.suffix.lower() not in MESH_INPUTS:
        raise CADError("Mesh input must be STL, OBJ, PLY, OFF, GLB or 3MF")
    if not file.is_file():
        raise CADError(f"Mesh file not found: {file}")
    if file.suffix.lower() == ".3mf":
        mesh = read_3mf(file)
    else:
        mesh = trimesh.load_scene(str(file), process=True).to_mesh()
    return valid_mesh(mesh, solid=False)


def mesh_report(mesh, density=None):
    """Topology and measurements. ``density`` in g/cm^3 adds the mass in grams (coordinates in mm)."""
    valid_mesh(mesh, solid=False)
    solid = bool(mesh.is_volume)
    report = {"vertices": len(mesh.vertices), "faces": len(mesh.faces),
              "watertight": bool(mesh.is_watertight), "winding_consistent": bool(mesh.is_winding_consistent),
              "is_volume": solid, "volume": float(mesh.volume), "area": float(mesh.area),
              "bounds": mesh.bounds.tolist(), "extents": mesh.extents.tolist(),
              "center_mass": mesh.center_mass.tolist() if solid else None,
              "components": len(mesh.split(only_watertight=False)), "euler_number": int(mesh.euler_number)}
    if density is not None:
        report["density"] = density
        report["mass_g"] = float(mesh.volume)*density/1000.0 if solid else None
    return report


def export_bytes(mesh, path, allow_open=False):
    valid_mesh(mesh, solid=not allow_open)
    suffix = Path(path).suffix.lower()
    if suffix[1:] not in EXPORT_FORMATS:
        raise CADError("Export extension must be one of " + ", ".join("." + f for f in EXPORT_FORMATS))
    if suffix == ".3mf":
        data = export_3mf(mesh)
    else:
        data = mesh.export(file_type=suffix[1:])
        data = data.encode() if isinstance(data, str) else data
    if suffix in (".stl", ".3mf") and not allow_open:
        if suffix == ".stl":
            reloaded = trimesh.load_mesh(io.BytesIO(data), file_type="stl", process=True)
        else:
            reloaded = read_3mf(io.BytesIO(data))
        if not reloaded.is_volume:
            raise CADError(f"Serialized {suffix[1:].upper()} is not solid after vertex merging/rounding; "
                           "adjust tangent contacts or tiny features, or explicitly allow open export")
    return data
