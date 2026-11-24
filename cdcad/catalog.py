"""Every recipe operation in one table: its properties, their documentation, defaults
and a small working example.

The JSON Schema (schema.py), ``cdcad describe``, ``cdcad guide`` and the MCP tool
descriptions are all generated from this table, and the test suite builds every example,
so the documentation cannot drift from what the engine accepts.

Lengths are in the recipe's units (mm unless ``units`` says otherwise) and angles are in
degrees, except inside expressions, where trigonometric functions take radians.
"""
from ._common import UNITS

NUM = {"type": "number"}
POS = {"type": "number", "exclusiveMinimum": 0}
NONNEG = {"type": "number", "minimum": 0}
INT = {"type": "integer", "minimum": 3, "maximum": 100000}
STR = {"type": "string", "minLength": 1}
REF = {"type": "string", "minLength": 1}
BOOL = {"type": "boolean"}
LITERAL = {"type": "string", "minLength": 1, "x-cdcad-literal": True}   # never evaluated, even if it starts with '='
NOTE = {"type": "string", "x-cdcad-literal": True}


def vector(n, item=NUM):
    return {"type": "array", "items": item, "minItems": n, "maxItems": n}


def array(item, minimum=1):
    return {"type": "array", "items": item, "minItems": minimum}


def expr(*variables):
    """A field expression: text that is a function of these variables (and parameters)."""
    return {"type": "string", "minLength": 1, "x-cdcad-variables": list(variables)}


V2, V3 = vector(2), vector(3)
RANGE = vector(2)
POINTS = array(V2, 3)
ANCHOR = {"enum": ["center", "left", "right", "front", "back", "bottom", "top"]}
COUNT = {"type": "integer", "minimum": 1, "maximum": 10000}
JOIN = {"enum": ["round", "mitre", "bevel"]}
FILL_RULE = {"enum": ["evenodd", "nonzero"]}
PLANE = {"enum": ["xy", "xz", "yz"]}
ARCH_PROPS = {"span": POS, "rise": POS, "leg": NONNEG, "segments": INT}
CUTOUT = {"oneOf": [{"type": "object", "additionalProperties": False,
    "required": ["shape", *required], "properties": {
        "shape": {"const": shape}, "center": V2, "rotate": NUM, **props}}
    for shape, required, props in [
        ("rectangle", ["size"], {"size": vector(2, POS), "radius": NONNEG}),
        ("circle", ["radius"], {"radius": POS}),
        ("slot", ["size"], {"size": vector(2, POS)}),
        ("arch", ["span"], ARCH_PROPS)]]}
CURVE3 = {"type": "object", "additionalProperties": False, "required": ["x", "y", "z"], "properties": {
    "x": expr("t"), "y": expr("t"), "z": expr("t"), "t": RANGE,
    "samples": {"type": "integer", "minimum": 2, "maximum": 1000000}}}
EDGE = {"type": "object", "additionalProperties": False, "minProperties": 1, "properties": {
    "fillet": POS, "chamfer": POS, "segments": {"type": "integer", "minimum": 1, "maximum": 256}}}
TRANSFORM = {"translate": V3, "rotate": V3, "scale": V3, "matrix": vector(4, vector(4))}

OPS = {"profiles": {}, "solids": {}}


def op(kind, name, summary, required=None, optional=None, example=None, notes=None, group=None):
    """Register an operation.

    ``required``: {prop: (schema, doc)}; ``optional``: {prop: (schema, doc, default)}
    with default None when there is none. ``example`` is a recipe fragment whose
    solid "part" shows the operation.
    """
    OPS[kind][name] = {
        "summary": summary, "group": group or "", "notes": notes,
        "required": dict(required or {}), "optional": dict(optional or {}), "example": example,
    }


def ex(profiles=None, solids=None, **extra):
    return {"profiles": profiles or {}, "solids": solids or {}, **extra}


SQUARE = {"op": "rectangle", "size": [20, 20]}

# ---- profiles: 2D regions -------------------------------------------------------------

op("profiles", "rectangle", "Axis-aligned rectangle centred on `center`, optionally with rounded corners.",
   {"size": (vector(2, POS), "[width, depth]")},
   {"center": (V2, "Centre point", [0, 0]), "radius": (NONNEG, "Corner radius; at most half the shorter side", 0)},
   ex({"p": {"op": "rectangle", "size": [40, 25], "radius": 3}}, {"part": {"op": "extrude", "profile": "p", "height": 4}}),
   group="shapes")
op("profiles", "circle", "Circle as a regular polygon with `segments` sides.",
   {"radius": (POS, "Radius")},
   {"center": (V2, "Centre point", [0, 0]), "segments": (INT, "Number of sides", 96)},
   ex({"p": {"op": "circle", "radius": 10}}, {"part": {"op": "extrude", "profile": "p", "height": 3}}), group="shapes")
op("profiles", "ellipse", "Ellipse with radii [rx, ry].",
   {"radii": (vector(2, POS), "[rx, ry]")},
   {"center": (V2, "Centre point", [0, 0]), "segments": (INT, "Number of sides", 96)},
   ex({"p": {"op": "ellipse", "radii": [20, 10]}}, {"part": {"op": "extrude", "profile": "p", "height": 3}}), group="shapes")
