"""Regression tests for the defects fixed in 0.5.0."""
import io
import json
import zipfile

import numpy as np
import pytest
import trimesh
from PIL import Image

import cdcad as cad
from cdcad.cli import execute, parser
from cdcad.mesh import export_3mf, heightfield, load_mesh, read_3mf
from cdcad._profiles import read_dxf


def run(*argv):
    return execute(parser().parse_args([str(a) for a in argv]))


def svg_profile(tmp_path, body, **extra):
    (tmp_path/"t.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" '
                                  f'viewBox="0 0 40 40">{body}</svg>')
    r = cad.resolve_recipe({"version": 1, "profiles": {"p": {"op": "svg", "path": "t.svg", **extra}},
                            "solids": {"s": {"op": "extrude", "profile": "p", "height": 1}},
                            "exports": [{"solid": "s", "path": "x.stl"}]})
    return cad.Engine(r, tmp_path).get("profiles", "p")


def test_image_heightfield_keeps_the_picture_upright(tmp_path):
    picture = np.zeros((40, 40), dtype=np.uint8)
    picture[:10, :] = 255                       # a white band along the top of the picture
    Image.fromarray(picture).save(tmp_path/"top.png")
    m = heightfield(tmp_path/"top.png", {"size": [40, 40], "height": 10, "base": 1})
    high = m.vertices[m.vertices[:, 2] > 5]
    assert high[:, 1].min() > 25                # top of the picture = high Y, like SVG import
    flipped = heightfield(tmp_path/"top.png", {"size": [40, 40], "height": 10, "base": 1, "flip_y": False})
    assert flipped.vertices[flipped.vertices[:, 2] > 5][:, 1].max() < 15


def test_npy_heightfield_row_zero_is_low_y_even_when_strided(tmp_path):
    values = np.zeros((301, 5))
    values[:20] = 1.0                           # first rows high
    np.save(tmp_path/"a.npy", values)
    m = heightfield(tmp_path/"a.npy", {"size": [5, 30], "height": 4, "max_samples": 100})
    assert m.is_volume
    assert m.vertices[m.vertices[:, 2] > 3][:, 1].max() < 3
    assert m.bounds[:, 1] == pytest.approx([0, 30])


def test_sixteen_bit_heightmap_keeps_its_levels(tmp_path):
    row = np.linspace(0, 65535, 64).astype(np.uint16)
    Image.fromarray(np.tile(row, (8, 1))).save(tmp_path/"g16.png")
    m = heightfield(tmp_path/"g16.png", {"size": [64, 8], "height": 10, "base": 1})
    top = m.vertices[m.vertices[:, 2] > 0]
    assert len(np.unique(np.round(top[:, 2], 6))) > 50
    assert top[:, 2].max() == pytest.approx(11) and top[:, 2].min() == pytest.approx(1)


def test_downsampled_image_stays_in_range(tmp_path):
    checker = (np.indices((300, 300)).sum(axis=0) % 2 * 255).astype(np.uint8)   # worst case for Lanczos ringing
    Image.fromarray(checker).save(tmp_path/"c.png")
    m = heightfield(tmp_path/"c.png", {"size": [10, 10], "height": 1, "max_samples": 64})
    assert m.is_volume and m.bounds[1, 2] <= 2 + 1e-9


def test_large_heightfield_is_fast(tmp_path):
    import time
    np.save(tmp_path/"big.npy", np.random.default_rng(1).random((512, 512)))
    start = time.perf_counter()
    m = heightfield(tmp_path/"big.npy", {"size": [50, 50], "height": 2, "max_samples": 512})
    assert len(m.faces) == 4*511*511 + 4*2*511
    assert time.perf_counter()-start < 60   # about 3 s on the development machine; CI runners vary


def test_3mf_round_trip_units_components_and_transforms(tmp_path):
    box = trimesh.creation.box([10, 20, 3])
    data = export_3mf(box)
    assert data == export_3mf(box)              # deterministic bytes
    back = read_3mf(io.BytesIO(data))
    assert back.is_volume and back.volume == pytest.approx(600)
    # An inch-based package whose build item references a component with a translation.
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in box.vertices/25.4)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in box.faces)
    model = ('<model unit="inch" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
             f'<object id="1" type="model"><mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>'
             '<object id="2" type="model"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 1 0 0"/>'
             '</components></object></resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 2"/></build></model>')
    with zipfile.ZipFile(tmp_path/"inch.3mf", "w") as z:
        z.writestr("3D/3dmodel.model", model)
    mesh = load_mesh(tmp_path/"inch.3mf")
    assert mesh.volume == pytest.approx(600, rel=1e-6)
    assert mesh.bounds.mean(axis=0) == pytest.approx([25.4, 0, 50.8])


def test_3mf_cli_inspect_and_export(tmp_path):
    report = run("make", "box", "--size", 4, 5, 6, "-o", tmp_path/"b.3mf")
    assert report["exports"][0]["is_volume"]
    assert run("inspect", tmp_path/"b.3mf", "--require-solid")["volume"] == pytest.approx(120)
    (tmp_path/"bad.3mf").write_bytes(b"not a zip")
    with pytest.raises(cad.CADError, match="not a 3MF"):
        run("inspect", tmp_path/"bad.3mf")


def test_compile_refuses_to_overwrite_without_force(tmp_path):
    run("project", "init", tmp_path/"p", "--template", "arch-demo")
    target = tmp_path/"recipe.json"
    target.write_text("hand written")
    with pytest.raises(cad.CADError, match="exists"):
        run("project", "compile", tmp_path/"p", "-o", target)
    assert target.read_text() == "hand written"
    run("project", "compile", tmp_path/"p", "-o", target, "--force")
    assert json.loads(target.read_text())["version"] == 1


