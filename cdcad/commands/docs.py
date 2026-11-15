"""Documentation generated from the operation catalog: describe and guide."""
from .._common import CADError, VERSION, suggest


class Text(str):
    """A command result printed as plain text (only when the user asks with --text)."""


def register(s):
    d = s.add_parser("describe", help="Document recipe operations: list them all, or one in detail with an example")
    d.set_defaults(handler=describe)
    d.add_argument("op", nargs="?", help="Operation name, e.g. extrude; omit to list every operation")
    d.add_argument("--kind", choices=["profiles", "solids"], help="For names used by both (union, hull...)")
    d.add_argument("--text", action="store_true", help="Print readable text instead of JSON")
    g = s.add_parser("guide", help="A compact manual for writing recipes, for people and coding agents")
    g.set_defaults(handler=guide)
    g.add_argument("--text", action="store_true", help="Print Markdown instead of JSON")


def type_text(schema):
    """Short human description of a property schema."""
    if "x-cdcad-variables" in schema:
        return "expression of " + ", ".join(schema["x-cdcad-variables"])
    if "oneOf" in schema:
        return " or ".join(type_text(s) for s in schema["oneOf"])
    if "enum" in schema:
        return " | ".join(map(str, schema["enum"]))
    if "const" in schema:
        return repr(schema["const"])
    kind = schema.get("type")
    if kind == "array":
        item = schema.get("items", {})
        lo, hi = schema.get("minItems"), schema.get("maxItems")
        if lo is not None and lo == hi and item.get("type") == "number" and lo <= 3:
            return "[" + ", ".join("xyz"[:lo]) + "]"
        if lo is not None and lo == hi and item.get("type") == "array":
            return f"[{lo} x {type_text(item)}]"
        return f"list of {type_text(item)}" + (f" (at least {lo})" if lo and lo > 1 else "")
    if kind == "number":
        if "exclusiveMinimum" in schema:
            return "number > 0"
        return "number >= 0" if schema.get("minimum") == 0 else "number"
    if kind == "integer":
        lo, hi = schema.get("minimum"), schema.get("maximum")
        return f"integer {lo}..{hi}" if lo is not None and hi is not None else "integer" + (f" >= {lo}" if lo is not None else "")
    if kind == "object":
        return "object {" + ", ".join(schema.get("properties", {})) + "}"
    return kind or "value"


def entry(kind, name):
    from ..catalog import OPS
    spec = OPS[kind][name]
    example = spec["example"]
    return {
        "op": name, "kind": kind, "summary": spec["summary"], "group": spec["group"],
        "required": {k: {"type": type_text(s), "doc": doc} for k, (s, doc) in spec["required"].items()},
        "optional": {k: {"type": type_text(s), "doc": doc, "default": default}
                     for k, (s, doc, default) in spec["optional"].items() if k != "note"},
        "notes": spec["notes"],
        "example": {"version": 1, **{k: v for k, v in example.items() if v},
                    "exports": [{"solid": "part", "path": "part.stl"}]},
    }


def entry_text(item):
    import json
    lines = [f"## {item['op']} ({item['kind'][:-1]})", "", item["summary"], ""]
    if item["required"]:
        lines.append("Required:")
        lines += [f"  {k}: {v['type']} - {v['doc']}" for k, v in item["required"].items()]
    if item["optional"]:
        lines.append("Optional:")
        lines += [f"  {k}: {v['type']} - {v['doc']}" + (f" (default {v['default']})" if v["default"] is not None else "")
                  for k, v in item["optional"].items()]
    if item["notes"]:
        lines += ["", item["notes"]]
    lines += ["", "Example:", json.dumps(item["example"], indent=2), ""]
    return "\n".join(lines)


def overview():
    from ..catalog import OPS, RECIPE_FIELDS
    from ..expr import FUNCTIONS
    groups = {}
    for kind, ops in OPS.items():
        for name, spec in ops.items():
            groups.setdefault(kind, {}).setdefault(spec["group"], {})[name] = spec["summary"]
    return {"ok": True, "version": VERSION, "recipe_fields": RECIPE_FIELDS, "operations": groups,
            "functions": {name: doc for name, (_, _, _, doc) in FUNCTIONS.items()},
            "constants": ["pi", "tau", "e"],
            "next": "cdcad describe OP for properties and an example"}


def describe(args):
    from ..catalog import OPS
    if not args.op:
        result = overview()
        if args.text:
            lines = [f"CDCAD {VERSION} operations (cdcad describe OP for details)", ""]
            for kind, groups in result["operations"].items():
                lines.append(kind.upper())
                for group, ops in groups.items():
                    lines.append(f"  {group}")
                    lines += [f"    {name:16} {summary}" for name, summary in ops.items()]
                lines.append("")
            lines.append("Expression functions: " + ", ".join(result["functions"]) + "; constants pi, tau, e")
            return Text("\n".join(lines) + "\n")
        return result
    kinds = [args.kind] if args.kind else [k for k in OPS if args.op in OPS[k]]
    kinds = [k for k in kinds if args.op in OPS[k]]
    if not kinds:
        names = sorted({n for ops in OPS.values() for n in ops})
        raise CADError(f"No operation named {args.op!r}", hint=suggest(args.op, names) or "run cdcad describe to list them")
    items = [entry(k, args.op) for k in kinds]
    if args.text:
        return Text("\n".join(entry_text(item) for item in items))
    return {"ok": True, "operations": items}