op("profiles", "regular_polygon", "Regular polygon such as a hexagon. Give either `radius` (centre to corner) "
   "or `across_flats` (distance between opposite flat sides, as for nuts and hex keys).",
   {"sides": ({"type": "integer", "minimum": 3, "maximum": 1000}, "Number of sides")},
   {"radius": (POS, "Circumradius (centre to corner)", None),
    "across_flats": (POS, "Width across flats; the alternative to radius", None),
    "center": (V2, "Centre point", [0, 0]), "rotate": (NUM, "Rotation in degrees", 0)},
   ex({"hex": {"op": "regular_polygon", "sides": 6, "across_flats": 5.5}},
      {"part": {"op": "extrude", "profile": "hex", "height": 2.4}}), group="shapes")
op("profiles", "polygon", "Polygon from points, optionally with holes (each hole is a point ring).",
   {"points": (POINTS, "Outer boundary [[x, y], ...]")},
   {"holes": (array(POINTS, 0), "Rings inside the boundary that are cut out", [])},
   ex({"p": {"op": "polygon", "points": [[0, 0], [30, 0], [15, 20]]}}, {"part": {"op": "extrude", "profile": "p", "height": 2}}),
   group="shapes")
op("profiles", "stroke", "A polyline thickened to `width`, like a pen line.",
   {"points": (array(V2, 2), "Points along the line"), "width": (POS, "Line width")},
   {"closed": (BOOL, "Join the last point back to the first", False),
    "cap": ({"enum": ["round", "square", "flat"]}, "End cap style", "round"), "join": (JOIN, "Corner style", "round")},
   ex({"p": {"op": "stroke", "points": [[0, 0], [20, 0], [20, 15]], "width": 3}},
      {"part": {"op": "extrude", "profile": "p", "height": 2}}), group="shapes")
op("profiles", "spline", "Smooth curve through points (a cubic spline). Closed, it is a smooth outline that "
   "joins itself without a kink; with `width` it is drawn as a line of that width instead, open unless `closed`.",
   {"points": (array(V2, 2), "Points the curve passes through, in order")},
   {"closed": (BOOL, "Join the end back to the start (default: true without width, false with width)", None),
    "width": (POS, "Draw the curve as a line of this width instead of filling it", None),
    "cap": ({"enum": ["round", "square", "flat"]}, "End cap style for an open line", "round"),
    "samples": ({"type": "integer", "minimum": 3, "maximum": 1000000}, "Points along the curve", 256)},
   ex({"blob": {"op": "spline", "points": [[0, 0], [30, -5], [40, 15], [20, 30], [-5, 20]]}},
      {"part": {"op": "extrude", "profile": "blob", "height": 4}}), group="shapes")
op("profiles", "slot", "Rounded slot (stadium) of overall size [width, height].",
   {"size": (vector(2, POS), "[width, height]")}, {"center": (V2, "Centre point", [0, 0])},
   ex({"p": {"op": "slot", "size": [30, 8]}}, {"part": {"op": "extrude", "profile": "p", "height": 2}}), group="shapes")
op("profiles", "function", "Region between the graph of a function and a base line: with var x it is the area "
   "between y = f(x) and y = base for x in range; with var y it is between x = f(y) and x = base, which is the "
   "shape `revolve` turns into a vase or knob.",
   {"f": (expr("x", "y"), "The function, of x or y according to var, e.g. 18 + 6*sin(y/12)"),
    "range": (RANGE, "[start, end] of the variable")},
   {"var": ({"enum": ["x", "y"]}, "The free variable", "x"),
    "base": ({"oneOf": [NUM, expr("x", "y")]}, "The other boundary: a number or a function of the same variable", 0),
    "samples": ({"type": "integer", "minimum": 2, "maximum": 100000}, "Points along the curve", 128)},
   ex({"wall": {"op": "function", "f": "20 + 5*sin(y/8)", "var": "y", "range": [0, 60], "base": 0}},
      {"part": {"op": "revolve", "profile": "wall"}}),
   notes="Where f crosses base the region splits into lobes; their areas are |f - base|.", group="math")
op("profiles", "curve", "Closed parametric curve (x(t), y(t)) for t in [start, end]; the end joins the start. "
   "Self-intersecting curves are filled even-odd.",
   {"x": (expr("t"), "x as a function of t"), "y": (expr("t"), "y as a function of t")},
   {"t": (RANGE, "Parameter range", [0, "tau"]),
    "samples": ({"type": "integer", "minimum": 3, "maximum": 1000000}, "Points along the curve", 256)},
   ex({"gear": {"op": "curve", "x": "(20 + 1.5*sin(12*t))*cos(t)", "y": "(20 + 1.5*sin(12*t))*sin(t)", "samples": 720}},
      {"part": {"op": "extrude", "profile": "gear", "height": 5}}), group="math")
op("profiles", "text", "Text outline from a TrueType/OpenType font; bundled DejaVu Sans by default (Latin, Greek and "
   "math symbols). `size` is the height of capital letters. Use \\n for new lines.",
   {"text": (LITERAL, "The text")},
   {"size": (POS, "Capital-letter height", 10), "path": (STR, "A .ttf/.otf font file instead of the bundled one", None),
    "halign": ({"enum": ["left", "center", "right"]}, "Horizontal alignment to x = 0", "left"),
    "valign": ({"enum": ["baseline", "bottom", "center", "top"]}, "Vertical alignment to y = 0", "baseline"),
    "spacing": (POS, "Letter-spacing factor", 1), "line_spacing": (POS, "Line height as a multiple of the font size", 1.2),
    "segments": ({"type": "integer", "minimum": 1, "maximum": 64}, "Line segments per curved glyph edge", 6)},
   ex({"label": {"op": "text", "text": "CDCAD", "size": 8, "halign": "center", "valign": "center"},
       "plate": {"op": "rectangle", "size": [50, 16], "radius": 2}},
      {"plate": {"op": "extrude", "profile": "plate", "height": 2},
       "letters": {"op": "extrude", "profile": "label", "height": 1, "z": 1.9},
       "part": {"op": "union", "inputs": ["plate", "letters"]}}), group="shapes")