def test_svg_fills_open_paths_and_polylines_like_a_renderer(tmp_path):
    # The viewBox maps one user unit to 1 mm.
    assert svg_profile(tmp_path, '<path id="p" d="M0 0 H10 V10 H0"/>').area == pytest.approx(100, rel=1e-4)
    assert svg_profile(tmp_path, '<polyline id="p" points="0,0 10,0 10,10"/>').area == pytest.approx(50, rel=1e-4)
    # A filled straight line encloses nothing and is skipped instead of failing the import.
    area = svg_profile(tmp_path, '<rect x="0" y="0" width="10" height="10"/><path d="M0 20 L10 20"/>').area
    assert area == pytest.approx(100, rel=1e-4)


def test_literal_path_data_still_needs_z_or_width():
    raw = {"version": 1, "profiles": {"p": {"op": "path", "d": "M0 0 L10 0 L10 10"}},
           "solids": {"s": {"op": "extrude", "profile": "p", "height": 1}}, "exports": [{"solid": "s", "path": "x.stl"}]}
    with pytest.raises(cad.CADError, match="Open SVG path"):
        cad.Engine(cad.resolve_recipe(raw)).get("solids", "s")


def test_missing_svg_id_suggests_the_close_one(tmp_path):
    with pytest.raises(cad.CADError) as error:
        svg_profile(tmp_path, '<rect id="badge" width="5" height="5"/>', ids=["bagde"])
    assert "badge" in (error.value.hint or "")


def test_missing_segment_file_is_a_cad_error(tmp_path):
    root = tmp_path/"p"
    run("project", "init", root, "--template", "arch-demo")
    (root/"segments/structure/brace.json").unlink()
    with pytest.raises(cad.CADError, match="source file is missing"):
        run("project", "info", root)


def test_corrupt_build_state_rebuilds_with_a_warning(tmp_path):
    root = tmp_path/"p"
    run("project", "init", root, "--template", "arch-demo")
    run("project", "build", root, "--segment", "base")
    (root/".cdcad/build-state.json").write_text("{trunc")
    report = run("project", "build", root, "--segment", "base")
    assert report["built"] == 1 and report["warnings"]
    assert run("project", "build", root, "--segment", "base")["built"] == 0


def test_only_one_history_entry_is_current_after_restore(tmp_path):
    root = tmp_path/"p"
    run("project", "init", root, "--template", "arch-demo")
    run("project", "set", root, "/params/span", "65")
    run("project", "set", root, "/params/span", "70")
    run("project", "restore", root, "last")
    entries = run("project", "history", root)["entries"]
    assert [(e["action"], e["current"]) for e in entries] == [("restore", True), ("set", False), ("set", False)]


def test_split_keeps_a_void_inside_its_body(tmp_path):
    outer = trimesh.creation.icosphere(radius=10)
    inner = trimesh.creation.icosphere(radius=5)
    inner.invert()
    other = trimesh.creation.box([2, 2, 2])
    other.apply_translation([30, 0, 0])
    trimesh.util.concatenate([outer, inner, other]).export(tmp_path/"h.stl")
    parts = run("split", tmp_path/"h.stl", "--out-dir", tmp_path/"parts")["parts"]
    assert len(parts) == 2
    hollow = load_mesh(parts[0])
    assert hollow.is_volume and hollow.volume == pytest.approx(4/3*np.pi*(1000-125), rel=.02)


def test_dxf_layers_match_regardless_of_case(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    doc.layers.new("OUTLINE")
    doc.modelspace().add_lwpolyline([(0, 0), (5, 0), (5, 5), (0, 5)], close=True, dxfattribs={"layer": "OUTLINE"})
    doc.saveas(tmp_path/"l.dxf")
    assert read_dxf(tmp_path/"l.dxf", {"layers": ["outline"]}).area == pytest.approx(25)


def test_corner_radius_of_exactly_half_survives_float_rounding():
    raw = {"version": 1, "params": {"w": 0.3},
           "profiles": {"p": {"op": "rectangle", "size": ["=w", 1], "radius": "=(0.1+0.2)/2"}},
           "solids": {"s": {"op": "extrude", "profile": "p", "height": 1}}, "exports": [{"solid": "s", "path": "x.stl"}]}
    assert cad.Engine(cad.resolve_recipe(raw)).get("profiles", "p").area > 0


def test_inspect_density_reports_mass(tmp_path):
    run("make", "box", "--size", 10, 10, 10, "-o", tmp_path/"cube.stl")
    report = run("inspect", tmp_path/"cube.stl", "--density", 1.24)
    assert report["mass_g"] == pytest.approx(1.24)
    assert report["center_mass"] == pytest.approx([0, 0, 0], abs=1e-9)


def test_node_key_works_without_a_cache_dir():
    r = cad.resolve_recipe({"version": 1, "solids": {"s": {"op": "box", "size": [1, 1, 1]}},
                            "exports": [{"solid": "s", "path": "x.stl"}]})
    assert len(cad.Engine(r).node_key("solids", "s")) == 64


def test_malformed_recipe_json_is_a_cad_error_with_position(tmp_path):
    (tmp_path/"r.json").write_text('{"version": 1,}')
    with pytest.raises(cad.CADError, match="line 1 column"):
        cad.read_recipe(tmp_path/"r.json")
    with pytest.raises(cad.CADError, match="File not found"):
        cad.read_recipe(tmp_path/"missing.json")
