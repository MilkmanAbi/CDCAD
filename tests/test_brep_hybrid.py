"""The hybrid B-rep path: exact OpenCascade solids for the operations it supports, and the
mesh path for everything else (and when the kernel is not installed)."""
import json
import math
import re

import pytest

import cdcad as cad
from cdcad import brep, build_recipe, resolve_recipe
from cdcad._common import CADError

needs_kernel = pytest.mark.skipif(not brep.available(), reason="cadquery-ocp (cdcad[brep]) is not installed")


def run(raw, tmp_path, *, exports=None):
    recipe = {"version": 1, **raw}
    recipe.setdefault("exports", exports or [{"solid": "part", "path": "part.stl"}])
    return build_recipe(resolve_recipe(recipe), tmp_path, tmp_path/"out", force=True)


@needs_kernel
def test_primitives_are_exact_volumes():
    assert brep.volume(brep.primitive({"op": "box", "size": [30, 20, 8]})) == pytest.approx(4800, rel=1e-9)
    assert brep.volume(brep.primitive({"op": "cylinder", "radius": 5, "height": 10})) == \
        pytest.approx(math.pi*25*10, rel=1e-9)
    assert brep.volume(brep.primitive({"op": "sphere", "radius": 10})) == pytest.approx(4/3*math.pi*1000, rel=1e-9)


@needs_kernel
def test_fillet_on_a_union_is_exact_and_fast(tmp_path):
    raw = {"params": {}, "solids": {
        "plate": {"op": "box", "size": [30, 20, 8]},
        "pin": {"op": "cylinder", "radius": 5, "height": 10},
        "up": {"op": "transform", "input": "pin", "translate": [0, 0, 9]},
        "u": {"op": "union", "inputs": ["plate", "up"]},
        "part": {"op": "fillet", "input": "u", "radius": 1.5}}}
    report = run(raw, tmp_path)
    assert report["exports"][0]["is_volume"]
    union_volume = brep.volume(brep.build({"op": "union"}, [brep.primitive({"op": "box", "size": [30, 20, 8]}),
                                                           brep._move(brep.primitive({"op": "cylinder", "radius": 5,
                                                                                      "height": 10}), 0, 0, 9)]))
    assert report["exports"][0]["volume"] < union_volume
    assert report["exports"][0]["volume"] == pytest.approx(union_volume - 105, abs=5)


@needs_kernel
def test_step_from_a_brep_solid_keeps_curved_faces(tmp_path):
    raw = {"solids": {"plate": {"op": "box", "size": [30, 20, 8]},
                      "cyl": {"op": "cylinder", "radius": 5, "height": 10},
                      "pin": {"op": "transform", "input": "cyl", "translate": [0, 0, 8]},
                      "u": {"op": "union", "inputs": ["plate", "pin"]},
                      "part": {"op": "fillet", "input": "u", "radius": 1.5}}}
    run(raw, tmp_path, exports=[{"solid": "part", "path": "part.step"}])
    text = (tmp_path/"out"/"part.step").read_text(encoding="ascii")
    assert "CYLINDRICAL_SURFACE" in text and "SPHERICAL_SURFACE" in text
    planes = len(re.findall(r"=PLANE\(", text))
    assert planes < 50  # a faceted export would have one plane per triangle


@needs_kernel
def test_brep_routes_only_supported_chains():
    raw = {"solids": {
        "box": {"op": "box", "size": [10, 10, 10]},
        "cut": {"op": "cut", "input": "box", "origin": [0, 0, 0], "normal": [0, 0, 1]},
        "sphere": {"op": "sphere", "radius": 4},
        "fine": {"op": "fillet", "input": "sphere", "radius": 0.5},
        "round": {"op": "fillet", "input": "box", "radius": 1, "edges": "concave"}}}
    recipe = resolve_recipe({"version": 1, **raw, "exports": []})
    from cdcad.engine import Engine
    engine = Engine(recipe, base=".")
    assert engine.brep_ok("solids", "box")
    assert engine.brep_ok("solids", "fine")
    assert not engine.brep_ok("solids", "cut")      # mesh-only operation
    assert not engine.brep_ok("solids", "round")    # concave edges use the mesh method


@needs_kernel
def test_engine_uses_the_mesh_for_mesh_only_inputs(tmp_path):
    raw = {"solids": {"box": {"op": "box", "size": [10, 10, 10]},
                      "part": {"op": "fillet", "input": "box", "radius": 1, "edges": "concave"}}}
    report = run(raw, tmp_path)
    assert report["exports"][0]["is_volume"]


def test_without_the_kernel_everything_uses_meshes(tmp_path, monkeypatch):
    monkeypatch.setattr(brep, "available", lambda: False)
    raw = {"solids": {"box": {"op": "box", "size": [30, 20, 10]},
                      "part": {"op": "fillet", "input": "box", "radius": 2}}}
    report = run(raw, tmp_path)
    assert report["exports"][0]["is_volume"]
    assert report["exports"][0]["volume"] < 30*20*10


def test_without_the_kernel_step_is_faceted(tmp_path, monkeypatch):
    monkeypatch.setattr(brep, "available", lambda: False)
    run({"solids": {"part": {"op": "cylinder", "radius": 5, "height": 10, "segments": 16}}}, tmp_path,
        exports=[{"solid": "part", "path": "part.step"}])
    text = (tmp_path/"out"/"part.step").read_text(encoding="ascii")
    assert "CYLINDRICAL_SURFACE" not in text


def test_missing_kernel_gives_an_install_hint(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "OCP", None)  # makes "import OCP" fail
    with pytest.raises(CADError) as info:
        brep._require()
    assert "cdcad[brep]" in (info.value.hint or "")


@needs_kernel
def test_brep_extrude_with_holes_has_exact_volume():
    from shapely.geometry import box
    from shapely.geometry import Point
    geom = box(-10, -10, 10, 10).difference(Point(0, 0).buffer(3, quad_segs=64))
    shape = brep.extruded(geom, {"height": 10, "z": 0})
    expected = (400 - math.pi*9)*10
    assert brep.volume(shape) == pytest.approx(expected, rel=1e-3)


@needs_kernel
def test_cli_build_keeps_stdout_as_one_json_object(tmp_path):
    import subprocess
    import sys
    recipe = {"version": 1, "solids": {"box": {"op": "box", "size": [20, 10, 4]},
                                       "cyl": {"op": "cylinder", "radius": 3, "height": 6},
                                       "up": {"op": "transform", "input": "cyl", "translate": [0, 0, 2]},
                                       "u": {"op": "union", "inputs": ["box", "up"]},
                                       "part": {"op": "fillet", "input": "u", "radius": 0.5}},
              "exports": [{"solid": "part", "path": "part.step"}]}
    path = tmp_path/"recipe.json"
    path.write_text(json.dumps(recipe), encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "cdcad", "build", str(path), "--out-dir", str(tmp_path/"out")],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["ok"] is True
    assert "Step File Name" not in done.stdout and "WorkSession" not in done.stdout


def test_public_api_reports_kernel_status():
    from cdcad.cli import _brep_available
    assert _brep_available() == brep.available()
    assert cad.__version__