op("profiles", "path", "Region from SVG path data. Closed subpaths are filled; give `width` to stroke instead.",
   {"d": (LITERAL, "SVG path data, e.g. M0 0 H40 V25 H0 Z")},
   {"width": (POS, "Stroke the path with this width instead of filling it", None),
    "step": (POS, "Maximum spacing of samples on curves", 0.5), "fill_rule": (FILL_RULE, "How nested contours fill", "evenodd")},
   ex({"p": {"op": "path", "d": "M0 0 H40 V25 H0 Z M8 8 H16 V16 H8 Z"}}, {"part": {"op": "extrude", "profile": "p", "height": 3}}),
   group="drawings")
op("profiles", "svg", "Filled shapes from an SVG file, in true size (96 px per inch). Y is flipped so the drawing "
   "reads the right way up. Open filled paths are closed, as SVG viewers do; strokes and text are not imported.",
   {"path": (STR, "SVG file, relative to the recipe")},
   {"ids": (array(LITERAL), "Only these shape ids", None), "step": (POS, "Curve sample spacing in SVG px", 0.5),
    "flip_y": (BOOL, "Flip Y for right-handed CAD coordinates", True),
    "fill_rule": (FILL_RULE, "Override each shape's fill-rule", None)},
   ex({"p": {"op": "svg", "path": "examples/outline.svg", "ids": ["plate"]}}, {"part": {"op": "extrude", "profile": "p", "height": 3}}),
   group="drawings")
op("profiles", "dxf", "Closed contours from a DXF file (lines, arcs, circles, ellipses, splines, polylines at Z=0). "
   "Nested contours become holes. Layer names match regardless of case.",
   {"path": (STR, "DXF file, relative to the recipe")},
   {"layers": (array(LITERAL), "Only these layers", None), "tolerance": (POS, "Curve flattening tolerance", 0.05)},
   ex({"p": {"op": "dxf", "path": "examples/drawing.dxf", "layers": ["OUTLINE"]}}, {"part": {"op": "extrude", "profile": "p", "height": 3}}),
   group="drawings")
for name, what in (("union", "Merge profiles"), ("difference", "Subtract every later profile from the first"),
                   ("intersection", "Keep the area common to all profiles")):
    op("profiles", name, what + ".", {"inputs": (array(REF, 1 if name == "union" else 2), "Profile names")}, {},
       ex({"a": SQUARE, "b": {"op": "circle", "radius": 12}, "p": {"op": name, "inputs": ["a", "b"]}},
          {"part": {"op": "extrude", "profile": "p", "height": 2}}), group="combine")
op("profiles", "offset", "Grow (positive) or shrink (negative) a profile by a distance; rounds or mitres corners.",
   {"input": (REF, "Profile name"), "distance": (NUM, "Offset distance")}, {"join": (JOIN, "Corner style", "round")},
   ex({"a": SQUARE, "p": {"op": "offset", "input": "a", "distance": 3}}, {"part": {"op": "extrude", "profile": "p", "height": 2}}),
   group="modify")
op("profiles", "fillet", "Round the corners of a profile with an exact radius: outside (convex) corners, inside "
   "(concave) corners or both. Parts narrower than twice the radius are removed.",
   {"input": (REF, "Profile name"), "radius": (POS, "Corner radius")},
   {"corners": ({"enum": ["all", "convex", "concave"]}, "Which corners to round", "all"),
    "segments": ({"type": "integer", "minimum": 1, "maximum": 256}, "Arc segments per quarter turn", 16)},
   ex({"l": {"op": "polygon", "points": [[0, 0], [40, 0], [40, 10], [10, 10], [10, 30], [0, 30]]},
       "p": {"op": "fillet", "input": "l", "radius": 3}}, {"part": {"op": "extrude", "profile": "p", "height": 4}}),
   group="modify")
op("profiles", "chamfer", "Bevel the corners of a profile; at a right-angled corner each leg of the bevel is `distance`.",
   {"input": (REF, "Profile name"), "distance": (POS, "Bevel size")},
   {"corners": ({"enum": ["all", "convex", "concave"]}, "Which corners to bevel", "all")},
   ex({"a": {"op": "rectangle", "size": [30, 20]}, "p": {"op": "chamfer", "input": "a", "distance": 4}},
      {"part": {"op": "extrude", "profile": "p", "height": 3}}), group="modify")
op("profiles", "transform", "Scale, then rotate (degrees), then translate a profile about the origin.",
   {"input": (REF, "Profile name")},
   {"scale": (V2, "[sx, sy]; negative mirrors", [1, 1]), "rotate": (NUM, "Degrees counter-clockwise", 0),
    "translate": (V2, "[dx, dy]", [0, 0])},
   ex({"a": SQUARE, "p": {"op": "transform", "input": "a", "rotate": 45, "translate": [30, 0]}},
      {"part": {"op": "extrude", "profile": "p", "height": 2}}), group="modify")
op("profiles", "hull", "Convex hull of one or more profiles.", {},
   {"input": (REF, "Profile name", None), "inputs": (array(REF), "Several profiles", None)},
   ex({"a": {"op": "circle", "radius": 5}, "b": {"op": "circle", "radius": 3, "center": [25, 0]},
       "p": {"op": "hull", "inputs": ["a", "b"]}}, {"part": {"op": "extrude", "profile": "p", "height": 3}}),
   notes="Give input or inputs.", group="modify")
