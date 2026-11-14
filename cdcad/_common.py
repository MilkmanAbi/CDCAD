"""Shared serialization, file operations, and runtime configuration."""
import functools
import importlib.metadata
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import sys

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
    """Replace path with data in one step; readers see the old or the new file, never a part."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create the temporary file with the mode a normal write would get (0o666 minus
    # the umask). tempfile.NamedTemporaryFile makes it 0o600, and that mode would
    # survive os.replace, leaving every model and project file owner-only on POSIX.
    tmp = path.parent/f".cdcad-{os.getpid():x}-{secrets.token_hex(6)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # the recovery journal is only useful if it reached the disk
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False)+"\n").encode()
