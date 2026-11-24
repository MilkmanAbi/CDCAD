"""STEP (ISO 10303-21, AP214) export of a mesh as a faceted boundary representation.

CDCAD works on triangle meshes, so the STEP file holds planar faces, not curved surfaces:
each face is a PLANE bounded by POLY_LOOPs inside a FACETED_BREP. Coplanar neighbouring
triangles are merged into one polygon face (with holes where needed), so a box comes out
as six faces and a plate with a hole as a handful, which is what CAD programs show as
faces you can pick and measure. Curved areas stay as many small flat faces.

The file is plain text and byte-identical for identical meshes.
"""
import numpy as np

from ._common import VERSION

# Facets whose normals differ by less than this are merged (radians, about 0.06 degrees).
COPLANAR_ANGLE = 1e-3


def _number(value):
    """A STEP REAL: always has a decimal point; exponent written with E."""
    value = float(value)
    if value == 0:
        return "0."
    text = format(value, ".12g")
    if "e" in text:
        mantissa, exponent = text.split("e")
        if "." not in mantissa:
            mantissa += "."
        return f"{mantissa}E{int(exponent)}"
    return text if "." in text else text + "."


def _string(text):
    return "'" + str(text).replace("'", "''") + "'"


def _loops(faces):
    """Boundary loops (vertex index lists, wound like the faces) of a patch of triangles,
    or None when the boundary touches itself and cannot be split into simple loops."""
    edges = set()
    for a, b, c in faces:
        edges.update(((a, b), (b, c), (c, a)))
    boundary = [e for e in edges if (e[1], e[0]) not in edges]
    following = {}
    for start, end in boundary:
        if start in following:
            return None  # two boundary edges leave one vertex: a pinch point
        following[start] = end
    loops = []
    while following:
        start, current = next(iter(following.items()))
        loop = [start]
        del following[start]
        while current != start:
            if current not in following:
                return None
            loop.append(current)
            current = following.pop(current)
        loops.append(loop)
    return loops


def _without_collinear(points, loop):
    """Drop vertices that lie on the straight line between their neighbours."""
    keep = []
    n = len(loop)
    for i in range(n):
        p0, p1, p2 = points[loop[i-1]], points[loop[i]], points[loop[(i+1) % n]]
        a, b = p1 - p0, p2 - p1
        scale = max(np.linalg.norm(a)*np.linalg.norm(b), 1e-300)
        if np.linalg.norm(np.cross(a, b))/scale > 1e-9 or np.dot(a, b) < 0:
            keep.append(loop[i])
    return keep if len(keep) >= 3 else loop


def _signed_area(points, loop, normal):
    ring = points[loop]
    return 0.5*float(np.dot(np.cross(ring, np.roll(ring, -1, axis=0)).sum(axis=0), normal))


def planar_faces(mesh):
    """[(normal, [outer loop, *hole loops])] covering every triangle of ``mesh``."""
    vertices = np.asarray(mesh.vertices, dtype=float)
    triangles = np.asarray(mesh.faces)
    normals = np.asarray(mesh.face_normals, dtype=float)
    grouped = np.zeros(len(triangles), dtype=bool)
    result = []
    for facet in mesh.facets:
        normal = normals[facet].mean(axis=0)
        normal /= np.linalg.norm(normal)
        if np.arccos(np.clip(normals[facet] @ normal, -1, 1)).max() > COPLANAR_ANGLE:
            continue
        loops = _loops(triangles[facet].tolist())
        if not loops:
            continue
        loops = [_without_collinear(vertices, loop) for loop in loops]
        areas = [_signed_area(vertices, loop, normal) for loop in loops]
        outer = [i for i, area in enumerate(areas) if area > 0]
        if len(outer) != 1:
            continue  # not one outline with holes; keep its triangles instead
        result.append((normal, [loops[outer[0]]] + [loop for i, loop in enumerate(loops) if i != outer[0]]))
        grouped[facet] = True
    for index in np.flatnonzero(~grouped):
        result.append((normals[index], [triangles[index].tolist()]))
    return result


