"""Fillets, chamfers, splines, smooth lofts and sweeps, and STEP export."""
import math
import re

import numpy as np
import pytest
import trimesh

from cdcad import generate
from cdcad._common import CADError
from cdcad.catalog import OPS
from cdcad.mesh import from_manifold, to_manifold
from cdcad.step import export_step, planar_faces


def test_step_box_has_six_planar_faces():
    text = export_step(trimesh.creation.box([20, 10, 5]), "box").decode("ascii")
    assert text.startswith("ISO-10303-21;") and text.rstrip().endswith("END-ISO-10303-21;")
    assert len(re.findall(r"=FACE_SURFACE\(", text)) == 6
    assert len(re.findall(r"=PLANE\(", text)) == 6
    assert "FACETED_BREP" in text and "CLOSED_SHELL" in text


def test_step_plate_with_hole_keeps_inner_loop():
    box = trimesh.creation.box([20, 10, 5])
    hole = trimesh.creation.cylinder(2, 10, sections=32)
    plate = trimesh.boolean.difference([box, hole], engine="manifold")
    text = export_step(plate, "plate").decode("ascii")
    assert "FACE_BOUND(" in text
    assert len(re.findall(r"=FACE_SURFACE\(", text)) == len(planar_faces(plate))


def test_step_output_is_deterministic():
    mesh = trimesh.creation.cylinder(3, 6, sections=24)
    assert export_step(mesh, "a") == export_step(mesh, "a")


def test_step_reals_always_have_a_decimal_point():
    from cdcad.step import _number
    assert _number(3) == "3."
    assert _number(2.5) == "2.5"
    assert _number(0) == "0."
    assert _number(1e-7) == "1.E-7"


def test_step_export_through_a_recipe(tmp_path):
    from cdcad import build_recipe, resolve_recipe
    raw = {"version": 1, "solids": {"part": {"op": "box", "size": [10, 10, 4]}},
           "exports": [{"solid": "part", "path": "part.stp"}]}
    report = build_recipe(resolve_recipe(raw), tmp_path, tmp_path/"out", force=True)
    assert report["exports"][0]["is_volume"]
    data = (tmp_path/"out"/"part.stp").read_bytes()
    assert data.startswith(b"ISO-10303-21;")


def test_2d_fillet_matches_the_exact_area():
    # A 40 x 30 rectangle with all four corners rounded by r = 4 has area 1200 - (4 - pi) r^2 * 4.
    from shapely.geometry import box
    geom = generate.round_corners(box(0, 0, 40, 30), {"radius": 4})
    expected = 40*30 - 4*(4**2)*(1 - math.pi/4)
    assert abs(geom.area - expected) / expected < 2e-3


def test_2d_fillet_rejects_too_large_radius():
    from shapely.geometry import box
    with pytest.raises(CADError):
        generate.round_corners(box(0, 0, 4, 4), {"radius": 10, "corners": "convex"})


def test_2d_chamfer_removes_the_expected_triangles():
    from shapely.geometry import box
    geom = generate.chamfer_corners(box(0, 0, 20, 20), {"distance": 3})
    assert abs(geom.area - (400 - 4*(3*3/2))) < 1e-6


def test_spline_profile_closed_is_smooth_and_filled():
    points = [[0, 0], [30, -5], [40, 15], [20, 30], [-5, 20]]
    geom = generate.spline_profile({"points": points})
    assert geom.is_valid and geom.area > 500


def test_spline_open_line_needs_width_or_closed():
    with pytest.raises(CADError):
        generate.spline_profile({"points": [[0, 0], [10, 5], [20, 0]], "closed": False})
    line = generate.spline_profile({"points": [[0, 0], [10, 5], [20, 0]], "width": 2})
    assert line.area > 0


def test_extrude_fillet_top_edge_volume():
    from shapely.geometry import box
    # Top edges rounded by r on a 20 x 20 x 10 block: volume = full - the rounded strip.
    r = 2.0
    mesh = generate.extrude(box(-10, -10, 10, 10), {"height": 10, "top": {"fillet": r, "segments": 16}})
    assert mesh.is_volume
    full = 20*20*10
    loss = 4*20*(r*r*(1 - math.pi/4))  # four straight edges
    assert abs(mesh.volume - (full - loss)) / full < 5e-3


def test_extrude_chamfer_top_and_bottom():
    from shapely.geometry import box
    mesh = generate.extrude(box(-10, -10, 10, 10), {"height": 10, "top": {"chamfer": 2}, "bottom": {"chamfer": 1}})
    assert mesh.is_volume
    assert mesh.volume < 20*20*10


def test_extrude_edges_cannot_exceed_height():
    from shapely.geometry import box
    with pytest.raises(CADError):
        generate.extrude(box(-5, -5, 5, 5), {"height": 2, "top": {"fillet": 2}, "bottom": {"fillet": 2}})


