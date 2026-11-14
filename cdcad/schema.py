"""Recipe operation definitions and resolved-document validation."""
import functools

from jsonschema import Draft202012Validator

from ._common import CADError, UNITS


NUM = {"type": "number"}
POS = {"type": "number", "exclusiveMinimum": 0}
INT = {"type": "integer", "minimum": 3, "maximum": 100000}
STR = {"type": "string", "minLength": 1}
BOOL = {"type": "boolean"}


def vector(n, item=NUM):
    return {"type": "array", "items": item, "minItems": n, "maxItems": n}


def array(item, minimum=1):
    return {"type": "array", "items": item, "minItems": minimum}


V2, V3 = vector(2), vector(3)
POINTS = array(V2, 3)
TRANSFORM = {"translate": V3, "rotate": V3, "scale": V3,
             "matrix": vector(4, vector(4))}
ANCHOR = {"enum": ["center", "left", "right", "front", "back", "bottom", "top"]}
NONNEG = {"type": "number", "minimum": 0}
COUNT = {"type": "integer", "minimum": 1, "maximum": 10000}
ARCH = {"span": POS, "rise": POS, "leg": NONNEG, "segments": INT}
CUTOUT = {"oneOf": [{"type": "object", "additionalProperties": False,
    "required": ["shape", *required], "properties": {
        "shape": {"const": shape}, "center": V2, "rotate": NUM, **props}}
    for shape, required, props in [
        ("rectangle", ["size"], {"size": vector(2, POS), "radius": NONNEG}),
        ("circle", ["radius"], {"radius": POS}),
        ("slot", ["size"], {"size": vector(2, POS)}),
        ("arch", ["span"], ARCH)]]}


def spec(required, **props):
    return (required.split() if required else [], props)


PROFILE_OPS = {
    "arch_opening": spec("span", **ARCH),
    "arch": spec("span thickness", **ARCH, thickness=POS),
    "frame": spec("size border", size=vector(2, POS), border=POS),
    "slot": spec("size", size=vector(2, POS), center=V2),
    "grid": spec("input counts step", input=STR, counts=vector(2, COUNT), step=V2),
    "rectangle": spec("size", size=vector(2, POS), center=V2, radius={"type": "number", "minimum": 0}),
    "circle": spec("radius", radius=POS, center=V2, segments=INT),
    "ellipse": spec("radii", radii=vector(2, POS), center=V2, segments=INT),
    "polygon": spec("points", points=POINTS, holes=array(POINTS, 0)),
    "stroke": spec("points width", points=array(V2, 2), width=POS, closed=BOOL,
                   cap={"enum": ["round", "square", "flat"]}, join={"enum": ["round", "mitre", "bevel"]}),
    "path": spec("d", d=STR, width=POS, step=POS, fill_rule={"enum": ["evenodd", "nonzero"]}),
    "svg": spec("path", path=STR, ids=array(STR), step=POS, flip_y=BOOL,
                fill_rule={"enum": ["evenodd", "nonzero"]}),
    "dxf": spec("path", path=STR, layers=array(STR), tolerance=POS),
    "union": spec("inputs", inputs=array(STR)),
    "difference": spec("inputs", inputs=array(STR, 2)),
    "intersection": spec("inputs", inputs=array(STR, 2)),
    "offset": spec("input distance", input=STR, distance=NUM,
                   join={"enum": ["round", "mitre", "bevel"]}),
    "transform": spec("input", input=STR, translate=V2, scale=V2, rotate=NUM),
}
SOLID_OPS = {
    "arch": spec("span thickness depth", **ARCH, thickness=POS, depth=POS, origin=V3),
    "wall": spec("length height thickness", length=POS, height=POS, thickness=POS,
                 cutouts=array(CUTOUT, 0), origin=V3),
    "panel": spec("size thickness", size=vector(2, POS), thickness=POS,
                  cutouts=array(CUTOUT, 0), plane={"enum": ["xy", "xz", "yz"]}, center=V3, radius=NONNEG),
    "frame": spec("size border depth", size=vector(2, POS), border=POS, depth=POS,
                  plane={"enum": ["xy", "xz", "yz"]}, center=V3),
    "beam": spec("start end size", start=V3, end=V3, size=vector(2, POS), roll=NUM),
    "drill": spec("input positions radius depth", input=STR, positions=array(V3), radius=POS, depth=POS,
                  axis={"enum": ["x", "y", "z"]}, segments=INT),
    "align": spec("input target", input=STR, target=STR, anchor=ANCHOR, target_anchor=ANCHOR, offset=V3),
    "repeat": spec("input counts step", input=STR, counts=vector(3, COUNT), step=V3, fuse=BOOL),
    "extrude": spec("profile height", profile=STR, height=POS, z=NUM),
    "loft": spec("sections", sections=array({"type": "object", "properties": {
        "profile": STR, "z": NUM}, "required": ["profile", "z"], "additionalProperties": False}, 2), samples=INT),
    "revolve": spec("profile", profile=STR, segments=INT),
    "box": spec("size", size=vector(3, POS)),
    "cylinder": spec("radius height", radius=POS, height=POS, segments=INT),
    "sphere": spec("radius", radius=POS, subdivisions={"type": "integer", "minimum": 0, "maximum": 7}),
    "import": spec("path", path=STR, units={"enum": list(UNITS)}, repair=BOOL),
    "heightfield": spec("path size height", path=STR, size=vector(2, POS), height=POS,
                        base=POS, max_samples={"type": "integer", "minimum": 2, "maximum": 2048}, invert=BOOL),
    "union": spec("inputs", inputs=array(STR)),
    "difference": spec("inputs", inputs=array(STR, 2)),
    "intersection": spec("inputs", inputs=array(STR, 2)),
    "combine": spec("inputs", inputs=array(STR)),
    "transform": spec("input", input=STR, **TRANSFORM),
    "array": spec("input count step", input=STR, count={"type": "integer", "minimum": 1, "maximum": 10000},
                  step=V3, fuse=BOOL),
    "repair": spec("input", input=STR, fill_holes=BOOL),
    "hull": spec("input", input=STR),
    "simplify": spec("input faces", input=STR, faces={"type": "integer", "minimum": 4}),
    "cut": spec("input origin normal", input=STR, origin=V3, normal=V3, cap=BOOL),
}


