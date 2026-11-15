"""Safe arithmetic expressions: one whitelist, two compiled forms.

An expression is parsed with Python's ``ast`` module and every node is checked against
a short whitelist: numbers, names of parameters, constants and field variables, the
operators below, comparisons, ``a if c else b``, ``and``/``or``/``not`` and calls to the
functions in FUNCTIONS. There are no attributes, subscripts, strings, keywords, lambdas
or comprehensions. Only after that check does the tree get compiled, with an empty
``__builtins__``, so nothing outside the whitelist can be reached.

The same checked tree compiles two ways:

* scalar: plain floats through ``math``, used for parameters and dimensions;
* vector: numpy arrays, used to evaluate a field such as f(x, y, z) on a whole grid
  at once (implicit solids, function graphs, parametric curves).
"""
import ast
import functools
import math

from ._common import CADError, suggest

MAX_LENGTH = 1024
MAX_DEPTH = 100
MAX_EXPONENT = 100
LIMIT = 1e100

CONSTANTS = {"pi": math.pi, "tau": math.tau, "e": math.e}

_BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**"}
_COMPARE = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _smoothstep(e0, e1, x):
    t = _clamp((x - e0)/(e1 - e0), 0.0, 1.0)
    return t*t*(3 - 2*t)


def _smin(a, b, k):
    # Polynomial smooth minimum (Inigo Quilez): blends two distance fields over width k.
    h = _clamp(0.5 + 0.5*(b - a)/k, 0.0, 1.0)
    return b + (a - b)*h - k*h*(1 - h)


def _smax(a, b, k):
    return -_smin(-a, -b, k)


def _sign(x):
    return (x > 0) - (x < 0)


def _log(x, base=None):
    return math.log(x) if base is None else math.log(x, base)


MAX_DIGITS = 30


def _digits(digits):
    digits = int(digits)
    if abs(digits) > MAX_DIGITS:
        raise CADError(f"round() digits must be between -{MAX_DIGITS} and {MAX_DIGITS}")
    return digits


def _round(x, digits=None):
    return round(x) if digits is None else round(x, _digits(digits))


# name: (scalar implementation, minimum arguments, maximum arguments or None, one-line doc)
FUNCTIONS = {
    "sin": (math.sin, 1, 1, "sine of radians"), "cos": (math.cos, 1, 1, "cosine of radians"),
    "tan": (math.tan, 1, 1, "tangent of radians"), "asin": (math.asin, 1, 1, "arcsine, radians"),
    "acos": (math.acos, 1, 1, "arccosine, radians"), "atan": (math.atan, 1, 1, "arctangent, radians"),
    "atan2": (math.atan2, 2, 2, "atan2(y, x): angle of the point (x, y), radians"),
    "sinh": (math.sinh, 1, 1, "hyperbolic sine"), "cosh": (math.cosh, 1, 1, "hyperbolic cosine"),
    "tanh": (math.tanh, 1, 1, "hyperbolic tangent"),
    "sqrt": (math.sqrt, 1, 1, "square root"), "cbrt": (math.cbrt, 1, 1, "cube root"),
    "exp": (math.exp, 1, 1, "e to the power x"), "log": (_log, 1, 2, "natural log, or log(x, base)"),
    "log10": (math.log10, 1, 1, "base-10 log"), "log2": (math.log2, 1, 1, "base-2 log"),
    "pow": (None, 2, 2, "pow(a, b) is a ** b"),
    "hypot": (math.hypot, 1, None, "hypot(x, y, ...) Euclidean length"),
    "abs": (abs, 1, 1, "absolute value"), "sign": (_sign, 1, 1, "-1, 0 or 1"),
    "min": (min, 1, None, "smallest argument"), "max": (max, 1, None, "largest argument"),
    "clamp": (_clamp, 3, 3, "clamp(x, lo, hi)"), "lerp": (lambda a, b, t: a + (b - a)*t, 3, 3, "lerp(a, b, t) = a + (b - a) t"),
    "smoothstep": (_smoothstep, 3, 3, "smoothstep(edge0, edge1, x), a 0 to 1 S-curve"),
    "smin": (_smin, 3, 3, "smin(a, b, k): smooth minimum, blends two fields over width k"),
    "smax": (_smax, 3, 3, "smax(a, b, k): smooth maximum"),
    "ceil": (math.ceil, 1, 1, "round up to an integer"), "floor": (math.floor, 1, 1, "round down to an integer"),
    "round": (_round, 1, 2, "round half to even (round(2.5) = 2), or round(x, digits)"),
    "radians": (math.radians, 1, 1, "degrees to radians"), "degrees": (math.degrees, 1, 1, "radians to degrees"),
}


