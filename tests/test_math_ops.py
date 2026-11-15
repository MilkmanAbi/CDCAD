"""Expressions, field-driven operations, sweeps and the new primitives."""
import math

import numpy as np
import pytest

import cdcad as cad
from cdcad.expr import Field, evaluate, field_text


def solid(solids, profiles=None, params=None, part="part"):
    raw = {"version": 1, "profiles": profiles or {}, "solids": solids, "exports": [{"solid": part, "path": "x.stl"}]}
    if params:
        raw["params"] = params
    mesh = cad.Engine(cad.resolve_recipe(raw)).get("solids", part)
    assert mesh.is_volume, "result is not a closed solid"
    return mesh


def profile(node, **others):
    raw = {"version": 1, "profiles": {"p": node, **others}, "solids": {"s": {"op": "extrude", "profile": "p", "height": 1}},
           "exports": [{"solid": "s", "path": "x.stl"}]}
    return cad.Engine(cad.resolve_recipe(raw)).get("profiles", "p")


# ---- expressions ----------------------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("atan2(1, 1)", math.pi/4), ("hypot(3, 4)", 5), ("clamp(7, 0, 5)", 5), ("lerp(10, 20, 0.25)", 12.5),
    ("1 if 3 > 2 else 0", 1), ("2 if 1 > 2 else 3", 3), ("(1 < 2 < 3) + 0", 1), ("not 0", 1), ("e", math.e),
    ("log(8, 2)", 3), ("cbrt(27)", 3), ("sign(-4)", -1), ("smoothstep(0, 1, 0.5)", 0.5), ("round(2.5)", 2),
    ("7 // 2", 3), ("pow(2, 3)", 8), ("max(1, 5, 3)", 5),
])
def test_expression_functions_and_conditionals(text, value):
    assert evaluate(text, {}) == pytest.approx(value)


def test_power_hint_and_misspelling_hint():
    with pytest.raises(cad.CADError) as error:
        evaluate("x^2", {"x": 1})
    assert "**" in error.value.hint
    with pytest.raises(cad.CADError) as error:
        evaluate("widht/2", {"width": 1})
    assert "width" in error.value.hint


def test_parameter_errors_name_the_parameter_and_the_cycle():
    raw = {"version": 1, "params": {"a": "=b+1", "b": "=c*2", "c": "=a"}, "solids": {"s": {"op": "box", "size": [1, 1, 1]}},
           "exports": [{"solid": "s", "path": "x.stl"}]}
    with pytest.raises(cad.CADError, match="cycle a -> b -> c -> a"):
        cad.resolve_recipe(raw)
    raw["params"] = {"a": "=missing+1"}
    with pytest.raises(cad.CADError, match="Parameter a"):
        cad.resolve_recipe(raw)


def test_field_inlining_keeps_negative_parameters_correct():
    text, used = field_text("a**2 + x", ("x",), {"a": -3, "unused": 1})
    assert used == {"a"}
    assert Field(text, ("x",))(np.array([0.0]))[0] == pytest.approx(9)
    assert field_text("=a*x", ("x",), {"a": 2})[0] == "2 * x"           # a leading '=' is accepted


def test_field_vector_semantics_match_scalar():
    f = Field("x if x < 0 else (0 if x < 1 else x**2)", ("x",))
    xs = np.array([-2.0, 0.5, 3.0])
    assert list(f(xs)) == [f.scalar()(x) for x in xs] == [-2, 0, 9]


def test_field_parameters_change_the_cache_key(tmp_path):
    def key(r):
        raw = {"version": 1, "params": {"r": r},
               "solids": {"s": {"op": "implicit", "f": "x**2+y**2+z**2-r**2", "bounds": [[-6, -6, -6], [6, 6, 6]]}},
               "exports": [{"solid": "s", "path": "x.stl"}]}
        return cad.Engine(cad.resolve_recipe(raw), cache_dir=tmp_path).node_key("solids", "s")
    assert key(4) != key(5) and key(5) == key(5)


def test_literal_strings_are_never_evaluated():
    p = profile({"op": "text", "text": "=5", "size": 5})
    assert p.area > 0


def test_notes_are_allowed_and_do_not_change_cache_keys(tmp_path):
    def key(note):
        node = {"op": "box", "size": [1, 2, 3], **({"note": note} if note else {})}
        raw = {"version": 1, "description": "demo", "solids": {"s": node}, "exports": [{"solid": "s", "path": "x.stl"}]}
        return cad.Engine(cad.resolve_recipe(raw), cache_dir=tmp_path).node_key("solids", "s")
    assert key("the lid") == key(None)


# ---- validation messages --------------------------------------------------------------