op("profiles", "array", "Copies of a profile: copy i is rotated by i*rotate degrees about `center`, then moved by "
   "i*step. With step [0, 0] and rotate 360/count this is a polar (circular) pattern.",
   {"input": (REF, "Profile name"), "count": (COUNT, "Number of copies")},
   {"step": (V2, "Translation between copies", [0, 0]), "rotate": (NUM, "Degrees between copies", 0),
    "center": (V2, "Rotation centre", [0, 0])},
   ex({"hole": {"op": "circle", "radius": 2, "center": [15, 0]}, "ring": {"op": "array", "input": "hole", "count": 6, "rotate": 60},
       "disc": {"op": "circle", "radius": 20}, "p": {"op": "difference", "inputs": ["disc", "ring"]}},
      {"part": {"op": "extrude", "profile": "p", "height": 3}}), group="pattern")
op("profiles", "grid", "Rectangular grid of copies: counts [nx, ny] spaced by step [dx, dy].",
   {"input": (REF, "Profile name"), "counts": (vector(2, COUNT), "[nx, ny]"), "step": (V2, "[dx, dy]")}, {},
   ex({"hole": {"op": "circle", "radius": 2}, "p": {"op": "grid", "input": "hole", "counts": [4, 3], "step": [8, 8]}},
      {"part": {"op": "extrude", "profile": "p", "height": 2}}), group="pattern")
op("profiles", "arch_opening", "Opening of an arch: straight legs of height `leg` under a semi-ellipse of `rise`.",
   {"span": (POS, "Clear width")},
   {"rise": (POS, "Height of the curve", "span/2"), "leg": (NONNEG, "Straight leg height", 0), "segments": (INT, "Curve sides", 64)},
   ex({"p": {"op": "arch_opening", "span": 20, "leg": 10}}, {"part": {"op": "extrude", "profile": "p", "height": 2}}), group="structural")
op("profiles", "arch", "Arch band of `thickness` around an arch opening.",
   {"span": (POS, "Clear width"), "thickness": (POS, "Band thickness")},
   {"rise": (POS, "Height of the curve", "span/2"), "leg": (NONNEG, "Straight leg height", 0), "segments": (INT, "Curve sides", 64)},
   ex({"p": {"op": "arch", "span": 30, "thickness": 4, "leg": 10}}, {"part": {"op": "extrude", "profile": "p", "height": 5}}),
   group="structural")
op("profiles", "frame", "Rectangular frame: outer size minus an inset of `border`.",
   {"size": (vector(2, POS), "Outer [width, height]"), "border": (POS, "Frame width")}, {},
   ex({"p": {"op": "frame", "size": [40, 30], "border": 4}}, {"part": {"op": "extrude", "profile": "p", "height": 3}}),
   group="structural")

# ---- solids ---------------------------------------------------------------------------

op("solids", "box", "Box centred on the origin.", {"size": (vector(3, POS), "[x, y, z]")}, {},
   ex(solids={"part": {"op": "box", "size": [30, 20, 10]}}), group="primitives")
op("solids", "cylinder", "Cylinder along Z, centred on the origin.",
   {"radius": (POS, "Radius"), "height": (POS, "Height")}, {"segments": (INT, "Sides", 96)},
   ex(solids={"part": {"op": "cylinder", "radius": 8, "height": 20}}), group="primitives")
op("solids", "cone", "Cone or frustum along Z, centred on the origin: `radius` at the bottom, `radius_top` at the top.",
   {"radius": (POS, "Bottom radius"), "height": (POS, "Height")},
   {"radius_top": (NONNEG, "Top radius; 0 makes a point", 0), "segments": (INT, "Sides", 96)},
   ex(solids={"part": {"op": "cone", "radius": 10, "radius_top": 4, "height": 15}}), group="primitives")
op("solids", "sphere", "Sphere centred on the origin (an icosphere).",
   {"radius": (POS, "Radius")}, {"subdivisions": ({"type": "integer", "minimum": 0, "maximum": 7}, "Refinement level", 3)},
   ex(solids={"part": {"op": "sphere", "radius": 10}}), group="primitives")
op("solids", "torus", "Torus around the Z axis, centred on the origin.",
   {"major": (POS, "Distance from the axis to the tube centre"), "minor": (POS, "Tube radius")},
   {"segments": (INT, "Sides around the axis", 96), "minor_segments": (INT, "Sides around the tube", 48)},
   ex(solids={"part": {"op": "torus", "major": 15, "minor": 4}}), notes="minor must be less than major.", group="primitives")
op("solids", "polyhedron", "Solid from vertices and faces (each face lists vertex indices, any winding; polygons are "
   "split into triangles). Without faces it is the convex hull of the points.",
   {"points": (array(V3, 4), "[[x, y, z], ...]")},
   {"faces": (array(array({"type": "integer", "minimum": 0}, 3)), "Faces as vertex index lists", None)},
   ex(solids={"part": {"op": "polyhedron", "points": [[0, 0, 0], [20, 0, 0], [0, 20, 0], [0, 0, 20]],
                       "faces": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]}}), group="primitives")
