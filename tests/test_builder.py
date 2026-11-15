"""The Python recipe builder produces ordinary recipes."""
import json
import math

import numpy as np
import pytest

import cdcad as cad
from cdcad import Recipe, fx


def plate_recipe():
    r = Recipe(params={"w": 60, "d": 40, "h": 6})
    w, h = r.p.w, r.p.h
    plate = r.rectangle([w, r.p.d], radius=3) - r.circle(4, center=[w/4, 0])
    part = plate.extrude(height=h)
    r.export(part, "plate.stl")
    return r, part


def test_builder_makes_a_plain_recipe_that_builds(tmp_path):
    r, part = plate_recipe()
    data = r.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["profiles"]["circle1"]["center"] == ["=(w)/4", 0]
    report = r.build(tmp_path, force=True)
    assert report["exports"][0]["is_volume"]
    r.save(tmp_path/"plate.json")
    again = cad.build_recipe(cad.read_recipe(tmp_path/"plate.json"), tmp_path, tmp_path/"again")
    assert again["exports"][0]["volume"] == pytest.approx(report["exports"][0]["volume"])


def test_overrides_operators_and_expressions():
    r, part = plate_recipe()
    assert r.mesh(part, w=90).extents == pytest.approx([90, 40, 6])
    a, b = r.box([10, 10, 10]), r.sphere(6)
    whole, minus, common = r.mesh(a).volume, r.mesh(a - b).volume, r.mesh(a & b).volume
    assert r.mesh(a | b).volume > whole
    assert minus + common == pytest.approx(whole, rel=1e-6)
    e = (r.p.w - 100)**2/fx.sqrt(4)
    assert cad.resolve_recipe({"version": 1, "params": {"w": 60, "v": str(e)}, "solids": {},
                               "exports": []})["params"]["v"] == pytest.approx(800)


def test_numpy_values_and_positional_inputs():
    r = Recipe()
    pts = np.array([[0, 0], [10, 0], [0, 10]], dtype=np.float32)
    tri = r.polygon(pts)
    solid = tri.extrude(np.float64(2.5))
    assert r.mesh(solid).volume == pytest.approx(125)
    cubes = [r.box([1, 1, 1]).translate(i*2, 0, 0) for i in range(3)]
    assert r.mesh(r.combine(*cubes)).volume == pytest.approx(3)


def test_transform_shortcuts_and_pattern():
    r = Recipe()
    bar = r.box([10, 2, 2]).translate(10, 0, 0)
    star = bar.array(6, rotate=[0, 0, 60])
    assert r.mesh(star).extents[:2] == pytest.approx([30, 30*math.sin(math.radians(60)) + 2*math.cos(math.radians(30))], rel=0.05)
    twisted = r.regular_polygon(sides=5, radius=8).extrude(20, twist=72)
    assert r.mesh(twisted).is_volume


def test_builder_errors_are_helpful():
    r = Recipe()
    disc = r.circle(5)
    with pytest.raises(AttributeError, match="cylinder"):
        r.cyliner(3)
    with pytest.raises(cad.CADError, match="profiles and solids"):
        r.union()
    with pytest.raises(cad.CADError, match="Cannot combine"):
        disc - r.box([1, 1, 1])
    with pytest.raises(cad.CADError, match="no truth value"):
        if r.p.__class__ and Recipe(params={"a": 1}).p.a:
            pass
    with pytest.raises(cad.CADError, match="positional"):
        r.box([1, 1, 1], 2)


def test_render_from_the_builder(tmp_path):
    r = Recipe()
    part = r.torus(10, 3)
    path = r.render(part, tmp_path/"t.png", size=200)
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_rename_updates_every_reference():
    r = Recipe()
    disc = r.circle(5)
    rod = disc.extrude(10)
    r.export(rod, "rod.stl").check(rod, volume={"min": 1})
    disc.rename("disc")
    rod.rename("rod")
    data = r.to_dict()
    assert data["solids"]["rod"]["profile"] == "disc"
    assert data["exports"][0]["solid"] == data["checks"][0]["solid"] == "rod"
    assert r.mesh(rod).is_volume