@pytest.mark.parametrize("solids,message,hint", [
    ({"s": {"op": "cylindr", "radius": 1, "height": 1}}, "Unknown operation", "cylinder"),
    ({"s": {"op": "circle", "radius": 1}}, "Unknown operation", "profile operation"),
    ({"s": {"op": "box", "size": [1, 1, 1], "sise": 2}}, "Additional properties", "size"),
    ({"s": {"op": "cylinder", "radius": 1}}, "missing required 'height'", "cdcad describe cylinder"),
])
def test_validation_errors_explain_the_fix(solids, message, hint):
    with pytest.raises(cad.CADError, match=message) as error:
        cad.resolve_recipe({"version": 1, "solids": solids, "exports": [{"solid": "s", "path": "x.stl"}]})
    assert hint in error.value.hint


# ---- profiles -------------------------------------------------------------------------

def test_function_profile_area_and_lathe():
    assert profile({"op": "function", "f": "2", "range": [0, 5]}).area == pytest.approx(10)
    assert profile({"op": "function", "f": "x**2", "range": [0, 3], "samples": 2000}).area == pytest.approx(9, rel=1e-5)
    between = profile({"op": "function", "f": "x + 4", "base": "x", "range": [0, 2]})
    assert between.area == pytest.approx(8)
    vase = solid({"part": {"op": "revolve", "profile": "w", "segments": 256}},
                 {"w": {"op": "function", "f": "5", "var": "y", "range": [0, 10]}})
    assert vase.volume == pytest.approx(math.pi*25*10, rel=1e-3)


def test_curve_profile_circle_and_self_intersecting_rose():
    circle = profile({"op": "curve", "x": "3*cos(t)", "y": "3*sin(t)", "samples": 2000})
    assert circle.area == pytest.approx(math.pi*9, rel=1e-4)
    star = profile({"op": "curve", "x": "10*cos(2*t)", "y": "10*sin(2*t) + 0*t", "t": [0, "=pi"], "samples": 500})
    assert star.area > 0


def test_regular_polygon_across_flats():
    hexagon = profile({"op": "regular_polygon", "sides": 6, "across_flats": 10})
    _, ymin, _, ymax = hexagon.bounds
    assert ymax - ymin == pytest.approx(10)
    with pytest.raises(cad.CADError, match="exactly one"):
        profile({"op": "regular_polygon", "sides": 6})


def test_text_size_alignment_and_missing_glyphs():
    h = profile({"op": "text", "text": "H", "size": 10})
    assert h.bounds[3] - h.bounds[1] == pytest.approx(10, rel=0.02)
    centred = profile({"op": "text", "text": "HIH", "size": 5, "halign": "center", "valign": "center"})
    xmin, ymin, xmax, ymax = centred.bounds
    assert xmin == pytest.approx(-xmax, abs=0.5) and ymin == pytest.approx(-ymax, abs=1e-6)
    assert len(profile({"op": "text", "text": "AB\nC", "size": 5}).geoms) >= 3
    assert profile({"op": "text", "text": "Ω≈π±µ°", "size": 5}).area > 0
    with pytest.raises(cad.CADError, match="no glyphs"):
        profile({"op": "text", "text": "漢", "size": 5})


def test_profile_array_polar_and_hull():
    ring = profile({"op": "array", "input": "c", "count": 8, "rotate": 45}, c={"op": "circle", "radius": 1, "center": [10, 0]})
    assert len(ring.geoms) == 8
    hull = profile({"op": "hull", "inputs": ["a", "b"]}, a={"op": "circle", "radius": 1, "segments": 64},
                   b={"op": "circle", "radius": 1, "center": [10, 0], "segments": 64})
    assert hull.area == pytest.approx(20 + math.pi, rel=1e-2)


# ---- extrusion, revolve, sweep --------------------------------------------------------

def test_scaled_extrusion_is_an_exact_frustum_and_twist_turns_the_top():
    square = {"q": {"op": "rectangle", "size": [10, 10]}}
    frustum = solid({"part": {"op": "extrude", "profile": "q", "height": 9, "scale": 0.5}}, square)
    assert frustum.volume == pytest.approx(100*9*(1 + 0.5 + 0.25)/3, rel=1e-9)
    twisted = solid({"part": {"op": "extrude", "profile": "q", "height": 10, "twist": 90, "slices": 60}}, square)
    assert twisted.volume == pytest.approx(1000, rel=1e-2)
    top = twisted.vertices[np.isclose(twisted.vertices[:, 2], 10)]
    assert any(np.allclose(v[:2], [-5, 5]) for v in top)          # corner (5, 5) turned 90 degrees
    tapered_hole = solid({"part": {"op": "extrude", "profile": "p", "height": 5, "scale": [2, 1]}},
                         {"p": {"op": "polygon", "points": [[-5, -5], [5, -5], [5, 5], [-5, 5]],
                                "holes": [[[-1, -1], [1, -1], [1, 1], [-1, 1]]]}})
    assert tapered_hole.volume == pytest.approx(96*5*1.5, rel=1e-9)