GUIDE = """# CDCAD in one page

CDCAD builds meshes (STL, 3MF, OBJ, PLY, OFF, GLB) from JSON recipes. A recipe names 2D
profiles and 3D solids; nodes refer to each other by name, in any order.

## Workflow
1. Write recipe.json (skeleton below). `cdcad describe` lists operations; `cdcad describe OP`
   shows one with an example you can copy.
2. `cdcad validate recipe.json` checks schema, names and expressions without meshing; it
   also warns about unused parameters and nodes that no export reaches.
3. `cdcad render recipe.json -o view.png` draws the first exported solid (iso, front, top and
   right views) without writing a model. Look at it.
4. `cdcad build recipe.json --out-dir out` writes the exports and prints a JSON report with
   volume, area, bounds, extents, centre of mass and whether each mesh is a closed solid.
5. Add `checks` to the recipe so the build itself verifies what matters (a volume, a size, a
   fit on the printer bed); a failing check stops the build before any file is written.

Every command prints one JSON object. On failure it exits 2 and prints {"ok": false, "error",
"type", "hint"} to stderr; the hint usually names the fix (a misspelling, the right place).

## Skeleton
{
  "version": 1,
  "units": "mm",
  "params": {"w": 60, "d": 40, "h": 6, "r": 4},
  "profiles": {
    "outline": {"op": "rectangle", "size": ["=w", "=d"], "radius": 3},
    "hole": {"op": "circle", "radius": "=r", "center": ["=w/4", 0]},
    "plate": {"op": "difference", "inputs": ["outline", "hole"]}
  },
  "solids": {"part": {"op": "extrude", "profile": "plate", "height": "=h"}},
  "exports": [{"solid": "part", "path": "part.stl"}],
  "checks": [{"solid": "part", "volume": {"min": 1000}, "size_z": {"approx": "=h"}}]
}

## Conventions
- Lengths are in `units` (mm by default); exported files are always in mm.
- Right-handed axes, Z up. Angles in node properties are degrees; inside expressions
  sin/cos/tan take radians (use radians(deg)).
- box, cylinder, cone, sphere and torus are centred on the origin. extrude starts at z
  (default 0) and grows up. revolve turns profile X (radius, >= 0) and Y (height) about Z.
- A profile is 2D; turn it into a solid with extrude, revolve, loft or sweep.
- difference subtracts every later input from the first. union fuses; combine just collects.
- Booleans need closed solids; overlap parts slightly instead of touching faces or edges.
- transform applies scale, then rotate (XYZ degrees), then translate, about the origin.

## Expressions
- Any string value starting with = is arithmetic on params: "=w/2 + 3".
- Field properties (implicit f, graph f, function f, curve x/y, sweep curve x/y/z) are
  formulas of their variables (x, y, z or t) and may use params too: "x**2 + y**2 - r**2".
- Operators + - * / // % ** (not ^), comparisons, `a if cond else b`, and/or/not.
- Functions: FUNCTIONS. Constants: pi, tau, e.

## Maths-driven shapes
- function: area under y = f(x), or between x = f(y) and an axis for a revolve (vase, knob).
- curve: closed parametric outline (x(t), y(t)): gears, cams, roses, superellipses.
- graph: solid under z = f(x, y). implicit: solid where f(x, y, z) < 0 (gyroids, blobs;
  smin(a, b, k) blends fields). sweep: profile along a 3D path or curve (springs, threads,
  knots, pipes). extrude twist/scale: twisted vases, tapers. text: embossed labels.

## Checking a model
- `cdcad inspect part.stl --density 1.24 --bed 220 220 250` reports mass and printer fit.
- Report fields: is_volume (closed, consistently wound, positive volume), volume, area,
  extents, bounds, center_mass, components (separate pieces), mass_g with a density.

## Operations
OPERATIONS
"""


def guide_text():
    from ..catalog import OPS
    from ..expr import FUNCTIONS
    lines = []
    for kind, ops in OPS.items():
        lines.append(f"{kind}:")
        for name, spec in ops.items():
            required = ", ".join(spec["required"])
            lines.append(f"- {name}({required}): {spec['summary']}")
    return (GUIDE.replace("FUNCTIONS", ", ".join(FUNCTIONS)).replace("OPERATIONS", "\n".join(lines))
            .replace("CDCAD in one page", f"CDCAD {VERSION} in one page"))


def guide(args):
    text = guide_text()
    return Text(text) if args.text else {"ok": True, "version": VERSION, "format": "markdown", "text": text}
