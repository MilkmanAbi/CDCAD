"""Arithmetic expressions and recipe input."""
import ast
import math

from ._common import CADError, parse_sets, read_json, read_stdin_json
from .schema import validate_resolved


def expression(text, params):
    """Evaluate a tiny arithmetic AST; never execute Python or allow attributes/indexing."""
    funcs = {k: getattr(math, k) for k in ("sin", "cos", "tan", "sqrt", "ceil", "floor", "radians", "degrees")}
    funcs.update(min=min, max=max, abs=abs, round=round)
    operators = {ast.Add: lambda a, b: a+b, ast.Sub: lambda a, b: a-b,
                 ast.Mult: lambda a, b: a*b, ast.Div: lambda a, b: a/b,
                 ast.Mod: lambda a, b: a % b, ast.Pow: lambda a, b: a**b}
    if len(text) > 1024:
        raise CADError("Expression exceeds 1024 characters")
    def walk(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            value = n.value
        elif isinstance(n, ast.Name) and n.id in params.keys() | {"pi", "tau"}:
            value = params[n.id] if n.id in params else getattr(math, n.id)
        elif isinstance(n, ast.BinOp) and type(n.op) in operators:
            a, b = walk(n.left), walk(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise CADError("Power exponent exceeds 100")
            value = operators[type(n.op)](a, b)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            value = walk(n.operand) * (-1 if isinstance(n.op, ast.USub) else 1)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs and not n.keywords:
            value = funcs[n.func.id](*[walk(a) for a in n.args])
        else:
            raise CADError(f"Unsupported expression: {text}")
        if not isinstance(value, (float, int)) or not math.isfinite(value) or abs(value) > 1e100:
            raise CADError("Expression result is not a finite, bounded number")
        return value
    try:
        return walk(ast.parse(text, mode="eval").body)
    except CADError:
        raise
    except (ArithmeticError, ValueError, TypeError, SyntaxError, RecursionError) as exc:
        # Division by zero, math domain errors, wrong argument counts and syntax
        # errors are bad input, so report them as the documented error type.
        raise CADError(f"Invalid expression {text!r}: {exc}") from exc


def resolve_parameters(raw_params, overrides=None):
    if not isinstance(raw_params, dict):
        raise CADError("params must be an object")
    unknown = set(overrides or ()) - raw_params.keys()
    if unknown:
        raise CADError(f"Unknown parameter override: {', '.join(sorted(unknown))}")
    pending = dict(raw_params)
    pending.update(overrides or {})
    params = {}
    while pending:
        progress = False
        for name, value in list(pending.items()):
            if not name.isidentifier() or name in {"pi", "tau"}:
                raise CADError(f"Invalid or reserved parameter name: {name}")
            try:
                if isinstance(value, str) and value.startswith("="):
                    value = expression(value[1:], params)
                elif type(value) not in (int, float) or not math.isfinite(value):
                    raise CADError(f"Parameter {name} must be a finite number or =expression")
            except CADError as exc:
                if "Unsupported expression" in str(exc):
                    continue
                raise
            params[name] = value
            del pending[name]
            progress = True
        if not progress:
            raise CADError(f"Unresolvable parameter expressions (missing/cyclic/unsupported): {list(pending)}")
    return params


def resolve_recipe(raw, overrides=None):
    """Resolve dimension expressions and validate the resulting recipe."""
    if not isinstance(raw, dict):
        raise CADError("Recipe must be a JSON object")
    params = resolve_parameters(raw.get("params", {}), overrides)

    def resolve(obj):
        if isinstance(obj, str) and obj.startswith("="):
            return expression(obj[1:], params)
        if isinstance(obj, list):
            return [resolve(x) for x in obj]
        if isinstance(obj, dict):
            return {k: resolve(v) for k, v in obj.items()}
        if isinstance(obj, float) and not math.isfinite(obj):
            raise CADError("Non-finite numbers are not allowed")
        return obj
    result = resolve({**raw, "params": params})
    validate_resolved(result)
    return result


def read_recipe(path, sets=()):
    """Read a recipe file (or '-' for stdin) and resolve parameter overrides."""
    raw = read_stdin_json() if str(path) == "-" else read_json(path)
    return resolve_recipe(raw, parse_sets(sets))
