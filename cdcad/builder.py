"""Build recipes from Python.

    from cdcad import Recipe, fx

    r = Recipe(params={"w": 60, "d": 40, "h": 6})
    w, h = r.p.w, r.p.h                      # parameters as expressions
    plate = r.rectangle([w, r.p.d], radius=3) - r.circle(4, center=[w/4, 0])
    part = plate.extrude(height=h)
    r.export(part, "plate.stl")
    r.check(part, volume={"min": 1000})
    report = r.build("out")                  # same report as `cdcad build`
    mesh = r.mesh(part, w=90)                # a trimesh.Trimesh in mm, with an override

The result is an ordinary JSON recipe (``r.to_dict()``, ``r.save(path)``): nodes get
names, parameter arithmetic becomes "=..." text, and numpy values become plain numbers.
Every operation in ``cdcad describe`` is available as a method; positional arguments
fill the required properties in order (or the inputs of union/difference/...).
"""
import json
import numbers
from pathlib import Path

from ._common import CADError, UNITS, suggest
from .catalog import OPS


class Expr:
    """Parameter arithmetic. ``r.p.w / 4 + 1`` becomes the dimension "=(w)/4+1"."""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    @staticmethod
    def wrap(value):
        if isinstance(value, Expr):
            return f"({value.text})"
        value = _plain(value)
        if not isinstance(value, numbers.Real) or isinstance(value, bool):
            raise CADError(f"Cannot use {value!r} in a dimension expression")
        return repr(value) if value >= 0 else f"({value!r})"

    def _op(self, other, symbol, reverse=False):
        a, b = Expr.wrap(self), Expr.wrap(other)
        return Expr(f"{b}{symbol}{a}" if reverse else f"{a}{symbol}{b}")

    def __add__(self, o): return self._op(o, "+")
    def __radd__(self, o): return self._op(o, "+", True)
    def __sub__(self, o): return self._op(o, "-")
    def __rsub__(self, o): return self._op(o, "-", True)
    def __mul__(self, o): return self._op(o, "*")
    def __rmul__(self, o): return self._op(o, "*", True)
    def __truediv__(self, o): return self._op(o, "/")
    def __rtruediv__(self, o): return self._op(o, "/", True)
    def __floordiv__(self, o): return self._op(o, "//")
    def __rfloordiv__(self, o): return self._op(o, "//", True)
    def __mod__(self, o): return self._op(o, "%")
    def __rmod__(self, o): return self._op(o, "%", True)
    def __pow__(self, o): return self._op(o, "**")
    def __rpow__(self, o): return self._op(o, "**", True)
    def __neg__(self): return Expr(f"-({self.text})")
    def __pos__(self): return self

    def __str__(self):
        return "=" + self.text

    def __repr__(self):
        return f"Expr({self.text!r})"

    def __bool__(self):
        raise CADError("A parameter expression has no truth value in Python; write the condition in the "
                       "expression instead, e.g. fx.max(a, b) or a dimension string '=a if b > 2 else 3'")


class _Functions:
    """``fx.sin(r.p.a)`` and the other expression functions, for building dimensions."""

    def __getattr__(self, name):
        from .expr import FUNCTIONS
        if name not in FUNCTIONS:
            raise AttributeError(name)
        return lambda *args: Expr(f"{name}({', '.join(Expr.wrap(a) for a in args)})")

    def __dir__(self):
        from .expr import FUNCTIONS
        return list(FUNCTIONS)


fx = _Functions()


def _plain(value):
    """numpy scalars and arrays to Python numbers and lists."""
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        return value.tolist()
    if hasattr(value, "item") and not isinstance(value, (str, bytes, list, tuple, dict)):
        return value.item()
    return value


def _json(value):
    value = _plain(value)
    if isinstance(value, Node):
        return value.name
    if isinstance(value, Expr):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, float) and value != value:
        raise CADError("NaN cannot be written into a recipe")
    return value