@pytest.mark.parametrize("inner", [2, 0])
def test_partial_revolve(inner):
    mesh = solid({"part": {"op": "revolve", "profile": "p", "angle": 90, "segments": 720}},
                 {"p": {"op": "polygon", "points": [[inner, 0], [5, 0], [5, 10], [inner, 10]]}})
    assert mesh.volume == pytest.approx(math.pi*(25-inner**2)*10/4, rel=1e-4)
    assert mesh.bounds[0][:2] == pytest.approx([0, 0], abs=1e-9)


def test_straight_sweep_equals_extrusion_and_keeps_holes():
    ring = {"p": {"op": "difference", "inputs": ["o", "i"]}, "o": {"op": "circle", "radius": 3}, "i": {"op": "circle", "radius": 2}}
    tube = solid({"part": {"op": "sweep", "profile": "p", "points": [[0, 0, 0], [0, 0, 5], [0, 0, 20]]}}, ring)
    area = 96/2*math.sin(math.tau/96)*(9-4)
    assert tube.volume == pytest.approx(area*20, rel=1e-6)


def test_closed_sweep_makes_a_torus_and_a_knot():
    disc = {"c": {"op": "circle", "radius": 1, "segments": 32}}
    ring = solid({"part": {"op": "sweep", "profile": "c", "closed": True,
                           "curve": {"x": "10*cos(t)", "y": "10*sin(t)", "z": "0", "samples": 400}}}, disc)
    assert ring.volume == pytest.approx(2*math.pi**2*10*1*(32/(2*math.pi)*math.sin(2*math.pi/32)), rel=1e-3)
    knot = solid({"part": {"op": "sweep", "profile": "c", "closed": True, "curve": {
        "x": "(10 + 4*cos(3*t))*cos(2*t)", "y": "(10 + 4*cos(3*t))*sin(2*t)", "z": "4*sin(3*t)", "samples": 600}}}, disc)
    assert knot.volume > 0 and len(knot.split()) == 1


def test_up_frame_keeps_the_profile_radial_on_a_helix():
    thread = solid({"part": {"op": "sweep", "profile": "tooth", "frame": "up",
                             "curve": {"x": "10*cos(t)", "y": "10*sin(t)", "z": "2*t/tau", "t": [0, "=3*tau"], "samples": 360}}},
                   {"tooth": {"op": "polygon", "points": [[0, -0.6], [1, 0], [0, 0.6]]}})
    radius = np.hypot(thread.vertices[:, 0], thread.vertices[:, 1])
    assert radius.max() == pytest.approx(11, rel=1e-3) and radius.min() == pytest.approx(10, rel=1e-3)


def test_sweep_needs_one_path_and_a_closed_sweep_cannot_scale():
    disc = {"c": {"op": "circle", "radius": 1}}
    with pytest.raises(cad.CADError, match="exactly one of points or curve"):
        solid({"part": {"op": "sweep", "profile": "c"}}, disc)
    with pytest.raises(cad.CADError, match="cannot change scale"):
        solid({"part": {"op": "sweep", "profile": "c", "closed": True, "scale": 2,
                        "points": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]}}, disc)


# ---- fields ---------------------------------------------------------------------------

def test_implicit_sphere_and_limits():
    ball = solid({"part": {"op": "implicit", "f": "x**2 + y**2 + z**2 - r**2", "bounds": [[-6, -6, -6], [6, 6, 6]],
                           "resolution": 0.2}}, params={"r": 5})
    assert ball.volume == pytest.approx(4/3*math.pi*125, rel=0.01)
    with pytest.raises(cad.CADError, match="cells") as error:
        solid({"part": {"op": "implicit", "f": "x", "bounds": [[0, 0, 0], [100, 100, 100]], "resolution": 0.1}})
    assert "resolution" in error.value.hint
    with pytest.raises(cad.CADError, match="not negative anywhere"):
        solid({"part": {"op": "implicit", "f": "1", "bounds": [[0, 0, 0], [1, 1, 1]]}})
    with pytest.raises(cad.CADError, match="not finite|failed"):
        solid({"part": {"op": "implicit", "f": "log(x)", "bounds": [[-1, -1, -1], [1, 1, 1]]}})


def test_graph_volume_and_base_check():
    flat = solid({"part": {"op": "graph", "f": "3", "x": [0, 4], "y": [0, 5], "samples": [5, 5]}})
    assert flat.volume == pytest.approx(60)
    wave = solid({"part": {"op": "graph", "f": "2 + x", "x": [0, 2], "y": [0, 1], "base": -1}})
    assert wave.volume == pytest.approx((3*2 + 2)*1)
    with pytest.raises(cad.CADError, match="reaches the base"):
        solid({"part": {"op": "graph", "f": "x", "x": [-1, 1], "y": [0, 1]}})


