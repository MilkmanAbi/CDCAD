"""The example scripts that the README points at keep working."""
import os
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def run_example(name, out_dir):
    env = dict(os.environ, PYTHONPATH=str(ROOT))   # run the checkout, not an installed copy
    return subprocess.run([sys.executable, str(ROOT/"examples"/name), str(out_dir)],
                          capture_output=True, text=True, env=env)


def test_vase_is_a_hollow_solid_90_mm_tall(tmp_path):
    result = run_example("vase.py", tmp_path)
    assert result.returncode == 0, result.stderr
    match = re.search(r": ([\d.]+) cm\^3, ([\d.]+) mm tall, solid=True", result.stdout)
    assert match, result.stdout
    volume, height = map(float, match.groups())
    assert height == pytest.approx(90)
    assert 20 < volume < 26   # the same outline filled in would be about 90 cm^3
    assert (tmp_path/"vase.stl").is_file()


def test_ripple_height_map_becomes_a_solid_relief(tmp_path):
    result = run_example("ripple.py", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "solid=True" in result.stdout, result.stdout
    assert int(re.search(r"(\d+) triangles", result.stdout).group(1)) > 100_000
    values = np.load(tmp_path/"ripple.npy")
    assert values.shape == (200, 200) and 0 <= values.min() and values.max() <= 1


def test_design_sweep_checks_its_own_volumes(tmp_path):
    result = run_example("design-sweep.py", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("mm^3") == 3


RECIPES = sorted(p.name for p in (ROOT/"examples").glob("*.json"))


@pytest.mark.parametrize("name", RECIPES)
def test_every_example_recipe_builds_and_passes_its_checks(tmp_path, name):
    import cdcad
    recipe = cdcad.read_recipe(ROOT/"examples"/name)
    report = cdcad.build_recipe(recipe, ROOT/"examples", tmp_path)
    assert all(export["is_volume"] for export in report["exports"] if not export["path"].endswith("path-track.stl"))
    assert all(check["ok"] for check in report.get("checks", []))


def test_wheel_builder_example(tmp_path):
    result = run_example("wheel.py", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("solid=True") == 3
    assert (tmp_path/"wheel-5.png").is_file()