class Node:
    """A named profile or solid in a Recipe. Operators: ``a | b`` or ``a + b`` union,
    ``a - b`` difference, ``a & b`` intersection."""

    def __init__(self, recipe, kind, name):
        self.recipe, self.kind, self.name = recipe, kind, name

    def __repr__(self):
        return f"<{self.kind[:-1]} {self.name!r}: {self.recipe.data[self.kind][self.name]['op']}>"

    def __str__(self):
        return self.name

    def rename(self, name):
        """Give the node a meaningful name (references to it are updated). Returns self."""
        data = self.recipe.data
        if name == self.name:
            return self
        if name in data[self.kind]:
            raise CADError(f"{self.kind[:-1].capitalize()} {name!r} already exists")
        old = self.name
        data[self.kind][name] = data[self.kind].pop(old)
        for kind, nodes in (("profiles", data["profiles"]), ("solids", data["solids"])):
            for node in nodes.values():
                if kind == self.kind:
                    for field in ("input", "target"):
                        if node.get(field) == old:
                            node[field] = name
                    if "inputs" in node:
                        node["inputs"] = [name if ref == old else ref for ref in node["inputs"]]
                if self.kind == "profiles":
                    if node.get("profile") == old:
                        node["profile"] = name
                    for section in node.get("sections", []):
                        if section.get("profile") == old:
                            section["profile"] = name
        if self.kind == "solids":
            for item in data["exports"] + data.get("checks", []):
                if item["solid"] == old:
                    item["solid"] = name
        self.name = name
        return self

    def _boolean(self, op, other):
        if not isinstance(other, Node) or other.kind != self.kind:
            raise CADError(f"Cannot combine a {self.kind[:-1]} with {other!r}")
        return self.recipe.add(self.kind, op, inputs=[self, other])

    def __or__(self, other): return self._boolean("union", other)
    def __add__(self, other): return self._boolean("union", other)
    def __sub__(self, other): return self._boolean("difference", other)
    def __and__(self, other): return self._boolean("intersection", other)

    # shared shortcuts
    def translate(self, *offset):
        offset = list(offset[0]) if len(offset) == 1 else list(offset)
        return self.recipe.add(self.kind, "transform", input=self, translate=offset)

    def rotate(self, *angles):
        if self.kind == "profiles":
            return self.recipe.add(self.kind, "transform", input=self, rotate=angles[0])
        angles = list(angles[0]) if len(angles) == 1 else list(angles)
        return self.recipe.add(self.kind, "transform", input=self, rotate=angles)

    def scale(self, *factors):
        size = 2 if self.kind == "profiles" else 3
        factors = list(factors[0]) if len(factors) == 1 and isinstance(factors[0], (list, tuple)) else list(factors)
        if len(factors) == 1:
            factors = factors*size
        return self.recipe.add(self.kind, "transform", input=self, scale=factors)

    def hull(self, *others):
        return self.recipe.add(self.kind, "hull", inputs=[self, *others])

    def array(self, count, **props):
        return self.recipe.add(self.kind, "array", input=self, count=count, **props)

    # profiles to solids
    def extrude(self, height, **props):
        return self.recipe.add("solids", "extrude", profile=self, height=height, **props)

    def revolve(self, **props):
        return self.recipe.add("solids", "revolve", profile=self, **props)

    def sweep(self, **props):
        return self.recipe.add("solids", "sweep", profile=self, **props)

    def offset(self, distance, **props):
        return self.recipe.add("profiles", "offset", input=self, distance=distance, **props)

    # solids
    def mirror(self, normal=(1, 0, 0), **props):
        return self.recipe.add("solids", "mirror", input=self, normal=list(normal), **props)

    def cut(self, origin, normal, **props):
        return self.recipe.add("solids", "cut", input=self, origin=list(origin), normal=list(normal), **props)

    def align(self, target, **props):
        return self.recipe.add("solids", "align", input=self, target=target, **props)


class _Params:
    def __init__(self, recipe):
        object.__setattr__(self, "_recipe", recipe)

    def __getattr__(self, name):
        params = self._recipe.data.get("params", {})
        if name not in params:
            raise AttributeError(f"No parameter {name!r}" + (f"; {suggest(name, params)}" if suggest(name, params) else ""))
        return Expr(name)

    def __setattr__(self, name, value):
        self._recipe.param(name, value)