op("solids", "extrude", "Extrude a profile up from `z`. `twist` turns the top (degrees) and `scale` resizes it, "
   "which makes twisted columns and tapers.",
   {"profile": (REF, "Profile name"), "height": (POS, "Height")},
   {"z": (NUM, "Starting Z", 0), "twist": (NUM, "Rotation of the top in degrees, counter-clockwise", 0),
    "scale": ({"oneOf": [POS, vector(2, POS)]}, "Top size relative to the bottom: a number or [sx, sy]", 1),
    "slices": ({"type": "integer", "minimum": 1, "maximum": 10000}, "Layers for twist/scale", "auto"),
    "top": (EDGE, "Round or bevel the top edges: {fillet: r} or {chamfer: d}, optionally segments", None),
    "bottom": (EDGE, "Round or bevel the bottom edges, like top", None)},
   ex({"star": {"op": "curve", "x": "(12 + 3*cos(5*t))*cos(t)", "y": "(12 + 3*cos(5*t))*sin(t)", "samples": 200}},
      {"part": {"op": "extrude", "profile": "star", "height": 40, "twist": 120, "scale": 0.6}}),
   notes="top and bottom give exact fillets or 45-degree chamfers along every top or bottom edge, holes included; "
         "they cannot be combined with twist or scale. Round the vertical edges with a profile fillet.",
   group="from 2D")
op("solids", "revolve", "Turn a profile around the Z axis. Profile X is the radius (X >= 0), profile Y becomes Z.",
   {"profile": (REF, "Profile name: one polygon without holes, X >= 0")},
   {"angle": ({"type": "number", "exclusiveMinimum": 0, "maximum": 360}, "Sweep angle in degrees; less than 360 leaves flat ends", 360),
    "segments": (INT, "Sides for a full turn", 96)},
   ex({"p": {"op": "polygon", "points": [[0, 0], [18, 0], [18, 3], [12, 3], [12, 18], [0, 24]]}},
      {"part": {"op": "revolve", "profile": "p"}}), group="from 2D")
op("solids", "loft", "Skin a solid through profiles at increasing Z heights (one hole-free outline each).",
   {"sections": (array({"type": "object", "properties": {"profile": REF, "z": NUM}, "required": ["profile", "z"],
                        "additionalProperties": False}, 2), "[{profile, z}, ...] with strictly increasing z")},
   {"samples": (INT, "Points per outline", 96),
    "smooth": (BOOL, "Curve the sides through the sections with a spline instead of straight lines (3+ sections)", False),
    "steps": ({"type": "integer", "minimum": 1, "maximum": 1000}, "Layers between two sections when smooth", 8)},
   ex({"a": {"op": "rectangle", "size": [30, 30]}, "b": {"op": "circle", "radius": 8}},
      {"part": {"op": "loft", "sections": [{"profile": "a", "z": 0}, {"profile": "b", "z": 25}]}}),
   notes="Very different sections can self-intersect; nothing checks for that.", group="from 2D")
op("solids", "sweep", "Sweep a profile along a 3D path: a list of `points` or a parametric `curve`. The profile's X "
   "and Y axes follow the path with rotation-minimising frames, or with `frame` up, keep Y towards the `up` "
   "vector (best for helices: threads, springs). `closed` joins the ends into a ring.",
   {"profile": (REF, "Profile name (holes allowed)")},
   {"points": (array(V3, 2), "Path points", None), "curve": (CURVE3, "{x, y, z: functions of t, t: [a, b], samples}", None),
    "closed": (BOOL, "Path is a loop", False), "frame": ({"enum": ["rmf", "up"]}, "How the profile turns", "rmf"),
    "up": (V3, "Reference direction", [0, 0, 1]), "twist": (NUM, "Extra roll along the path in degrees", 0),
    "scale": (POS, "Profile scale at the end (linear along the path)", 1),
    "smooth": (BOOL, "Run the path along a smooth cubic spline through `points` instead of straight segments", False),
    "samples": ({"type": "integer", "minimum": 2, "maximum": 1000000}, "Path points for a smooth path", "16 per point")},
   ex({"wire": {"op": "circle", "radius": 1.2, "segments": 24}},
      {"part": {"op": "sweep", "profile": "wire", "frame": "up",
                "curve": {"x": "10*cos(t)", "y": "10*sin(t)", "z": "4*t/tau", "t": [0, "=5*tau"], "samples": 600}}}),
   notes="Give points or curve. The path must not pass through itself.", group="from 2D")
op("solids", "implicit", "Solid where f(x, y, z) < 0, meshed inside `bounds`: spheres, gyroids, blobs. "
   "The region is clipped (and closed) by the bounds box.",
   {"f": (expr("x", "y", "z"), "Field; negative inside, e.g. x**2 + y**2 + z**2 - 10**2"),
    "bounds": (vector(2, V3), "[[xmin, ymin, zmin], [xmax, ymax, zmax]]")},
   {"resolution": (POS, "Grid spacing; smaller is finer and slower", "largest side/80"),
    "level": (NUM, "Surface where f equals this value", 0)},
   ex(solids={"part": {"op": "implicit", "f": "sin(x)*cos(y) + sin(y)*cos(z) + sin(z)*cos(x)",
                       "bounds": [[0, 0, 0], [12.6, 12.6, 12.6]], "resolution": 0.3}}),
   notes="Smooth fields mesh best; use smin(a, b, k) to blend shapes. Cost grows with (size/resolution)^3.",
   group="math")
op("solids", "graph", "Solid under a surface z = f(x, y) over a rectangle, down to a flat bottom at `base`.",
   {"f": (expr("x", "y"), "Height as a function of x and y"), "x": (RANGE, "[xmin, xmax]"), "y": (RANGE, "[ymin, ymax]")},
   {"base": (NUM, "Z of the flat bottom; f must stay above it", 0),
    "samples": (vector(2, {"type": "integer", "minimum": 2, "maximum": 4096}), "Grid points [nx, ny]", [128, 128])},
   ex(solids={"part": {"op": "graph", "f": "4 + 2*cos(hypot(x, y))", "x": [-10, 10], "y": [-10, 10]}}), group="math")
