"""Parameters, dimension expressions and recipe input.

Two kinds of expression appear in a recipe:

* a dimension: any string value starting with '=' (``"=w/2"``) is evaluated once, with
  the parameters, into a number;
* a field: a property that the schema marks as a function of variables (``"f"`` of an
  implicit solid is a function of x, y and z). It stays text, with the parameters it
  reads written in as numbers, and is evaluated on a grid by the geometry engine.
"""
from ._common import CADError, parse_sets, read_json, read_stdin_json, suggest
from .expr import check, evaluate, evaluate_checked, field_text

RESERVED = {"pi", "tau"}


def expression(text, params):
    """Evaluate one arithmetic expression (without its '=') with the given parameters."""
    return evaluate(text, params)


def resolve_parameters(raw_params, overrides=None, *, dependencies=None):
    """Resolve parameter values in dependency order.

    ``dependencies``, when given, receives {name: names it reads}.
    """
    if not isinstance(raw_params, dict):
        raise CADError("params must be an object")
    unknown = set(overrides or ()) - raw_params.keys()
    if unknown:
        first = sorted(unknown)[0]
        raise CADError(f"Unknown parameter override: {', '.join(sorted(unknown))}",
                       hint=suggest(first, raw_params) or f"declared parameters: {', '.join(raw_params) or 'none'}")
    pending = dict(raw_params)
    pending.update(overrides or {})
    checked, reads = {}, {}
    for name, value in pending.items():
        if not isinstance(name, str) or not name.isidentifier() or name in RESERVED:
            raise CADError(f"Invalid or reserved parameter name: {name}",
                           hint="parameter names are identifiers like width or hole_r; pi and tau are constants")
        if isinstance(value, str) and value.startswith("="):
            try:
                checked[name] = check(value[1:], pending.keys())
            except CADError as exc:
                raise CADError(f"Parameter {name}: {exc}", hint=exc.hint) from exc
            reads[name] = checked[name].names & pending.keys()
        elif type(value) not in (int, float) or value != value or value in (float("inf"), float("-inf")):
            raise CADError(f"Parameter {name} must be a finite number or =expression")
        else:
            reads[name] = set()
    if dependencies is not None:
        dependencies.update(reads)
    order, state = [], {}

    def visit(name, trail):
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            cycle = trail[trail.index(name):] + [name]
            raise CADError(f"Unresolvable parameter expressions: cycle {' -> '.join(cycle)}")
        state[name] = "active"
        for dep in sorted(reads[name]):
            visit(dep, trail + [name])
        state[name] = "done"
        order.append(name)

    for name in pending:
        visit(name, [])
    values = {}
    for name in order:
        value = pending[name]
        values[name] = evaluate_checked(checked[name], values) if name in checked else value
    return {name: values[name] for name in pending}


def _variables(schema):
    """Variables of a field-expression property (also inside a oneOf), else None."""
    if not isinstance(schema, dict):
        return None
    if "x-cdcad-variables" in schema:
        return schema["x-cdcad-variables"]
    for choice in schema.get("oneOf", ()):
        if isinstance(choice, dict) and "x-cdcad-variables" in choice:
            return choice["x-cdcad-variables"]
    return None


class _Resolver:
    def __init__(self, params):
        self.params = params
        self.used = set()

    def value(self, obj, schema=None, where=""):
        if isinstance(schema, dict) and schema.get("x-cdcad-literal"):
            return obj
        variables = _variables(schema)
        if variables is not None and isinstance(obj, str):
            try:
                text, names = field_text(obj, variables, self.params)
            except CADError as exc:
                raise CADError(f"{where}: {exc}", hint=exc.hint or
                               f"this field is a function of {', '.join(variables)}; parameters may be used too") from exc
            self.used |= names
            return text
        if isinstance(obj, str) and obj.startswith("="):
            try:
                checked = check(obj[1:], self.params.keys())
                value = evaluate_checked(checked, self.params)
            except CADError as exc:
                raise CADError(f"{where}: {exc}" if where else str(exc), hint=exc.hint) from exc
            self.used |= checked.names & self.params.keys()
            return value
        if isinstance(obj, list):
            items = schema.get("items") if isinstance(schema, dict) else None
            return [self.value(x, items, f"{where}/{i}") for i, x in enumerate(obj)]
        if isinstance(obj, dict):
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            return {k: self.value(v, props.get(k), f"{where}/{k}") for k, v in obj.items()}
        if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
            raise CADError("Non-finite numbers are not allowed")
        return obj


def resolve_details(raw, overrides=None):
    """Resolve a raw recipe; returns (resolved recipe, info) where info has the
    parameters each parameter reads and the parameters node values read."""
    from .schema import PROFILE_OPS, SOLID_OPS, validate_resolved
    if not isinstance(raw, dict):
        raise CADError("Recipe must be a JSON object")
    dependencies = {}
    params = resolve_parameters(raw.get("params", {}), overrides, dependencies=dependencies)
    resolver = _Resolver(params)
    result = {}
    for key, value in raw.items():
        if key == "params":
            result[key] = params
        elif key in ("profiles", "solids") and isinstance(value, dict):
            ops = PROFILE_OPS if key == "profiles" else SOLID_OPS
            nodes = {}
            for name, node in value.items():
                spec = ops.get(node.get("op")) if isinstance(node, dict) else None
                schema = {"properties": spec[1]} if spec else None
                nodes[name] = resolver.value(node, schema, f"{key}/{name}")
            result[key] = nodes
        else:
            result[key] = resolver.value(value, None, key)
    if "params" not in result and "params" in raw:
        result["params"] = params
    validate_resolved(result)
    used = set(resolver.used)
    frontier = list(used)
    while frontier:  # a parameter read by a used parameter is used too
        for dep in dependencies.get(frontier.pop(), ()):
            if dep not in used:
                used.add(dep)
                frontier.append(dep)
    return result, {"parameter_reads": dependencies, "used_parameters": used}


def resolve_recipe(raw, overrides=None):
    """Resolve dimension expressions and validate the resulting recipe."""
    return resolve_details(raw, overrides)[0]


def read_raw(path):
    return read_stdin_json() if str(path) == "-" else read_json(path)


def read_recipe(path, sets=()):
    """Read a recipe file (or '-' for stdin) and resolve parameter overrides."""
    return resolve_recipe(read_raw(path), parse_sets(sets))