class Checked:
    """A validated expression: its tree, the names it reads, and its source text."""

    def __init__(self, text, tree, names):
        self.text, self.tree, self.names = text, tree, names


def _fail(text, message, hint=None):
    raise CADError(f"Invalid expression {text!r}: {message}", hint=hint)


def check(text, names, *, kind="expression"):
    """Parse and whitelist ``text``; ``names`` are the identifiers it may read
    (parameters and variables; the constants are always allowed)."""
    if not isinstance(text, str) or not text.strip():
        raise CADError(f"Empty {kind}")
    if len(text) > MAX_LENGTH:
        raise CADError(f"Expression exceeds {MAX_LENGTH} characters")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        _fail(text, f"syntax error ({exc.msg})",
              "expressions are arithmetic like =w/2 + 3*sin(a); powers are written **")
    except (RecursionError, MemoryError, ValueError):
        _fail(text, "nested too deeply")
    allowed = set(names) | set(CONSTANTS)
    used = set()

    def unsupported(node, why=None):
        what = type(node).__name__
        raise CADError(f"Unsupported expression: {text} ({why or what + ' is not allowed'})",
                       hint="only numbers, names, + - * / // % **, comparisons, 'a if c else b', "
                            "and/or/not and the math functions listed by 'cdcad describe' are allowed")

    def visit(node, depth):
        if depth > MAX_DEPTH:
            _fail(text, "nested too deeply")
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                unsupported(node, f"the literal {node.value!r} is not a number")
        elif isinstance(node, ast.Name):
            if node.id in FUNCTIONS and node.id not in allowed:
                unsupported(node, f"{node.id} is a function; call it like {node.id}(x)")
            if node.id not in allowed:
                hint = suggest(node.id, sorted(allowed | set(FUNCTIONS)))
                raise CADError(f"Unsupported expression: {text} (unknown name '{node.id}')",
                               hint=hint or (f"known names: {', '.join(sorted(allowed))}" if allowed else None))
            used.add(node.id)
        elif isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.BitXor):
                unsupported(node, "^ is not a power here")  # replaced below with a clearer hint
            if type(node.op) not in _BINARY:
                unsupported(node, f"operator {type(node.op).__name__} is not allowed")
            if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and \
                    isinstance(node.right.value, (int, float)) and abs(node.right.value) > MAX_EXPONENT:
                raise CADError(f"Power exponent exceeds {MAX_EXPONENT}")
            visit(node.left, depth+1)
            visit(node.right, depth+1)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
                unsupported(node)
            visit(node.operand, depth+1)
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                visit(value, depth+1)
        elif isinstance(node, ast.Compare):
            if not all(isinstance(op, _COMPARE) for op in node.ops):
                unsupported(node, "only < <= > >= == != comparisons are allowed")
            visit(node.left, depth+1)
            for value in node.comparators:
                visit(value, depth+1)
        elif isinstance(node, ast.IfExp):
            for part in (node.test, node.body, node.orelse):
                visit(part, depth+1)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                name = node.func.id if isinstance(node.func, ast.Name) else None
                raise CADError(f"Unsupported expression: {text} (unknown function {name or 'call'})",
                               hint=suggest(name, FUNCTIONS) if name else None)
            if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
                unsupported(node, "functions take plain positional arguments")
            _, low, high, _ = FUNCTIONS[node.func.id]
            count = len(node.args)
            if count < low or (high is not None and count > high):
                expected = str(low) if low == high else f"{low} or more" if high is None else f"{low} to {high}"
                _fail(text, f"{node.func.id}() takes {expected} argument{'s' if expected != '1' else ''}, got {count}")
            for arg in node.args:
                visit(arg, depth+1)
        else:
            unsupported(node)

    try:
        visit(tree.body, 0)
    except CADError as exc:
        if "^ is not a power" in str(exc):
            raise CADError(f"Unsupported expression: {text} (^ is bitwise xor in this syntax)",
                           hint="write powers with **, for example x**2 + y**2") from None
        raise
    return Checked(text, tree.body, used)