op("solids", "heightfield", "Relief from a grayscale picture or a 2D .npy array of values in [0, 1]: black is "
   "`base`, white is base + height. Pictures keep their on-screen orientation (top row at high Y); arrays put row 0 "
   "at Y = 0. 16-bit PNGs keep their precision.",
   {"path": (STR, "Image or .npy file"), "size": (vector(2, POS), "[x, y] size"), "height": (POS, "Relief height")},
   {"base": (POS, "Thickness under black", 1), "max_samples": ({"type": "integer", "minimum": 2, "maximum": 2048}, "Resample the longer side to at most this", 256),
    "invert": (BOOL, "White low, black high", False), "flip_y": (BOOL, "Flip rows (default: true for pictures, false for arrays)", None)},
   ex(solids={"part": {"op": "heightfield", "path": "examples/relief.png", "size": [60, 40], "height": 6}}), group="math")
op("solids", "import", "Mesh file (STL, 3MF, OBJ, PLY, OFF, GLB). 3MF files carry their unit; for others give `units`.",
   {"path": (STR, "Mesh file")},
   {"units": ({"enum": list(UNITS)}, "Units of the file's coordinates", "recipe units"),
    "repair": (BOOL, "Merge vertices, drop degenerate faces, fill simple holes", False)},
   ex(solids={"part": {"op": "import", "path": "samples/revolved-knob.stl"}}), group="files")
for name, what in (("union", "Fuse solids into one"), ("difference", "Subtract every later solid from the first"),
                   ("intersection", "Keep the volume common to all solids")):
    op("solids", name, what + " (mesh boolean).", {"inputs": (array(REF, 1 if name == "union" else 2), "Solid names")}, {},
       ex(solids={"a": {"op": "box", "size": [20, 20, 20]}, "b": {"op": "sphere", "radius": 13},
                  "part": {"op": name, "inputs": ["a", "b"]}}), group="combine")
op("solids", "combine", "Put solids in one mesh without fusing them (fast; overlaps remain overlaps).",
   {"inputs": (array(REF), "Solid names")}, {},
   ex(solids={"a": {"op": "box", "size": [5, 5, 5]}, "b": {"op": "sphere", "radius": 3},
              "c": {"op": "transform", "input": "b", "translate": [10, 0, 0]}, "part": {"op": "combine", "inputs": ["a", "c"]}}),
   group="combine")
op("solids", "transform", "Scale, then rotate (XYZ Euler degrees), then translate about the origin; or one 4x4 matrix.",
   {"input": (REF, "Solid name")},
   {"scale": (V3, "[sx, sy, sz]; negative mirrors", [1, 1, 1]), "rotate": (V3, "[rx, ry, rz] degrees", [0, 0, 0]),
    "translate": (V3, "[dx, dy, dz]", [0, 0, 0]), "matrix": (vector(4, vector(4)), "Affine 4x4 for column vectors, instead of the others", None)},
   ex(solids={"a": {"op": "box", "size": [10, 4, 2]}, "part": {"op": "transform", "input": "a", "rotate": [0, 0, 45], "translate": [0, 0, 5]}}),
   group="modify")
op("solids", "mirror", "Reflect a solid in the plane through `origin` with `normal`; `keep` also keeps the original (fused).",
   {"input": (REF, "Solid name")},
   {"normal": (V3, "Plane normal", [1, 0, 0]), "origin": (V3, "A point on the plane", [0, 0, 0]),
    "keep": (BOOL, "Union the mirror image with the original", False)},
   ex(solids={"a": {"op": "box", "size": [10, 6, 4]}, "b": {"op": "transform", "input": "a", "translate": [4, 0, 0]},
              "part": {"op": "mirror", "input": "b", "keep": True}}), group="modify")
op("solids", "array", "Copies of a solid: copy i is rotated by i*rotate (XYZ degrees about the origin), then moved by "
   "i*step. Rotate [0, 0, 360/count] makes a polar pattern.",
   {"input": (REF, "Solid name"), "count": (COUNT, "Number of copies")},
   {"step": (V3, "Translation between copies", [0, 0, 0]), "rotate": (V3, "Rotation between copies, degrees", [0, 0, 0]),
    "fuse": (BOOL, "Union the copies (false just collects them)", True)},
   ex(solids={"spoke": {"op": "box", "size": [20, 3, 3]}, "s": {"op": "transform", "input": "spoke", "translate": [10, 0, 0]},
              "part": {"op": "array", "input": "s", "count": 5, "rotate": [0, 0, 72]}}), group="pattern")
op("solids", "repeat", "3D grid of copies: counts [nx, ny, nz] spaced by step.",
   {"input": (REF, "Solid name"), "counts": (vector(3, COUNT), "[nx, ny, nz]"), "step": (V3, "[dx, dy, dz]")},
   {"fuse": (BOOL, "Union the copies", True)},
   ex(solids={"c": {"op": "box", "size": [4, 4, 4]}, "part": {"op": "repeat", "input": "c", "counts": [3, 2, 1], "step": [6, 6, 0]}}),
   group="pattern")
