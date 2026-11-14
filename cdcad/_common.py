"""Shared serialization, file operations, and runtime configuration."""
import functools
import importlib.metadata
import json
import os
from pathlib import Path, PureWindowsPath
import sys
import tempfile

VERSION = "0.3.0"
UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
EXPORT_FORMATS = ("stl", "ply", "obj", "glb", "off")
GEOMETRY_DEPENDENCIES = (
    "numpy", "trimesh", "shapely", "manifold3d", "mapbox-earcut",
    "svgelements", "ezdxf", "Pillow", "fast-simplification", "scipy", "networkx",
)


class CADError(Exception):
    """Invalid input or an operation that could not produce a valid result."""


@functools.lru_cache(maxsize=1)
def dependency_versions():
    return {name: importlib.metadata.version(name) for name in GEOMETRY_DEPENDENCIES}


def parse_sets(settings):
    values = {}
    for setting in settings or ():
        key, sep, raw = setting.partition("=")
        if not sep or not key:
            raise CADError("--set expects NAME=NUMBER or NAME==EXPRESSION")
        try:
            values[key] = json.loads(raw)
        except json.JSONDecodeError:
            values[key] = raw
    return values


def document_path(value):
    """Interpret portable paths and relative paths saved by Windows clients."""
    windows = PureWindowsPath(value)
    if "\\" in value and not windows.anchor:
        return Path(*windows.parts)
    return Path(value)


def read_stdin_json():
    stream = getattr(sys.stdin, "buffer", None)
    data = stream.read().decode("utf-8-sig") if stream is not None else sys.stdin.read().lstrip("\ufeff")
    return json.loads(data)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".cdcad-", delete=False) as f:
            tmp = Path(f.name)
            f.write(data)
        os.replace(tmp, path)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()


def json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False)+"\n").encode()
