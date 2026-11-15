"""JSON Schema for resolved recipes, generated from the operation catalog, and validation
with messages that say what to change."""
import functools

from jsonschema import Draft202012Validator

from ._common import CADError, UNITS, suggest
from .catalog import ANCHOR, BOOL, CHECK, NUM, OPS, POS, RECIPE_FIELDS, STR, TRANSFORM, V2, array, vector

# ANCHOR, TRANSFORM and the type helpers are used by the commands and project modules.
__all__ = ["ANCHOR", "BOOL", "KIND_OPS", "NUM", "PROFILE_OPS", "SOLID_OPS", "STR", "TRANSFORM", "array",
           "recipe_schema", "validate_resolved", "vector"]

POINTS = array(V2, 3)


def _documented(schema, doc, default=None, has_default=False):
    result = {**schema, "description": doc}
    if has_default and default is not None and not (isinstance(default, str) and schema.get("type") != "string"):
        result["default"] = default
    return result


def _table(kind):
    table = {}
    for name, entry in OPS[kind].items():
        props = {key: _documented(schema, doc) for key, (schema, doc) in entry["required"].items()}
        props.update({key: _documented(schema, doc, default, True)
                      for key, (schema, doc, default) in entry["optional"].items()})
        table[name] = (list(entry["required"]), props)
    return table


# op name -> (required property names, {property: schema})
PROFILE_OPS = _table("profiles")
SOLID_OPS = _table("solids")
KIND_OPS = {"profiles": PROFILE_OPS, "solids": SOLID_OPS}


def node_schema(kind, ops):
    return {"oneOf": [{"type": "object", "description": OPS[kind][op]["summary"],
                       "properties": {"op": {"const": op}, **props},
                       "required": ["op", *required], "additionalProperties": False}
                      for op, (required, props) in ops.items()]}


def recipe_schema():
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "CDCAD resolved recipe v1 (expressions are resolved before validation)",
            "type": "object", "additionalProperties": False,
            "required": ["version", "solids", "exports"],
            "properties": {
                "version": {"const": 1, "description": RECIPE_FIELDS["version"]},
                "units": {"enum": list(UNITS), "description": RECIPE_FIELDS["units"]},
                "description": {"type": "string", "description": RECIPE_FIELDS["description"]},
                "params": {"type": "object", "additionalProperties": NUM, "description": RECIPE_FIELDS["params"]},
                "density": {**POS, "description": RECIPE_FIELDS["density"]},
                "profiles": {"type": "object", "additionalProperties": node_schema("profiles", PROFILE_OPS),
                             "description": RECIPE_FIELDS["profiles"]},
                "solids": {"type": "object", "additionalProperties": node_schema("solids", SOLID_OPS),
                           "description": RECIPE_FIELDS["solids"]},
                "exports": {**array({"type": "object", "additionalProperties": False,
                                     "properties": {"solid": STR, "path": STR, "allow_open": BOOL},
                                     "required": ["solid", "path"]}, 0),
                            "description": RECIPE_FIELDS["exports"]},
                "checks": {**array(CHECK, 0), "description": RECIPE_FIELDS["checks"]}}}


@functools.lru_cache(maxsize=1)
def validators():
    # Validate each node against its own operation only, instead of trying every
    # alternative of the oneOf for every node of a large model.
    schema = recipe_schema()
    nodes = {}
    for kind, ops in KIND_OPS.items():
        nodes[kind] = {op: Draft202012Validator(choice)
                       for op, choice in zip(ops, schema["properties"][kind]["additionalProperties"]["oneOf"])}
        schema["properties"][kind]["additionalProperties"] = {"type": "object"}
    return Draft202012Validator(schema), nodes


def _first_error(validator, value, prefix):
    error = next(iter(sorted(validator.iter_errors(value), key=lambda e: [str(p) for p in e.absolute_path])), None)
    if error:
        location = prefix + "/".join(map(str, error.absolute_path))
        raise CADError(f"Schema at {location or '/'}: {error.message}")


def _check_node(kind, name, node):
    where = f"{kind}/{name}"
    other = "solids" if kind == "profiles" else "profiles"
    if not isinstance(node, dict):
        raise CADError(f"Schema at {where}: a node must be an object with an op")
    op = node.get("op")
    if op not in KIND_OPS[kind]:
        if op in KIND_OPS[other]:
            hint = (f"'{op}' is a {other[:-1]} operation; define it under {other}"
                    + (" and extrude, revolve or sweep the profile" if other == "profiles" else ""))
        elif op is None:
            hint = f"add \"op\": one of {', '.join(KIND_OPS[kind])}"
        else:
            hint = suggest(op, KIND_OPS[kind]) or f"{kind} operations: {', '.join(KIND_OPS[kind])}"
        raise CADError(f"Unknown operation at {where}: {op}", hint=hint)
    required, props = KIND_OPS[kind][op]
    unknown = sorted(set(node) - set(props) - {"op"})
    if unknown:
        hint = suggest(unknown[0], props) or f"{op} accepts: {', '.join(props)}"
        raise CADError(f"Schema at {where}: Additional properties are not allowed for {op}: "
                       f"{', '.join(repr(k) for k in unknown)}", hint=hint)
    missing = [key for key in required if key not in node]
    if missing:
        raise CADError(f"Schema at {where}: {op} is missing required {', '.join(repr(k) for k in missing)}",
                       hint=f"{op} needs {', '.join(required)}; see: cdcad describe {op}")


def validate_resolved(result):
    root, nodes = validators()
    if not isinstance(result, dict):
        raise CADError("Recipe must be a JSON object")
    unknown = sorted(set(result) - set(recipe_schema()["properties"]))
    if unknown:
        raise CADError(f"Schema at /: Additional properties are not allowed: {', '.join(repr(k) for k in unknown)}",
                       hint=suggest(unknown[0], RECIPE_FIELDS) or f"recipe fields: {', '.join(RECIPE_FIELDS)}")
    _first_error(root, result, "")
    for kind, ops in nodes.items():
        for name, node in result.get(kind, {}).items():
            _check_node(kind, name, node)
            validator = ops[node["op"]]
            _first_error(validator, node, f"{kind}/{name}/")
            # JSON Schema permits 4.0 as an integer. Normalize only integer
            # fields so expression-derived counts work with range/linspace.
            for key, rule in validator.schema["properties"].items():
                if key not in node:
                    continue
                if rule.get("type") == "integer":
                    node[key] = int(node[key])
                elif rule.get("type") == "array" and rule.get("items", {}).get("type") == "integer":
                    node[key] = list(map(int, node[key]))