# ---- primitives and modifiers ---------------------------------------------------------

def test_cone_torus_and_polyhedron():
    frustum = solid({"part": {"op": "cone", "radius": 4, "radius_top": 2, "height": 6, "segments": 512}})
    assert frustum.volume == pytest.approx(math.pi*6*(16 + 8 + 4)/3, rel=1e-3)
    assert frustum.bounds[:, 2] == pytest.approx([-3, 3])
    ring = solid({"part": {"op": "torus", "major": 10, "minor": 2, "segments": 256, "minor_segments": 128}})
    assert ring.volume == pytest.approx(2*math.pi**2*10*4, rel=2e-3)
    tetra = solid({"part": {"op": "polyhedron", "points": [[0, 0, 0], [6, 0, 0], [0, 6, 0], [0, 0, 6]],
                            "faces": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]}})       # mixed winding is fixed
    assert tetra.volume == pytest.approx(36)
    cube = solid({"part": {"op": "polyhedron", "points": [[x, y, z] for x in (0, 2) for y in (0, 2) for z in (0, 2)]}})
    assert cube.volume == pytest.approx(8)
    with pytest.raises(cad.CADError, match="minor radius"):
        solid({"part": {"op": "torus", "major": 2, "minor": 3}})


def test_mirror_array_rotation_and_multi_hull():
    block = {"b": {"op": "box", "size": [4, 2, 2]}, "m": {"op": "transform", "input": "b", "translate": [3, 0, 0]}}
    pair = solid({**block, "part": {"op": "mirror", "input": "m", "keep": True}})
    assert pair.volume == pytest.approx(32) and pair.bounds[:, 0] == pytest.approx([-5, 5])
    only = solid({**block, "part": {"op": "mirror", "input": "m", "normal": [1, 0, 0], "origin": [10, 0, 0]}})
    assert only.bounds[:, 0] == pytest.approx([15, 19])
    polar = solid({**block, "part": {"op": "array", "input": "m", "count": 4, "rotate": [0, 0, 90]}})
    assert polar.extents[:2] == pytest.approx([10, 10])
    hull = solid({"a": {"op": "box", "size": [2, 2, 2]}, "c": {"op": "transform", "input": "a", "translate": [8, 0, 0]},
                  "part": {"op": "hull", "inputs": ["a", "c"]}})
    assert hull.volume == pytest.approx(40)
    with pytest.raises(cad.CADError, match="exactly one of input or inputs"):
        solid({"a": {"op": "box", "size": [1, 1, 1]}, "part": {"op": "hull", "input": "a", "inputs": ["a"]}})


@pytest.mark.parametrize("payload", ["=((((9**99)**99)**99)**99)", "=round(5, -10**99)", "=(10**99)**2", "=round(5, -1e99)"])
def test_expressions_cannot_build_huge_numbers(payload):
    # Run in a separate process with a timeout, so a regression fails instead of hanging the suite.
    import json, subprocess, sys
    raw = {"version": 1, "params": {"a": payload}, "solids": {"s": {"op": "box", "size": ["=a", 1, 1]}}, "exports": []}
    result = subprocess.run([sys.executable, "-m", "cdcad", "validate", "-"], input=json.dumps(raw),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and json.loads(result.stderr)["type"] == "CADError"


def test_a_parameter_may_share_a_function_name():
    raw = {"version": 1, "params": {"sin": 2, "min": 3, "a": "=sin(0) + sin"},
           "solids": {"s": {"op": "graph", "f": "min(x, 1) + min", "x": [0, 1], "y": [0, 1]}}, "exports": []}
    resolved = cad.resolve_recipe(raw)
    assert resolved["params"]["a"] == 2
    assert resolved["solids"]["s"]["f"] == "min(x, 1) + 3"


def test_and_or_mean_the_same_in_dimensions_and_fields():
    f = Field("(x or 5) + (x and 7) + (not x)", ("x",))
    xs = np.array([0.0, 2.0])
    assert list(f(xs)) == [f.scalar()(x) for x in xs] == [6, 9]
    assert evaluate("0 or 5", {}) == 5 and evaluate("2 and 3", {}) == 3


def test_sweep_sample_count_from_an_expression():
    mesh = solid({"part": {"op": "sweep", "profile": "c", "curve": {"x": "10*cos(t)", "y": "10*sin(t)", "z": "t",
                                                                      "t": [0, 6], "samples": "=96/2"}}},
                 {"c": {"op": "circle", "radius": 1}})
    assert mesh.volume > 0
