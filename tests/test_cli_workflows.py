"""Regression checks for script interfaces and protected project sources."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cdcad import CADError, build_recipe, resolve_recipe
from cdcad.cli import execute, parser


def cli(*argv):
    return execute(parser().parse_args([str(arg) for arg in argv]))


def box_recipe(path="block.stl"):
    return {
        "version": 1,
        "params": {"w": 2, "h": "=w/2"},
        "solids": {"part": {"op": "box", "size": ["=w", 3, "=h"]}},
        "exports": [{"solid": "part", "path": path}],
    }


def test_resolve_stdin_and_discovery():
    result = subprocess.run(
        [sys.executable, "-m", "cdcad", "--pretty", "resolve", "-", "--set", "w=8"],
        input=json.dumps(box_recipe()), capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout)["solids"]["part"]["size"] == [8, 3, 4]
    assert len(result.stdout.splitlines()) > 1
    assert "resolve" in cli("capabilities")["commands"]
    assert "compile" in cli("capabilities")["project_commands"]


def test_recipe_rejects_typo_and_cannot_overwrite_source(tmp_path):
    with pytest.raises(CADError, match="Unknown parameter override: widht"):
        resolve_recipe(box_recipe(), {"widht": 25})
    path = tmp_path / "block.stl"
    build_recipe(resolve_recipe(box_recipe()), tmp_path)
    original = path.read_bytes()
    raw = box_recipe()
    raw["solids"]["part"] = {"op": "import", "path": path.name}
    with pytest.raises(CADError, match="overwrite a source"):
        build_recipe(resolve_recipe(raw), tmp_path, force=True)
    assert path.read_bytes() == original


def test_unit_conversion_and_duplicate_asset_path(tmp_path):
    original = tmp_path / "block.stl"
    result = cli("make", "box", "--size", 1, 2, 3, "--units", "in", "-o", original)
    assert result["exports"][0]["extents"] == pytest.approx([25.4, 50.8, 76.2])

    root = tmp_path / "fixture"
    cli("project", "init", root, "--units", "in")
    source = root / "segments" / "structure" / "source.json"
    local_mesh = source.with_name("block.stl")
    local_mesh.write_bytes(original.read_bytes())
    raw = box_recipe()
    raw["solids"]["part"] = {"op": "import", "path": "block.stl", "units": "mm"}
    source.write_text(json.dumps(raw), encoding="utf-8")
    cli("project", "put", root, "source", "--file", source)
    cli("project", "duplicate", root, "source", "other", "--layer", "fixtures")
    outputs = cli("project", "build", root)["outputs"]
    assert len(outputs) == 2
    for output in outputs:
        assert output["extents"] == pytest.approx([25.4, 50.8, 76.2])


def test_project_compile_overrides_and_patch_indices(tmp_path):
    root = tmp_path / "fixture"
    cli("project", "init", root, "--template", "arch-demo")
    path = tmp_path / "compiled.json"
    with pytest.raises(CADError, match="Unknown parameter override"):
        cli("project", "compile", root, "--set", "sapn=70", "-o", path)
    cli("project", "compile", root, "--set", "span=70", "-o", path)
    assert json.loads(path.read_text())["solids"]["arch::part"]["span"] == 70
    with pytest.raises(CADError, match="Invalid array pointer index"):
        cli("project", "set", root, "/assemblies/demo/members/-1/value", "1")


def test_utf8_stdin_and_worker(tmp_path):
    path = "\u6e2c\u8a66/caf\u00e9.stl"
    raw = box_recipe(path)
    payload = json.dumps(raw, ensure_ascii=False)
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    command = [sys.executable, "-m", "cdcad"]
    result = subprocess.run(
        [*command, "build", "-", "--out-dir", str(tmp_path)],
        input=payload.encode("utf-8"), capture_output=True, check=True, env=env,
    )
    assert json.loads(result.stdout)["exports"][0]["is_volume"]
    assert (tmp_path / path).is_file()

    request = json.dumps({"id": "\u6e2c\u8a66", "args": ["resolve", "-"], "stdin": payload},
                         ensure_ascii=False)
    result = subprocess.run(
        [*command, "serve"], input=(request + "\n").encode("utf-8"),
        capture_output=True, check=True, env=env,
    )
    reply = json.loads(result.stdout)
    assert reply["id"] == "\u6e2c\u8a66"
    assert reply["result"]["exports"][0]["path"] == path


def test_windows_paths_in_documents(tmp_path):
    report = build_recipe(resolve_recipe(box_recipe(r"output\block.stl")), tmp_path)
    assert Path(report["exports"][0]["path"]) == tmp_path / "output" / "block.stl"
    raw = box_recipe("copy.stl")
    raw["solids"]["part"] = {"op": "import", "path": r"output\block.stl"}
    assert build_recipe(resolve_recipe(raw), tmp_path)["exports"][0]["is_volume"]

    project = tmp_path / "fixture"
    cli("project", "init", project)
    cli("project", "put", project, "base", "--spec", '{"op":"box","size":[2,3,4]}')
    manifest_file = project / "project.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["segments"]["base"]["path"] = r"segments\structure\base.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    cli("project", "set", project, "/solids/part/size/0", "5", "--segment", "base")
    assert cli("project", "build", project)["outputs"][0]["volume"] == pytest.approx(60)
    with pytest.raises(CADError, match="reserved on Windows"):
        cli("project", "put", project, "NUL", "--spec", '{"op":"box","size":[1,1,1]}')
