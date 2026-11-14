import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon

import cdcad as cad
from cdcad.mesh import boolean, clean_mesh, export_bytes, heightfield, load_mesh, loft, transform_mesh
from cdcad._profiles import loops_fill, read_dxf
from cdcad.recipe import expression


def recipe(profiles=None, solids=None, exports=None, units="mm"):
    return cad.resolve_recipe({"version": 1, "units": units,
        "profiles": profiles or {}, "solids": solids or {"part": {"op": "box", "size": [10, 20, 3]}},
        "exports": exports or [{"solid": "part", "path": "part.stl"}]})


def test_extrude_holes_volume_and_roundtrip(tmp_path):
    r = recipe({"p": {"op": "polygon", "points": [[0, 0], [10, 0], [10, 10], [0, 10]],
                       "holes": [[[2, 2], [4, 2], [4, 4], [2, 4]]]}},
               {"part": {"op": "extrude", "profile": "p", "height": 5, "z": 2}})
    result = cad.build_recipe(r, tmp_path)
    mesh = load_mesh(tmp_path/"part.stl")
    assert result["exports"][0]["is_volume"]
    assert mesh.volume == pytest.approx(480)
    assert mesh.bounds[:, 2] == pytest.approx([2, 7])


def test_expression_params_and_overrides():
    r = {"version": 1, "params": {"w": 10, "h": "=w/2"},
         "solids": {"part": {"op": "box", "size": ["=w", "=h", "=sqrt(16)"]}},
         "exports": [{"solid": "part", "path": "part.stl"}]}
    result = cad.resolve_recipe(r, {"w": 20})
    assert result["solids"]["part"]["size"] == [20, 10, 4]


@pytest.mark.parametrize("expr", ["__import__('os').system('whoami')", "(1).__class__", "[1][0]", "2**1000"])
def test_expression_does_not_execute_code(expr):
    with pytest.raises(cad.CADError):
        expression(expr, {})


@pytest.mark.parametrize("expr", ["1/0", "1+", "sqrt(-1)", "sin()", "max()", "round(1,2,3,4)", "-"*1000 + "1", "2 3"])
def test_invalid_expressions_are_cad_errors(expr):
    with pytest.raises(cad.CADError, match="Invalid expression|Unsupported expression"):
        expression(expr, {})
    raw = {"version": 1, "params": {"a": "=" + expr}, "solids": {"part": {"op": "box", "size": [1, 1, 1]}},
           "exports": [{"solid": "part", "path": "part.stl"}]}
    with pytest.raises(cad.CADError):
        cad.resolve_recipe(raw)


def test_set_override_with_invalid_expression_is_cad_error():
    raw = {"version": 1, "params": {"w": 2}, "solids": {"part": {"op": "box", "size": ["=w", 1, 1]}},
           "exports": [{"solid": "part", "path": "part.stl"}]}
    with pytest.raises(cad.CADError, match="Invalid expression"):
        cad.resolve_recipe(raw, {"w": "=1/0"})


def test_forward_parameter_references_still_resolve():
    # The solver retries a parameter whose dependencies are not resolved yet; that
    # retry keys off the "Unsupported expression" message, so cover it explicitly.
    raw = {"version": 1, "params": {"a": "=b+1", "c": "=a*2", "b": 2},
           "solids": {"part": {"op": "box", "size": ["=a", "=c", 1]}},
           "exports": [{"solid": "part", "path": "part.stl"}]}
    result = cad.resolve_recipe(raw)
    assert result["params"] == {"b": 2, "a": 3, "c": 6}
    assert result["solids"]["part"]["size"] == [3, 6, 1]
    cyclic = {**raw, "params": {"a": "=b+1", "b": "=a+1"}}
    with pytest.raises(cad.CADError, match="Unresolvable"):
        cad.resolve_recipe(cyclic)


def test_schema_rejects_typos():
    with pytest.raises(cad.CADError, match="Additional properties"):
        recipe(solids={"part": {"op": "box", "size": [1, 2, 3], "height": 2}})


def test_cycles_and_missing_references():
    for solids, message in [({"part": {"op": "transform", "input": "part"}}, "Cyclic"),
                            ({"part": {"op": "transform", "input": "missing"}}, "Unknown")]:
        with pytest.raises(cad.CADError, match=message):
            cad.Engine(recipe(solids=solids))


def test_unit_conversion(tmp_path):
    result = cad.build_recipe(recipe(units="in"), tmp_path)
    assert result["exports"][0]["extents"] == pytest.approx([254, 508, 76.2])


