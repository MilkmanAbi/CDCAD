"""Shared serialization, file operations, and runtime configuration."""
import functools
import importlib.metadata
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import sys

VERSION = "0.5.0"
UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
EXPORT_FORMATS = ("stl", "3mf", "step", "stp", "ply", "obj", "glb", "off")
GEOMETRY_DEPENDENCIES = (
    "numpy", "trimesh", "shapely", "manifold3d", "mapbox-earcut",
    "svgelements", "ezdxf", "Pillow", "fast-simplification", "scipy", "networkx", "fonttools",
)


class CADError(Exception):
    """Invalid input or an operation that could not produce a valid result.

    ``hint`` is an optional suggestion (a likely misspelling, the right place for a
    value) that the CLI prints next to the message.
    """

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint


def error_report(exc):
    """The JSON object printed for a failed command."""
    report = {"ok": False, "error": str(exc), "type": type(exc).__name__}
    if getattr(exc, "hint", None):
        report["hint"] = exc.hint
    return report


def suggest(word, choices, label="Did you mean"):
    """'Did you mean X?' for a misspelt name, or None."""
    import difflib
    close = difflib.get_close_matches(str(word), [str(c) for c in choices], n=3, cutoff=0.6)
    if not close:
        return None
    return f"{label} {' or '.join(repr(c) for c in close)}?"


@functools.lru_cache(maxsize=1)
def dependency_versions():
    versions = {}
    for name in GEOMETRY_DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None  # vendored or renamed; still part of the cache key
    return versions


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


def parse_json(text, source):
    def reject_constant(value):
        raise CADError(f"{source}: {value} is not valid JSON; write a number or an =expression")
    try:
        return json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise CADError(f"{source} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
                       hint="JSON needs double quotes, no trailing commas and no comments") from exc


def read_stdin_json():
    stream = getattr(sys.stdin, "buffer", None)
    data = stream.read().decode("utf-8-sig") if stream is not None else sys.stdin.read().lstrip("\ufeff")
    return parse_json(data, "stdin")


def read_json(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise CADError(f"File not found: {path}") from exc
    except IsADirectoryError as exc:
        raise CADError(f"Expected a JSON file but found a directory: {path}") from exc
    except UnicodeDecodeError as exc:
        raise CADError(f"{path} is not UTF-8 text") from exc
    return parse_json(text, str(path))


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