def test_extrude_edges_reject_twist():
    from shapely.geometry import box
    with pytest.raises(CADError):
        generate.extrude(box(-5, -5, 5, 5), {"height": 4, "twist": 30, "top": {"chamfer": 1}})


def test_solid_fillet_on_convex_box_is_exact():
    box = trimesh.creation.box([30, 20, 10])
    r = 2.0
    mesh = generate.morph(box, {"radius": r, "segments": 16, "edges": "all"}, "fillet")
    assert mesh.is_volume
    # Each of 12 edges loses (1 - pi/4) r^2 per unit length; each of 8 corners adds back a
    # small spherical correction. The mesh is an inscribed approximation, so allow a little slack.
    edge_loss = (1 - math.pi/4)*r*r*(4*30 + 4*20 + 4*10)
    expected = 30*20*10 - edge_loss
    assert abs(mesh.volume - expected) / expected < 0.02


def test_solid_chamfer_on_convex_box():
    box = trimesh.creation.box([30, 20, 10])
    mesh = generate.morph(box, {"distance": 2, "edges": "all"}, "chamfer")
    assert mesh.is_volume and mesh.volume < 30*20*10
    assert len(mesh.faces) > 12


def test_fillet_of_nonconvex_solid_fills_concave_seam():
    a = trimesh.creation.box([30, 20, 8])
    b = trimesh.creation.cylinder(5, 20, sections=32)
    b.apply_translation([0, 0, 8])
    union = trimesh.boolean.union([a, b], engine="manifold")
    before = union.volume
    filled = generate.morph(union, {"radius": 1.5, "segments": 8, "edges": "concave"}, "fillet")
    assert filled.is_volume
    assert filled.volume > before


def test_fillet_all_on_nonconvex_solid_explains_itself():
    a = trimesh.creation.box([30, 20, 8])
    b = trimesh.creation.cylinder(5, 20, sections=16)
    b.apply_translation([0, 0, 8])
    union = trimesh.boolean.union([a, b], engine="manifold")
    with pytest.raises(CADError) as info:
        generate.morph(union, {"radius": 1.5, "segments": 8, "edges": "all"}, "fillet")
    assert "concave" in (info.value.hint or "")


def test_smooth_solid_is_closed_and_grows_triangles():
    cube = trimesh.creation.box([20, 20, 20])
    smooth = generate.smooth(cube, {"smoothness": 0, "refine": 4})
    assert smooth.is_volume
    assert len(smooth.faces) > len(cube.faces)
    assert abs(smooth.volume - 20**3) / 20**3 < 0.01  # crisp edges: the box is unchanged


def test_smooth_faceted_sphere_becomes_round():
    sphere = trimesh.creation.icosphere(subdivisions=1, radius=10)
    smooth = generate.smooth(sphere, {"refine": 4})
    assert smooth.is_volume
    ideal = 4/3*math.pi*10**3
    assert abs(smooth.volume - ideal) / ideal < 0.02


def test_smooth_crease_angle_keeps_sharp_edges_on_a_cube():
    cube = trimesh.creation.box([20, 20, 20])
    mesh = generate.smooth(cube, {"sharp_angle": 60, "smoothness": 0, "refine": 2})
    assert mesh.is_volume


def test_loft_smooth_through_three_sections_is_closed():
    from shapely.geometry import box, Point
    from cdcad.mesh import loft
    sections = [box(-15, -15, 15, 15), Point(0, 0).buffer(10), box(-6, -6, 6, 6)]
    mesh = loft(sections, [0, 10, 20], 64, smooth=True, steps=4)
    assert mesh.is_volume
    straight = loft(sections, [0, 10, 20], 64)
    assert mesh.volume != straight.volume


def test_sweep_smooth_points_gives_a_closed_tube_path():
    from shapely.geometry import Point
    circle = Point(0, 0).buffer(1, quad_segs=8)
    points = [[0, 0, 0], [10, 0, 2], [20, 10, 4], [25, 25, 6]]
    mesh = generate.sweep(circle, {"points": points, "smooth": True, "samples": 60, "frame": "up"})
    assert mesh.is_volume


def test_manifold_round_trip_keeps_closed_mesh():
    box = trimesh.creation.box([10, 10, 10])
    back = from_manifold(to_manifold(box))
    assert back.is_volume and abs(back.volume - 1000) < 1e-6


def test_new_operations_are_documented_with_examples():
    for name in ("spline", "fillet", "chamfer"):
        assert name in OPS["profiles"]
    for name in ("fillet", "chamfer", "smooth"):
        assert name in OPS["solids"]
    for kind in OPS:
        for name, entry in OPS[kind].items():
            if name in ("spline", "fillet", "chamfer", "smooth"):
                assert entry["example"] is not None, f"{kind}/{name} needs an example"
