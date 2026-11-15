"""Dependency traversal shared by validation and geometry evaluation."""
from ._common import CADError, suggest


def dependencies(kind, node):
    if "input" in node:
        yield kind, node["input"]
    for ref in node.get("inputs", []):
        yield kind, ref
    if "profile" in node:
        yield "profiles", node["profile"]
    for section in node.get("sections", []):
        yield "profiles", section["profile"]
    if node.get("op") == "align":
        yield "solids", node["target"]

def validate_graph(recipe):
    done, active = set(), set()
    def visit(kind, name):
        key = (kind, name)
        if key in done:
            return
        if key in active:
            raise CADError(f"Cyclic reference at {kind}/{name}")
        if name not in recipe.get(kind, {}):
            other = "solids" if kind == "profiles" else "profiles"
            hint = (f"{name!r} is one of the {other}; {kind} can only refer to {kind}"
                    if name in recipe.get(other, {}) else suggest(name, recipe.get(kind, {})))
            raise CADError(f"Unknown reference: {kind}/{name}", hint=hint)
        active.add(key)
        for dep in dependencies(kind, recipe[kind][name]):
            visit(*dep)
        active.remove(key)
        done.add(key)
    for kind in ("profiles", "solids"):
        for name in recipe.get(kind, {}):
            visit(kind, name)
    for exp in recipe["exports"]:
        visit("solids", exp["solid"])
    for check in recipe.get("checks", []):
        visit("solids", check["solid"])