@pytest.mark.parametrize("op,volume", [("union", 1500), ("difference", 500), ("intersection", 500)])
def test_mesh_booleans(op, volume):
    a = trimesh.creation.box([10, 10, 10])
    b = a.copy()
    b.apply_translation([5, 0, 0])
    mesh = boolean(op, [a, b])
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(volume)


def test_transform_order_and_mirror():
    m = trimesh.creation.box([2, 4, 6])
    n = transform_mesh(m, {"scale": [-2, 1, 1], "rotate": [0, 0, 90], "translate": [10, 0, 0]})
    assert n.is_volume
    assert n.extents == pytest.approx([4, 4, 6])
    assert n.centroid == pytest.approx([10, 0, 0])
    assert m.extents == pytest.approx([2, 4, 6])


def test_closed_paths_evenodd_and_open_strokes():
    r = recipe({"p": {"op": "path", "d": "M0 0 H10 V10 H0 Z M2 2 H4 V4 H2 Z"},
                "s": {"op": "path", "d": "M0 0 L10 0", "width": 2}},
               {"part": {"op": "extrude", "profile": "p", "height": 1}})
    engine = cad.Engine(r)
    assert engine.get("profiles", "p").area == pytest.approx(96)
    assert engine.get("profiles", "s").area == pytest.approx(20+np.pi, rel=.002)


def test_winding_rule_and_element_union():
    loops = [[[0, 0], [10, 0], [10, 10], [0, 10]], [[2, 2], [4, 2], [4, 4], [2, 4]]]
    assert loops_fill(loops, "nonzero").area == pytest.approx(100)
    assert loops_fill(loops, "evenodd").area == pytest.approx(96)
    assert loops_fill([loops[0], loops[1][::-1]], "nonzero").area == pytest.approx(96)


def test_svg_physical_units_transforms_selection(tmp_path):
    (tmp_path/"x.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" '
        'viewBox="0 0 20 10"><g transform="translate(2 1)"><path id="a" d="M0 0 H10 V5 H0 Z"/></g>'
        '<circle id="b" cx="17" cy="5" r="1"/></svg>')
    r = recipe({"p": {"op": "svg", "path": "x.svg", "ids": ["a"]}},
               {"part": {"op": "extrude", "profile": "p", "height": 2}})
    m = cad.Engine(r, tmp_path).get("solids", "part")
    assert m.volume == pytest.approx(100, rel=2e-6)
    assert m.bounds == pytest.approx(np.array([[2, -6, 0], [12, -1, 2]]), abs=1e-5)


def test_svg_circle_closure_does_not_create_degenerate_triangles(tmp_path):
    (tmp_path/"circle.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="80mm" '
        'height="50mm" viewBox="0 0 80 50"><circle id="c" cx="50" cy="25" r="9"/></svg>')
    r = recipe({"p": {"op": "svg", "path": "circle.svg", "ids": ["c"], "step": .5}},
               {"part": {"op": "extrude", "profile": "p", "height": 5}})
    m = cad.Engine(r, tmp_path).get("solids", "part")
    assert m.is_volume
    assert m.volume == pytest.approx(np.pi*81*5, rel=.001)


def test_dxf_layers_arcs_and_holes(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    ms = doc.modelspace()
    doc.layers.new("OUTLINE")
    ms.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True, dxfattribs={"layer": "OUTLINE"})
    ms.add_circle((10, 5), 2, dxfattribs={"layer": "OUTLINE"})
    ms.add_text("ignore me")
    doc.saveas(tmp_path/"x.dxf")
    g = read_dxf(tmp_path/"x.dxf", {"layers": ["OUTLINE"], "tolerance": .001})
    assert g.area == pytest.approx(200-4*np.pi, rel=.001)
    assert len(g.interiors) == 1