def export_step(mesh, name="part"):
    """STEP text (bytes) for a closed mesh whose coordinates are millimetres."""
    lines = []

    def add(text):
        lines.append(text)
        return f"#{len(lines)}"

    context = add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,{context})")
    product_context = add(f"PRODUCT_CONTEXT('',{context},'mechanical')")
    product = add(f"PRODUCT({_string(name)},{_string(name)},'',({product_context}))")
    formation = add(f"PRODUCT_DEFINITION_FORMATION('','',{product})")
    definition_context = add(f"PRODUCT_DEFINITION_CONTEXT('part definition',{context},'design')")
    definition = add(f"PRODUCT_DEFINITION('design','',{formation},{definition_context})")
    shape = add(f"PRODUCT_DEFINITION_SHAPE('','',{definition})")
    add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,({product}))")
    length = add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
    angle = add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    solid_angle = add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    uncertainty = add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),{length},'distance_accuracy_value',"
                      "'confusion accuracy')")
    geometry = add(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({uncertainty}))"
                   f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({length},{angle},{solid_angle}))REPRESENTATION_CONTEXT('',''))")
    origin = add("CARTESIAN_POINT('',(0.,0.,0.))")
    z_axis = add("DIRECTION('',(0.,0.,1.))")
    x_axis = add("DIRECTION('',(1.,0.,0.))")
    placement = add(f"AXIS2_PLACEMENT_3D('',{origin},{z_axis},{x_axis})")

    vertices = np.asarray(mesh.vertices, dtype=float)
    point_ids = {}

    def point(index):
        if index not in point_ids:
            x, y, z = vertices[index]
            point_ids[index] = add(f"CARTESIAN_POINT('',({_number(x)},{_number(y)},{_number(z)}))")
        return point_ids[index]

    def direction(v):
        return add(f"DIRECTION('',({_number(v[0])},{_number(v[1])},{_number(v[2])}))")

    faces = []
    for normal, loops in planar_faces(mesh):
        bounds = []
        for k, loop in enumerate(loops):
            poly = add(f"POLY_LOOP('',({','.join(point(i) for i in loop)}))")
            bounds.append(add(f"{'FACE_OUTER_BOUND' if k == 0 else 'FACE_BOUND'}('',{poly},.T.)"))
        first = vertices[loops[0][0]]
        reference = vertices[loops[0][1]] - first
        reference -= np.dot(reference, normal)*normal
        reference /= np.linalg.norm(reference)
        frame = add(f"AXIS2_PLACEMENT_3D('',{point(loops[0][0])},{direction(normal)},{direction(reference)})")
        plane = add(f"PLANE('',{frame})")
        faces.append(add(f"FACE_SURFACE('',({','.join(bounds)}),{plane},.T.)"))
    shell = add(f"CLOSED_SHELL('',({','.join(faces)}))")
    brep = add(f"FACETED_BREP({_string(name)},{shell})")
    representation = add(f"FACETED_BREP_SHAPE_REPRESENTATION({_string(name)},({brep},{placement}),{geometry})")
    add(f"SHAPE_DEFINITION_REPRESENTATION({shape},{representation})")

    header = [
        "ISO-10303-21;", "HEADER;",
        "FILE_DESCRIPTION(('CDCAD faceted solid'),'2;1');",
        f"FILE_NAME({_string(name)},'1970-01-01T00:00:00',(''),(''),'CDCAD {VERSION}','CDCAD','');",
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));", "ENDSEC;", "DATA;",
    ]
    body = [f"#{i}={text};" for i, text in enumerate(lines, start=1)]
    return ("\n".join(header + body + ["ENDSEC;", "END-ISO-10303-21;"]) + "\n").encode("ascii")
