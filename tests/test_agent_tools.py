"""render, describe, guide, recipe checks, density and warnings: the tools for checking a
model without opening a CAD program (useful to people and to coding agents alike)."""
import json
import subprocess
import sys

import pytest
from PIL import Image

import cdcad as cad
from cdcad.cli import execute, parser
from cdcad.commands.docs import Text


def run(*argv):
    return execute(parser().parse_args([str(a) for a in argv]))


def recipe(tmp_path, **extra):
    raw = {"version": 1, "params": {"w": 20, "unused": 3},
           "solids": {"part": {"op": "box", "size": ["=w", 10, 5]}, "spare": {"op": "sphere", "radius": 2}},
           "exports": [{"solid": "part", "path": "part.stl"}], **extra}
    path = tmp_path/"r.json"
    path.write_text(json.dumps(raw))
    return path


def test_render_a_recipe_and_a_mesh_file(tmp_path):
    out = run("render", recipe(tmp_path), "-o", tmp_path/"grid.png", "--size", 400)
    assert out["extents"] == pytest.approx([20, 10, 5])
    with Image.open(tmp_path/"grid.png") as image:
        assert image.size == (400, 400)
        colours = image.convert("RGB").getcolors(1_000_000)
        assert len(colours) > 20                    # shaded faces, not a blank canvas
    run("make", "cone", "--radius", 5, "--height", 8, "-o", tmp_path/"c.stl")
    run("render", tmp_path/"c.stl", "-o", tmp_path/"top.png", "--view", "top", "--size", 128)
    with pytest.raises(cad.CADError, match="exists"):
        run("render", tmp_path/"c.stl", "-o", tmp_path/"top.png")
    with pytest.raises(cad.CADError, match="Unknown view"):
        run("render", tmp_path/"c.stl", "-o", tmp_path/"x.png", "--view", "sideways")
    with pytest.raises(cad.CADError, match="No solid named") as error:
        run("render", recipe(tmp_path), "-o", tmp_path/"y.png", "--solid", "prat")
    assert "part" in error.value.hint


def test_describe_lists_everything_and_one_operation_in_detail():
    listing = run("describe")
    assert "sweep" in listing["operations"]["solids"]["from 2D"]
    assert "atan2" in listing["functions"]
    both = run("describe", "union")["operations"]
    assert {item["kind"] for item in both} == {"profiles", "solids"}
    one = run("describe", "cylinder")["operations"][0]
    assert one["required"]["radius"]["type"] == "number > 0"
    assert one["optional"]["segments"]["default"] == 96
    assert isinstance(run("describe", "sweep", "--text"), Text)


def test_guide_is_markdown_in_json_and_mentions_every_operation():
    guide = run("guide")
    assert guide["format"] == "markdown"
    for op in ("implicit", "sweep", "text", "graph", "regular_polygon"):
        assert f"- {op}(" in guide["text"]


def test_checks_pass_and_are_reported(tmp_path):
    path = recipe(tmp_path, density=1.0, checks=[
        {"solid": "part", "volume": {"approx": "=w*10*5"}, "size_x": {"min": 19, "max": 21}, "mass": {"approx": 1.0},
         "components": {"approx": 1}, "is_volume": True, "fits": [10, 30, 5]}])
    report = run("build", path, "--out-dir", tmp_path/"out")
    assert all(c["ok"] for c in report["checks"]) and len(report["checks"]) == 6
    assert report["exports"][0]["mass_g"] == pytest.approx(1.0)


def test_a_failing_check_writes_nothing_and_says_why(tmp_path):
    path = recipe(tmp_path, checks=[{"solid": "part", "volume": {"max": 100}}, {"solid": "part", "fits": [5, 5, 5]}])
    with pytest.raises(cad.CADError, match="volume is 1000, expected <= 100.*does not fit") as error:
        run("build", path, "--out-dir", tmp_path/"out")
    assert "nothing was written" in error.value.hint
    assert not (tmp_path/"out"/"part.stl").exists()


def test_mass_check_needs_density(tmp_path):
    with pytest.raises(cad.CADError, match="density"):
        run("build", recipe(tmp_path, checks=[{"solid": "part", "mass": {"min": 1}}]), "--out-dir", tmp_path/"o")


def test_validate_and_build_warn_about_unused_parameters_and_nodes(tmp_path):
    warnings = run("validate", recipe(tmp_path))["warnings"]
    assert any("unused" in w for w in warnings) and any("spare" in w for w in warnings)
    assert not any("Parameters" in w and " w" in w.split(":")[1] for w in warnings)
    assert run("build", recipe(tmp_path), "--out-dir", tmp_path/"out")["warnings"] == warnings


def test_parameters_used_only_through_other_parameters_count_as_used(tmp_path):
    raw = {"version": 1, "params": {"a": 2, "b": "=a*3"}, "solids": {"s": {"op": "box", "size": ["=b", 1, 1]}},
           "exports": [{"solid": "s", "path": "x.stl"}]}
    (tmp_path/"r.json").write_text(json.dumps(raw))
    assert run("validate", tmp_path/"r.json")["warnings"] == []


def test_error_output_carries_the_hint():
    raw = {"version": 1, "solids": {"s": {"op": "bx", "size": [1, 1, 1]}}, "exports": [{"solid": "s", "path": "x.stl"}]}
    result = subprocess.run([sys.executable, "-m", "cdcad", "validate", "-"], input=json.dumps(raw),
                            capture_output=True, text=True)
    error = json.loads(result.stderr)
    assert result.returncode == 2 and error["hint"] == "Did you mean 'box'?"


def test_text_output_and_worker_results(tmp_path):
    text = subprocess.run([sys.executable, "-m", "cdcad", "describe", "torus", "--text"], capture_output=True, text=True)
    assert text.stdout.startswith("## torus")
    requests = [{"id": 1, "args": ["describe", "cone"]}, {"id": 2, "args": ["guide", "--text"]}]
    served = subprocess.run([sys.executable, "-m", "cdcad", "serve"], capture_output=True, text=True,
                            input="\n".join(json.dumps(r) for r in requests) + "\n")
    replies = [json.loads(line) for line in served.stdout.splitlines()]
    assert replies[0]["result"]["operations"][0]["op"] == "cone"
    assert replies[1]["result"].startswith("# CDCAD")


def test_validate_catches_a_misspelt_check_solid(tmp_path):
    with pytest.raises(cad.CADError, match="Unknown reference: solids/prat") as error:
        run("validate", recipe(tmp_path, checks=[{"solid": "prat", "volume": {"min": 1}}]))
    assert "part" in error.value.hint