def test_dxf_line_chains_and_reject_dangles(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    ms = doc.modelspace()
    pts = [[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]
    for a, b in zip(pts, pts[1:]):
        ms.add_line(a, b)
    doc.saveas(tmp_path/"lines.dxf")
    assert read_dxf(tmp_path/"lines.dxf", {}).area == pytest.approx(25)
    ms.add_line((100, 100), (110, 100))
    doc.saveas(tmp_path/"lines.dxf")
    with pytest.raises(cad.CADError, match="open"):
        read_dxf(tmp_path/"lines.dxf", {})


def test_loft_constant_section_and_revolve():
    p = Polygon([[0, 0], [10, 0], [10, 10], [0, 10]])
    m = loft([p, p], [0, 8], 40)
    assert m.volume == pytest.approx(800)
    r = recipe({"p": {"op": "polygon", "points": [[2, 0], [5, 0], [5, 10], [2, 10]]}},
               {"part": {"op": "revolve", "profile": "p", "segments": 128}})
    m = cad.Engine(r).get("solids", "part")
    assert m.is_volume
    assert m.volume == pytest.approx(np.pi*(25-4)*10, rel=.001)


def test_heightfield_watertight_and_analytic_volume(tmp_path):
    np.save(tmp_path/"height.npy", np.full((4, 6), .5))
    m = heightfield(tmp_path/"height.npy", {"size": [10, 20], "height": 4, "base": 2})
    assert m.is_volume
    assert m.volume == pytest.approx(800)


def test_capped_plane_cut():
    r = recipe(solids={"a": {"op": "box", "size": [10, 10, 10]},
                       "part": {"op": "cut", "input": "a", "origin": [0, 0, 0], "normal": [0, 0, 1]}})
    m = cad.Engine(r).get("solids", "part")
    assert m.is_volume
    assert m.volume == pytest.approx(500)


def test_capped_cut_through_hollow_solid():
    r = recipe({"p": {"op": "polygon", "points": [[0, 0], [10, 0], [10, 10], [0, 10]],
                       "holes": [[[2, 2], [8, 2], [8, 8], [2, 8]]]}},
               {"a": {"op": "extrude", "profile": "p", "height": 10},
                "part": {"op": "cut", "input": "a", "origin": [0, 0, 5], "normal": [0, 0, -1]}})
    m = cad.Engine(r).get("solids", "part")
    assert m.is_volume
    assert m.volume == pytest.approx(320)


def test_repair_duplicate_faces_and_simple_hole():
    m = trimesh.creation.box()
    m.update_faces(np.arange(len(m.faces)-1))
    assert not m.is_watertight
    assert clean_mesh(m).is_volume


def test_reject_invalid_profiles_and_open_paths():
    for profile in [{"op": "polygon", "points": [[0, 0], [10, 10], [0, 10], [10, 0]]},
                    {"op": "path", "d": "M0 0 L10 0 L10 10"}]:
        r = recipe({"p": profile}, {"part": {"op": "extrude", "profile": "p", "height": 1}})
        with pytest.raises(cad.CADError):
            cad.Engine(r).get("solids", "part")


def test_export_guards_and_no_partial_geometry_write(tmp_path):
    cad.build_recipe(recipe(), tmp_path)
    with pytest.raises(cad.CADError, match="exists"):
        cad.build_recipe(recipe(), tmp_path)
    with pytest.raises(cad.CADError, match="escapes"):
        cad.build_recipe(recipe(exports=[{"solid": "part", "path": "../escape.stl"}]), tmp_path, out_dir=tmp_path)
    solids = {"part": {"op": "box", "size": [1, 2, 3]},
              "bad": {"op": "import", "path": "missing.stl"}}
    exports = [{"solid": "part", "path": "good.stl"}, {"solid": "bad", "path": "bad.stl"}]
    with pytest.raises(cad.CADError):
        cad.build_recipe(recipe(solids=solids, exports=exports), tmp_path)
    assert not (tmp_path/"good.stl").exists()


def test_serialized_stl_rejects_coincident_edge_contact():
    a = trimesh.creation.box()
    b = a.copy()
    b.apply_translation([1, 1, 0])
    combined = trimesh.util.concatenate([a, b])
    assert combined.is_volume  # Separate vertex indices hide the shared edge.
    with pytest.raises(cad.CADError, match="Serialized STL"):
        export_bytes(combined, "edge-contact.stl")


def test_memoized_nodes_and_target_pruning(tmp_path):
    r = recipe(solids={"part": {"op": "box", "size": [1, 2, 3]},
                       "unused": {"op": "import", "path": "missing.stl"}},
               exports=[{"solid": "part", "path": "part.stl"}, {"solid": "unused", "path": "unused.stl"}])
    result = cad.build_recipe(r, tmp_path, targets=["part"])
    assert len(result["trace"]) == 1
    e = cad.Engine(r)
    assert e.get("solids", "part") is e.get("solids", "part")


def test_disk_cache_hits_invalidation_and_corruption(tmp_path):
    cache = tmp_path/"cache"
    r = recipe()
    first = cad.build_recipe(r, tmp_path, cache_dir=cache)
    second = cad.build_recipe(r, tmp_path, cache_dir=cache, force=True)
    assert not first["trace"][0]["cache_hit"]
    assert second["trace"][0]["cache_hit"]
    r["solids"]["part"]["size"] = [20, 20, 3]
    third = cad.build_recipe(r, tmp_path, cache_dir=cache, force=True)
    assert not third["trace"][0]["cache_hit"]
    assert third["exports"][0]["volume"] == pytest.approx(1200)
    for f in cache.glob("*.npz"):
        f.write_bytes(b"corrupt")
    fourth = cad.build_recipe(r, tmp_path, cache_dir=cache, force=True)
    assert not fourth["trace"][0]["cache_hit"]
    assert fourth["exports"][0]["volume"] == pytest.approx(1200)


def test_cache_invalidates_when_source_bytes_change(tmp_path):
    path = tmp_path/"source.stl"
    trimesh.creation.box([1, 2, 3]).export(path)
    r = recipe(solids={"part": {"op": "import", "path": "source.stl"}})
    cad.build_recipe(r, tmp_path, cache_dir=tmp_path/"cache")
    trimesh.creation.box([2, 2, 3]).export(path)
    result = cad.build_recipe(r, tmp_path, cache_dir=tmp_path/"cache", force=True)
    assert not result["trace"][0]["cache_hit"]
    assert result["exports"][0]["volume"] == pytest.approx(12)


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "cdcad", *map(str, args)],
                          input=stdin, text=True, capture_output=True)