def _constant(value):
    """An AST literal for a number; negatives become -(literal) so precedence survives
    unparsing, e.g. a**2 with a = -3 becomes (-3) ** 2, not -3 ** 2."""
    if isinstance(value, bool):
        value = int(value)
    if value < 0:
        return ast.UnaryOp(op=ast.USub(), operand=ast.Constant(-value))
    return ast.Constant(value)


class _Inline(ast.NodeTransformer):
    def __init__(self, values):
        self.values = values

    def visit_Name(self, node):
        if node.id in self.values:
            return ast.copy_location(_constant(self.values[node.id]), node)
        return node

    def visit_Call(self, node):
        # The called name is a function even when a parameter has the same name.
        node.args = [self.visit(arg) for arg in node.args]
        return node


def inline(checked, values):
    """Source text of ``checked`` with the given names replaced by their numbers."""
    import copy
    tree = _Inline(values).visit(copy.deepcopy(checked.tree))
    return ast.unparse(ast.fix_missing_locations(tree))


# --- compilation -----------------------------------------------------------------------

def _scalar_pow(a, b):
    if abs(b) > MAX_EXPONENT:
        raise CADError(f"Power exponent exceeds {MAX_EXPONENT}")
    if type(a) is int and type(b) is int and b >= 0:
        # Exact integer powers are fine while small; bound the size before computing, because a
        # chain such as ((9**99)**99)**99 would otherwise build a number with millions of digits.
        if abs(a) > 1 and b*math.log10(abs(a)) > math.log10(LIMIT):
            raise CADError("Expression result is not a finite, bounded number (power too large)")
        return a**b
    result = float(a)**b
    if isinstance(result, complex):
        raise ValueError("a negative number to a fractional power is not real")
    return result


class _Lower(ast.NodeTransformer):
    """Rewrite calls and operators onto helper names; for numpy, also the operators
    that have no elementwise meaning in plain Python (and/or/not, if-else, a < b < c)."""

    def __init__(self, vector):
        self.vector = vector

    def call(self, name, args, node):
        return ast.copy_location(ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=args, keywords=[]), node)

    def visit_Call(self, node):
        self.generic_visit(node)
        return self.call(f"_f_{node.func.id}", node.args, node)

    def visit_Name(self, node):
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Pow):
            return self.call("_f_pow", [node.left, node.right], node)
        return node

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if self.vector and isinstance(node.op, ast.Not):
            return self.call("_not", [node.operand], node)
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        if self.vector:
            return self.call("_and" if isinstance(node.op, ast.And) else "_or", node.values, node)
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        if self.vector and len(node.ops) > 1:
            parts, left = [], node.left
            for op, right in zip(node.ops, node.comparators):
                parts.append(ast.Compare(left=left, ops=[op], comparators=[right]))
                left = right
            return self.call("_and", parts, node)
        return node

    def visit_IfExp(self, node):
        self.generic_visit(node)
        if self.vector:
            return self.call("_where", [node.test, node.body, node.orelse], node)
        return node


@functools.lru_cache(maxsize=1)
def _scalar_namespace():
    space = {f"_f_{name}": fn for name, (fn, *_rest) in FUNCTIONS.items() if fn is not None}
    space["_f_pow"] = _scalar_pow
    space.update(CONSTANTS)
    return space