op("solids", "hull", "Convex hull of one or more solids.", {},
   {"input": (REF, "Solid name", None), "inputs": (array(REF), "Several solids", None)},
   ex(solids={"a": {"op": "sphere", "radius": 5}, "b": {"op": "cylinder", "radius": 2, "height": 4},
              "c": {"op": "transform", "input": "b", "translate": [20, 0, 0]}, "part": {"op": "hull", "inputs": ["a", "c"]}}),
   notes="Give input or inputs.", group="modify")
op("solids", "fillet", "Round the edges of any solid with a ball of `radius`: convex edges lose material, concave "
   "edges (where two parts meet) gain a smooth fillet, which blends a union into one organic piece.",
   {"input": (REF, "Solid name"), "radius": (POS, "Fillet radius")},
   {"edges": ({"enum": ["all", "convex", "concave"]}, "Which edges to round", "all"),
    "segments": ({"type": "integer", "minimum": 4, "maximum": 128}, "Sides of the rounding ball", 24)},
   ex(solids={"a": {"op": "box", "size": [30, 20, 8]}, "b": {"op": "cylinder", "radius": 5, "height": 20},
              "c": {"op": "transform", "input": "b", "translate": [0, 0, 8]}, "u": {"op": "union", "inputs": ["a", "c"]},
              "part": {"op": "fillet", "input": "u", "radius": 1.5, "segments": 8, "edges": "concave"}}),
   notes="With the B-rep kernel (pip install cdcad[brep]) and edges all, a solid made of primitives, extrusions, "
         "booleans, uniform scale/rotate/translate is filleted exactly, on its real edges, in milliseconds, and its "
         "STEP export keeps true curved faces. OpenCascade can refuse a fillet where a face is too narrow for the "
         "radius (its error suggests a smaller size). Otherwise the fillet is made by morphology on the mesh: any "
         "closed solid, facets as fine as `segments`; a convex solid rounds all edges, and a non-convex one uses edges "
         "concave, since its convex edges are too slow that way.", group="modify")
op("solids", "chamfer", "Bevel the edges of any solid. Edges parallel to an axis get a 45-degree bevel whose legs are "
   "`distance`; other edges get a bevel of similar size.",
   {"input": (REF, "Solid name"), "distance": (POS, "Bevel size")},
   {"edges": ({"enum": ["all", "convex", "concave"]}, "Which edges to bevel", "all")},
   ex(solids={"a": {"op": "box", "size": [30, 20, 10]}, "part": {"op": "chamfer", "input": "a", "distance": 2}}),
   notes="Exact with the B-rep kernel when edges is all and the solid is B-rep (see fillet); otherwise the mesh "
         "method, whose tool is an octahedron, so corners where three edges meet are cut by a triangle.",
   group="modify")
op("solids", "smooth", "Turn a faceted solid into a smooth, organic one by subdividing it onto a curved surface. "
   "Edges whose faces meet at more than `sharp_angle` stay creased; `smoothness` rounds those too.",
   {"input": (REF, "Solid name")},
   {"sharp_angle": ({"type": "number", "minimum": 0, "maximum": 180}, "Edges sharper than this (degrees) stay sharp", 60),
    "smoothness": ({"type": "number", "minimum": 0, "maximum": 1}, "Rounding of the sharp edges, 0 (crisp) to 1 (all smooth)", 0),
    "refine": ({"type": "integer", "minimum": 1, "maximum": 64}, "Split every edge into this many pieces", 4),
    "edge_length": (POS, "Refine to about this edge length instead of refine", None)},
   ex(solids={"a": {"op": "sphere", "radius": 10, "subdivisions": 1}, "part": {"op": "smooth", "input": "a", "refine": 4}}),
   notes="A faceted sphere becomes a true sphere; a box keeps its edges because they are sharper than sharp_angle. "
         "Triangles grow with refine squared.", group="modify")
op("solids", "cut", "Slice with a plane and keep the side the normal points to; `cap` closes the cut face.",
   {"input": (REF, "Solid name"), "origin": (V3, "A point on the plane"), "normal": (V3, "Direction of the kept side")},
   {"cap": (BOOL, "Close the cut (needs a solid input)", True)},
   ex(solids={"a": {"op": "sphere", "radius": 10}, "part": {"op": "cut", "input": "a", "origin": [0, 0, 0], "normal": [0, 0, 1]}}),
   group="modify")
op("solids", "repair", "Merge vertices, drop degenerate and duplicate faces, fix normals, fill simple holes.",
   {"input": (REF, "Solid name")}, {"fill_holes": (BOOL, "Fill small holes", True)},
   ex(solids={"a": {"op": "box", "size": [5, 5, 5]}, "part": {"op": "repair", "input": "a"}}), group="modify")
op("solids", "simplify", "Reduce the triangle count to about `faces` (quadric decimation; approximate).",
   {"input": (REF, "Solid name"), "faces": ({"type": "integer", "minimum": 4}, "Target face count")}, {},
   ex(solids={"a": {"op": "sphere", "radius": 10, "subdivisions": 4}, "part": {"op": "simplify", "input": "a", "faces": 800}}),
   group="modify")
op("solids", "align", "Move a solid so one of its bounding-box anchors meets an anchor of a target solid.",
   {"input": (REF, "Solid to move"), "target": (REF, "Solid to align to")},
   {"anchor": (ANCHOR, "Anchor on the moved solid", "bottom"), "target_anchor": (ANCHOR, "Anchor on the target", "top"),
    "offset": (V3, "Extra translation", [0, 0, 0])},
   ex(solids={"base": {"op": "box", "size": [20, 20, 4]}, "cap": {"op": "sphere", "radius": 5},
              "on": {"op": "align", "input": "cap", "target": "base", "offset": [0, 0, -1]},
              "part": {"op": "union", "inputs": ["base", "on"]}}), group="modify")