def test_cli_stdin_errors_and_json_contract(tmp_path):
    raw = recipe()
    success = cli("build", "-", "--out-dir", tmp_path, stdin=json.dumps(raw))
    assert success.returncode == 0, success.stderr
    assert json.loads(success.stdout)["ok"]
    failure = cli("build", "-", "--out-dir", tmp_path, stdin=json.dumps(raw))
    assert failure.returncode == 2
    assert json.loads(failure.stderr)["ok"] is False
    assert not failure.stdout


def test_cli_edit_split_section_and_simplification(tmp_path):
    source = tmp_path/"source.stl"
    trimesh.creation.icosphere(subdivisions=2, radius=10).export(source)
    edited = tmp_path/"edited.stl"
    result = cli("edit", source, "--scale", 2, 1, 1, "--faces", 200, "-o", edited)
    assert result.returncode == 0, result.stderr
    assert load_mesh(edited).is_volume
    assert len(load_mesh(edited).faces) <= 200
    result = cli("section", source, "--z", 0, "-o", tmp_path/"section.svg")
    assert result.returncode == 0, result.stderr
    result = cli("extrude", tmp_path/"section.svg", "--height", 2, "-o", tmp_path/"section.stl")
    assert result.returncode == 0, result.stderr
    assert load_mesh(tmp_path/"section.stl").extents[:2] == pytest.approx([20, 20], rel=.001)
    a, b = trimesh.creation.box(), trimesh.creation.box()
    b.apply_translation([3, 0, 0])
    trimesh.util.concatenate([a, b]).export(tmp_path/"two.stl")
    result = cli("split", tmp_path/"two.stl", "--out-dir", tmp_path/"parts")
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["parts"]) == 2


def test_section_svg_keeps_orientation_through_import(tmp_path):
    from shapely.geometry import Point

    # An L that is not symmetric in Y: foot 15 wide along the bottom, 30 tall leg on the left.
    shape = {"op": "polygon", "points": [[0, 0], [20, 0], [20, 5], [5, 5], [5, 30], [0, 30]]}
    solid = cad.resolve_recipe({"version": 1, "profiles": {"L": shape},
        "solids": {"part": {"op": "extrude", "profile": "L", "height": 4}},
        "exports": [{"solid": "part", "path": "L.stl"}]})
    cad.build_recipe(solid, tmp_path)
    result = cli("section", tmp_path/"L.stl", "--z", 2, "-o", tmp_path/"L.svg")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["area"] == pytest.approx(5*30 + 15*5)
    # SVG Y points down, so the part is drawn with negated Y: the viewBox starts at -30.
    view_box = [float(v) for v in re.search(r'viewBox="([^"]+)"', (tmp_path/"L.svg").read_text()).group(1).split()]
    assert view_box == pytest.approx([0, -30, 20, 30])
    # Round trip through the SVG importer (flip_y by default) must not mirror the part.
    imported = cad.Engine(cad.resolve_recipe({"version": 1, "profiles": {"s": {"op": "svg", "path": "L.svg"}},
        "solids": {"p": {"op": "extrude", "profile": "s", "height": 1}},
        "exports": [{"solid": "p", "path": "o.stl"}]}), base=tmp_path).get("profiles", "s")
    # Imported bounds are (0, -30, 20, 0): the viewBox origin maps to 0 and Y is flipped.
    assert imported.contains(Point(15, -27))
    assert not imported.contains(Point(15, -3))
    assert imported.contains(Point(2, -2))