@functools.lru_cache(maxsize=1)
def _vector_namespace():
    import numpy as np

    def reduce(fn):
        return lambda *args: functools.reduce(fn, args)

    def pow_(a, b):
        return np.power(np.asarray(a, dtype=float), b)

    def log(x, base=None):
        return np.log(x) if base is None else np.log(x)/np.log(base)

    def round_(x, digits=None):
        return np.round(x) if digits is None else np.round(x, _digits(digits))

    def smin(a, b, k):
        h = np.clip(0.5 + 0.5*(b - a)/k, 0.0, 1.0)
        return b + (a - b)*h - k*h*(1 - h)

    def smoothstep(e0, e1, x):
        t = np.clip((x - e0)/(e1 - e0), 0.0, 1.0)
        return t*t*(3 - 2*t)

    functions = {
        "sin": np.sin, "cos": np.cos, "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
        "atan2": np.arctan2, "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh, "sqrt": np.sqrt, "cbrt": np.cbrt,
        "exp": np.exp, "log": log, "log10": np.log10, "log2": np.log2, "pow": pow_, "hypot": reduce(np.hypot),
        "abs": np.abs, "sign": np.sign, "min": reduce(np.minimum), "max": reduce(np.maximum), "clamp": np.clip,
        "lerp": lambda a, b, t: a + (b - a)*t, "smoothstep": smoothstep, "smin": smin,
        "smax": lambda a, b, k: -smin(-a, -b, k), "ceil": np.ceil, "floor": np.floor, "round": round_,
        "radians": np.radians, "degrees": np.degrees,
    }
    assert functions.keys() == FUNCTIONS.keys()
    space = {f"_f_{name}": fn for name, fn in functions.items()}
    space.update(CONSTANTS)
    def and_(*values):  # like Python: the first falsy operand, else the last
        return functools.reduce(lambda a, b: np.where(np.asarray(a) == 0, a, b), values)

    def or_(*values):   # like Python: the first truthy operand, else the last
        return functools.reduce(lambda a, b: np.where(np.asarray(a) != 0, a, b), values)
    space.update(_and=and_, _or=or_, _not=np.logical_not, _where=np.where)
    return space


def _compile(tree, variables, vector):
    import copy
    body = _Lower(vector).visit(copy.deepcopy(tree))
    args = ast.arguments(posonlyargs=[], args=[ast.arg(arg=v) for v in variables], vararg=None,
                         kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[])
    module = ast.Expression(body=ast.Lambda(args=args, body=body))
    code = compile(ast.fix_missing_locations(module), "<cdcad expression>", "eval")
    space = dict(_vector_namespace() if vector else _scalar_namespace())
    space["__builtins__"] = {}
    return eval(code, space)  # the tree was whitelisted by check()


def _finite_number(text, value):
    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > LIMIT:
        raise CADError(f"Expression result is not a finite, bounded number: {text}")
    return value


def evaluate_checked(checked, params):
    """Value of a checked dimension expression; parameters are inlined as numbers."""
    import copy
    tree = _Inline({k: params[k] for k in checked.names if k in params}).visit(copy.deepcopy(checked.tree))
    function = _compile(ast.fix_missing_locations(tree), (), vector=False)
    try:
        value = function()
    except CADError:
        raise
    except (ArithmeticError, ValueError, TypeError, RecursionError) as exc:
        raise CADError(f"Invalid expression {checked.text!r}: {exc}") from exc
    return _finite_number(checked.text, value)


def evaluate(text, params):
    """Evaluate a dimension expression (without its leading '=') with parameter values."""
    return evaluate_checked(check(text, params.keys()), params)


class Field:
    """A compiled field expression f(variables...) for numpy arrays and for floats."""

    def __init__(self, text, variables):
        self.text, self.variables = text, tuple(variables)
        self.checked = check(text, self.variables, kind="field expression")
        self._vector = _compile(self.checked.tree, self.variables, vector=True)
        self._scalar = None

    def __call__(self, *arrays):
        """Evaluate on numpy arrays (broadcasting). Returns a float array; raises CADError on NaN/inf."""
        import numpy as np
        with np.errstate(all="ignore"):
            try:
                value = self._vector(*arrays)
            except (ArithmeticError, ValueError, TypeError) as exc:
                raise CADError(f"Invalid expression {self.text!r}: {exc}") from exc
            value = np.broadcast_to(np.asarray(value, dtype=float), np.broadcast(*arrays).shape)
        if not np.isfinite(value).all():
            raise CADError(f"Expression {self.text!r} is not finite everywhere on its domain (division by zero, "
                           "log or sqrt of a negative number?)")
        return value

    def scalar(self):
        """A plain-float version, for callers that evaluate one point at a time."""
        if self._scalar is None:
            self._scalar = _compile(self.checked.tree, self.variables, vector=False)
        return self._scalar


def field_text(text, variables, params):
    """Validate a field expression and inline the parameters it reads.

    A leading '=' is accepted and dropped. Variables shadow parameters of the same name.
    The result is plain text with only the variables free, so a field is self-contained
    (and its cache key changes whenever a parameter it reads changes).
    """
    if isinstance(text, str) and text.startswith("="):
        text = text[1:]
    names = set(variables) | set(params)
    checked = check(text, names, kind="field expression")
    values = {k: params[k] for k in checked.names if k in params and k not in variables}
    return inline(checked, values), {k for k in checked.names if k in params and k not in variables}