op("solids", "drill", "Drill cylindrical holes into a solid at `positions` (hole centres) along an axis.",
   {"input": (REF, "Solid name"), "positions": (array(V3), "Hole centres"), "radius": (POS, "Hole radius"),
    "depth": (POS, "Cutter length (centred on each position)")},
   {"axis": ({"enum": ["x", "y", "z"]}, "Drill direction", "z"), "segments": (INT, "Sides", 64)},
   ex(solids={"plate": {"op": "box", "size": [40, 20, 4]},
              "part": {"op": "drill", "input": "plate", "positions": [[-12, 0, 0], [12, 0, 0]], "radius": 1.6, "depth": 10}}),
   group="structural")
op("solids", "panel", "Flat panel with optional cutouts in the XY, XZ or YZ plane, centred on `center`.",
   {"size": (vector(2, POS), "[width, height] in the panel plane"), "thickness": (POS, "Thickness")},
   {"cutouts": (array(CUTOUT, 0), "Holes: rectangle, circle, slot or arch shapes", []), "plane": (PLANE, "Panel plane", "xy"),
    "center": (V3, "Centre", [0, 0, 0]), "radius": (NONNEG, "Corner radius", 0)},
   ex(solids={"part": {"op": "panel", "size": [60, 40], "thickness": 3, "radius": 3,
                       "cutouts": [{"shape": "circle", "radius": 5}, {"shape": "slot", "size": [20, 5], "center": [0, 12]}]}}),
   group="structural")
op("solids", "wall", "Wall along X from Z = 0, thickness centred on Y = 0, with optional cutouts in local X/Z.",
   {"length": (POS, "Length along X"), "height": (POS, "Height"), "thickness": (POS, "Thickness")},
   {"cutouts": (array(CUTOUT, 0), "Openings", []), "origin": (V3, "Bottom-centre position", [0, 0, 0])},
   ex(solids={"part": {"op": "wall", "length": 80, "height": 40, "thickness": 4,
                       "cutouts": [{"shape": "arch", "span": 16, "leg": 12, "center": [-15, 10]}]}}), group="structural")
op("solids", "frame", "Rectangular frame solid; plane XZ by default.",
   {"size": (vector(2, POS), "Outer [width, height]"), "border": (POS, "Frame width"), "depth": (POS, "Thickness")},
   {"plane": (PLANE, "Frame plane", "xz"), "center": (V3, "Centre", [0, 0, 0])},
   ex(solids={"part": {"op": "frame", "size": [40, 30], "border": 4, "depth": 5}}), group="structural")
op("solids", "arch", "Arch solid of `depth` (along Y) standing on Z = 0.",
   {"span": (POS, "Clear width"), "thickness": (POS, "Band thickness"), "depth": (POS, "Depth along Y")},
   {"rise": (POS, "Curve height", "span/2"), "leg": (NONNEG, "Straight leg height", 0), "segments": (INT, "Curve sides", 64),
    "origin": (V3, "Bottom-centre position", [0, 0, 0])},
   ex(solids={"part": {"op": "arch", "span": 30, "thickness": 4, "depth": 6, "leg": 10}}), group="structural")
op("solids", "beam", "Rectangular beam from `start` to `end`; `roll` turns its cross-section.",
   {"start": (V3, "Start point"), "end": (V3, "End point"), "size": (vector(2, POS), "Cross-section [w, h]")},
   {"roll": (NUM, "Cross-section rotation in degrees", 0)},
   ex(solids={"part": {"op": "beam", "start": [0, 0, 0], "end": [30, 10, 20], "size": [3, 3]}}), group="structural")

# Every node may carry a note; it is ignored by geometry and by cache keys.
for _kind in OPS.values():
    for _entry in _kind.values():
        _entry["optional"]["note"] = (NOTE, "Free text describing the node (ignored)", None)

CHECK_RANGE = {"type": "object", "additionalProperties": False, "minProperties": 1, "properties": {
    "min": NUM, "max": NUM, "approx": NUM, "rel": NONNEG, "abs": NONNEG}}
CHECK = {"type": "object", "additionalProperties": False, "required": ["solid"], "properties": {
    "solid": REF, "volume": CHECK_RANGE, "area": CHECK_RANGE, "mass": CHECK_RANGE, "components": CHECK_RANGE,
    "size_x": CHECK_RANGE, "size_y": CHECK_RANGE, "size_z": CHECK_RANGE, "is_volume": BOOL,
    "fits": vector(3, POS), "note": NOTE}}

RECIPE_FIELDS = {
    "version": "Always 1",
    "units": "mm (default), cm, m or in: the unit of every length in the recipe; exports are always mm",
    "description": "Free text about the recipe",
    "params": "Named numbers or =expressions; any string value starting with = elsewhere is arithmetic on them",
    "density": "Material density in g/cm^3 (PLA 1.24, PETG 1.27, ABS 1.04, aluminium 2.70); reports add mass_g",
    "profiles": "Named 2D regions",
    "solids": "Named solids",
    "exports": "[{solid, path, allow_open?}] files to write; .stl, .3mf, .step (or .stp; planar faces), .obj, .ply, .off or .glb",
    "checks": "[{solid, volume|area|mass|components|size_x|size_y|size_z: {min, max} or {approx, rel|abs}, "
              "is_volume, fits: [x, y, z]}] assertions; a failing check stops the build before anything is written",
}