def node_schema(ops):
    return {"oneOf": [{"type": "object", "properties": {"op": {"const": op}, **props},
                        "required": ["op", *required], "additionalProperties": False}
                       for op, (required, props) in ops.items()]}


@functools.lru_cache(maxsize=1)
def validators():
    # Discriminate on op before validating a node: avoid walking every oneOf
    # alternative for every node in a large model.
    schema = recipe_schema()
    nodes = {}
    for kind, ops in (("profiles", PROFILE_OPS), ("solids", SOLID_OPS)):
        nodes[kind] = {op: Draft202012Validator(choice)
                       for op, choice in zip(ops, schema["properties"][kind]["additionalProperties"]["oneOf"])}
        schema["properties"][kind]["additionalProperties"] = {"type": "object"}
    return Draft202012Validator(schema), nodes


def validate_resolved(result):
    root, nodes = validators()
    def check(validator, value, prefix=""):
        error = next(validator.iter_errors(value), None)
        if error:
            location = prefix + "/".join(map(str, error.absolute_path))
            raise CADError(f"Schema at {location or '/'}: {error.message}")
    check(root, result)
    for kind, ops in nodes.items():
        for name, node in result.get(kind, {}).items():
            if node.get("op") not in ops:
                raise CADError(f"Unknown operation at {kind}/{name}: {node.get('op')}")
            check(ops[node["op"]], node, f"{kind}/{name}/")
            # JSON Schema permits 4.0 as an integer. Normalize only integer
            # fields so expression-derived counts work with range/linspace.
            for key, rule in ops[node["op"]].schema["properties"].items():
                if key not in node:
                    continue
                if rule.get("type") == "integer":
                    node[key] = int(node[key])
                elif rule.get("type") == "array" and rule.get("items", {}).get("type") == "integer":
                    node[key] = list(map(int, node[key]))


def recipe_schema():
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "CDCAD resolved recipe v1 (expressions are resolved before validation)",
            "type": "object", "additionalProperties": False,
            "required": ["version", "solids", "exports"],
            "properties": {
                "version": {"const": 1}, "units": {"enum": list(UNITS)},
                "params": {"type": "object", "additionalProperties": NUM},
                "profiles": {"type": "object", "additionalProperties": node_schema(PROFILE_OPS)},
                "solids": {"type": "object", "additionalProperties": node_schema(SOLID_OPS)},
                "exports": array({"type": "object", "additionalProperties": False,
                                  "properties": {"solid": STR, "path": STR, "allow_open": BOOL},
                                  "required": ["solid", "path"]})}}