class Recipe:
    """A recipe under construction. Methods named after operations add nodes and return
    them; ``build``, ``mesh``, ``render`` and ``save`` use it."""

    def __init__(self, units="mm", params=None, description=None, density=None):
        if units not in UNITS:
            raise CADError(f"Unknown units {units!r}", hint=f"use one of {', '.join(UNITS)}")
        self.data = {"version": 1, "units": units, "params": {}, "profiles": {}, "solids": {}, "exports": []}
        if description:
            self.data["description"] = description
        if density is not None:
            self.data["density"] = density
        self.p = _Params(self)
        for name, value in (params or {}).items():
            self.param(name, value)
        self._counts = {}

    def param(self, name, value):
        """Declare a parameter (a number or "=expression"); returns it as an expression."""
        value = _plain(value)
        self.data["params"][name] = str(value) if isinstance(value, Expr) else value
        return Expr(name)

    def _name(self, kind, op, name):
        if name is not None:
            if name in self.data[kind]:
                raise CADError(f"{kind[:-1].capitalize()} {name!r} already exists")
            return name
        while True:
            self._counts[op] = self._counts.get(op, 0) + 1
            candidate = f"{op}{self._counts[op]}"
            if candidate not in self.data[kind]:
                return candidate

    def add(self, kind, op, name=None, **props):
        """Add a node; ``kind`` is "profiles" or "solids". Returns its Node."""
        if op not in OPS[kind]:
            other = "solids" if kind == "profiles" else "profiles"
            hint = (f"{op} is a {other[:-1]} operation" if op in OPS[other] else suggest(op, OPS[kind]))
            raise CADError(f"Unknown {kind[:-1]} operation {op!r}", hint=hint)
        for field in ("input", "target"):
            if isinstance(props.get(field), Node) and props[field].kind != kind:
                raise CADError(f"{op}: {field} must be a {kind[:-1]}, got {props[field]!r}")
        if isinstance(props.get("profile"), Node) and props["profile"].kind != "profiles":
            raise CADError(f"{op}: profile must be a profile, got {props['profile']!r}")
        for ref in props.get("inputs", []):
            if isinstance(ref, Node) and ref.kind != kind:
                raise CADError(f"{op}: inputs must all be {kind}, got {ref!r}")
        name = self._name(kind, op, name)
        self.data[kind][name] = {"op": op, **{k: _json(v) for k, v in props.items()}}
        return Node(self, kind, name)

    def profile(self, op, *args, name=None, **props):
        return self._call("profiles", op, args, name, props)

    def solid(self, op, *args, name=None, **props):
        return self._call("solids", op, args, name, props)

    def _call(self, kind, op, args, name, props):
        if op not in OPS[kind]:
            return self.add(kind, op, name, **props)  # raises with a hint
        required = list(OPS[kind][op]["required"])
        takes_inputs = "inputs" in required or ("inputs" in OPS[kind][op]["optional"] and "input" not in props)
        if args and takes_inputs and "inputs" not in props:
            props["inputs"] = list(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
        elif args:
            free = [key for key in required if key not in props]
            if len(args) > len(free):
                raise CADError(f"{op} takes {len(free)} positional argument(s): {', '.join(free)}")
            props.update(zip(free, args))
        return self.add(kind, op, name, **props)

    def __getattr__(self, op):
        if op.startswith("_"):
            raise AttributeError(op)
        in_profiles, in_solids = op in OPS["profiles"], op in OPS["solids"]
        if not (in_profiles or in_solids):
            names = sorted(set(OPS["profiles"]) | set(OPS["solids"]))
            raise AttributeError(f"No operation {op!r}" + (f"; {suggest(op, names)}" if suggest(op, names) else ""))

        def call(*args, name=None, **props):
            kind = "profiles" if in_profiles else "solids"
            if in_profiles and in_solids:
                refs = [a for a in args if isinstance(a, Node)] + [props.get(k) for k in ("input", "inputs") if k in props]
                refs = [r for ref in refs for r in (ref if isinstance(ref, (list, tuple)) else [ref])]
                kinds = {r.kind for r in refs if isinstance(r, Node)}
                if len(kinds) != 1:
                    raise CADError(f"{op} exists for profiles and solids; call r.profile({op!r}, ...) or r.solid({op!r}, ...)")
                kind = kinds.pop()
            return self._call(kind, op, args, name, props)
        call.__name__ = op
        return call

    def export(self, solid, path, allow_open=False):
        entry = {"solid": _json(solid), "path": str(path)}
        if allow_open:
            entry["allow_open"] = True
        self.data["exports"].append(entry)
        return self

    def check(self, solid, **props):
        """Add a recipe check, e.g. ``r.check(part, volume={"min": 10}, fits=[220, 220, 250])``."""
        self.data.setdefault("checks", []).append({"solid": _json(solid), **{k: _json(v) for k, v in props.items()}})
        return self

    def to_dict(self):
        data = json.loads(json.dumps(self.data, allow_nan=False))
        if not data["params"]:
            del data["params"]
        if not data["profiles"]:
            del data["profiles"]
        return data

    def save(self, path):
        from ._common import atomic_bytes, json_bytes
        atomic_bytes(path, json_bytes(self.to_dict()))
        return Path(path)

    def resolve(self, **overrides):
        from .recipe import resolve_recipe
        return resolve_recipe(self.to_dict(), overrides or None)

    def build(self, out_dir, base=".", force=False, cache_dir=None, overrides=None, targets=None):
        from .build import build
        return build(self.resolve(**(overrides or {})), Path(base), Path(out_dir), targets=targets, force=force,
                     cache_dir=cache_dir)

    def mesh(self, solid, base=".", **overrides):
        """The solid as a trimesh.Trimesh in millimetres."""
        from .engine import Engine
        recipe = self.resolve(**overrides)
        mesh = Engine(recipe, Path(base)).get("solids", _json(solid)).copy()
        mesh.apply_scale(UNITS[recipe.get("units", "mm")])
        return mesh

    def render(self, solid, path, view="grid", size=900, base=".", **overrides):
        from ._common import atomic_bytes
        from .render import render_png
        atomic_bytes(path, render_png(self.mesh(solid, base, **overrides), view, size, title=_json(solid)))
        return Path(path)
