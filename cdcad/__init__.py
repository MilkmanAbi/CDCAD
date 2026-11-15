"""CDCAD Python API for parametric, command-line driven CAD."""
from ._common import CADError, VERSION as __version__

__all__ = ["__version__", "CADError", "Engine", "Expr", "Recipe", "build_recipe", "fx", "read_recipe", "resolve_recipe"]


def __getattr__(name):
    # Keep package import and CLI help independent of geometry dependencies.
    if name == "Engine":
        from .engine import Engine
        return Engine
    if name == "build_recipe":
        from .build import build
        return build
    if name in {"Recipe", "Expr", "fx"}:
        from . import builder
        return getattr(builder, name)
    if name in {"read_recipe", "resolve_recipe"}:
        from . import recipe
        return getattr(recipe, name)
    raise AttributeError(name)
