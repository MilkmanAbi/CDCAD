from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

import cdcad as cad
from shapely.geometry import Point
from cdcad.mesh import load_mesh
from cdcad._profiles import arch_profile
from cdcad._common import atomic_bytes, json_bytes
from cdcad.cli import execute, parser
from cdcad import project_store as project


def run(*argv):
    return execute(parser().parse_args(list(map(str, argv))))


def model(node, profiles=None, solids=None):
    raw = project.segment_recipe(node)
    raw["profiles"] = profiles or {}
    raw["solids"].update(solids or {})
    return cad.Engine(cad.resolve_recipe(raw)).get("solids", "part")


def test_arch_geometry_bounds_and_open_bottom():
    n = {"op": "arch", "span": 40, "rise": 20, "leg": 10, "thickness": 4, "depth": 8}
    m = model(n)
    assert m.is_volume
    assert m.bounds == pytest.approx(np.array([[-24, -4, 0], [24, 4, 34]]))
    assert not arch_profile(n).contains(Point(0, 1))
    expected = ((24**2-20**2)*np.pi/2+8*10)*8
    assert m.volume == pytest.approx(expected, rel=.001)
    odd = model({**n, "segments": 7})
    assert odd.bounds == pytest.approx(m.bounds)


def test_expression_integer_counts_and_cutout_schema():
    r = project.segment_recipe({"op": "repeat", "input": "body", "counts": ["=4/2", 1.0, 1], "step": [3, 0, 0]})
    r["solids"]["body"] = {"op": "cylinder", "radius": 1, "height": 2, "segments": "=128/2"}
    result = cad.resolve_recipe(r)
    assert type(result["solids"]["part"]["counts"][0]) is int
    assert type(result["solids"]["body"]["segments"]) is int
    assert cad.Engine(result).get("solids", "part").is_volume
    with pytest.raises(cad.CADError, match="Schema"):
        cad.resolve_recipe(project.segment_recipe({"op": "wall", "length": 10, "height": 10, "thickness": 2,
            "cutouts": [{"shape": "rectangle", "radius": 1}]}))


@pytest.mark.parametrize("plane,extents", [("xy", [30, 20, 3]), ("xz", [30, 3, 20]), ("yz", [3, 30, 20])])
def test_panel_cutouts_planes_and_volume(plane, extents):
    m = model({"op": "panel", "size": [30, 20], "thickness": 3, "plane": plane,
               "cutouts": [{"shape": "rectangle", "size": [10, 5], "center": [-5, 0]},
                           {"shape": "circle", "radius": 2, "center": [9, 0]}]})
    assert m.is_volume
    assert m.extents == pytest.approx(extents)
    assert m.volume == pytest.approx((600-50-4*np.pi)*3, rel=.001)


def test_wall_with_arched_door_and_slot_window():
    m = model({"op": "wall", "length": 80, "height": 50, "thickness": 4,
               "cutouts": [{"shape": "arch", "span": 20, "rise": 10, "leg": 20, "center": [-15, 15]},
                           {"shape": "slot", "size": [15, 8], "center": [20, 32]}]})
    assert m.is_volume
    assert m.bounds[:, 2] == pytest.approx([0, 50])
    assert len(m.split()) == 1


def test_frame_beam_drill_and_grid():
    frame = model({"op": "frame", "size": [30, 20], "border": 2, "depth": 3})
    assert frame.volume == pytest.approx((600-26*16)*3)
    beam = model({"op": "beam", "start": [0, 0, 0], "end": [10, 10, 10], "size": [2, 3], "roll": 30})
    assert beam.is_volume
    assert beam.volume == pytest.approx(np.sqrt(300)*6)
    holes = model({"op": "drill", "input": "body", "positions": [[-5, 0, 0], [5, 0, 0]], "radius": 1, "depth": 20},
                  solids={"body": {"op": "box", "size": [20, 10, 5]}})
    assert holes.is_volume
    assert holes.volume == pytest.approx(1000-2*np.pi*5, rel=.001)
    grid = model({"op": "extrude", "profile": "grid", "height": 2},
                 profiles={"hole": {"op": "circle", "radius": 1},
                           "grid": {"op": "grid", "input": "hole", "counts": [3, 2], "step": [5, 5]}})
    assert grid.is_volume
    assert len(grid.split()) == 6


def test_attachment_uses_both_dependency_keys(tmp_path):
    raw = project.segment_recipe({"op": "align", "input": "body", "target": "base", "offset": [0, 0, -.2]})
    raw["solids"].update(body={"op": "box", "size": [2, 2, 4]}, base={"op": "box", "size": [10, 10, 2]})
    e = cad.Engine(cad.resolve_recipe(raw), cache_dir=tmp_path/"cache")
    m = e.get("solids", "part")
    assert m.bounds[0, 2] == pytest.approx(.8)
    key = e.node_key("solids", "part")
    raw["solids"]["base"]["size"][2] = 6
    e2 = cad.Engine(cad.resolve_recipe(raw), cache_dir=tmp_path/"cache")
    assert e2.node_key("solids", "part") != key
    assert e2.get("solids", "part").bounds[0, 2] == pytest.approx(2.8)


def test_equivalent_names_reuse_content_cache(tmp_path):
    r = project.segment_recipe({"op": "transform", "input": "a"})
    r["solids"].update(a={"op": "box", "size": [2, 3, 4]}, b={"op": "box", "size": [2, 3, 4]},
                       other={"op": "transform", "input": "b"})
    e = cad.Engine(cad.resolve_recipe(r), cache_dir=tmp_path/"cache")
    a, b = e.get("solids", "part"), e.get("solids", "other")
    assert e.node_key("solids", "part") == e.node_key("solids", "other")
    assert a is b


def test_blank_init_put_duplicate_place_stitch(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root)
    run("project", "put", root, "base", "--spec", json.dumps({"op": "box", "size": [20, 20, 2]}), "--layer", "base")
    run("project", "duplicate", root, "base", "upper", "--translate", 0, 0, 1.5)
    run("project", "stitch", root, "whole", "--members", "base", "upper")
    report = run("project", "build", root)
    assert report["built"] == 3
    fused = next(e for e in report["outputs"] if e["name"] == "whole")
    assert fused["volume"] == pytest.approx(20*20*3.5)
    assert fused["components"] == 1
    run("project", "place", root, "upper", "--translate", 0, 0, 1)
    report = run("project", "build", root)
    assert report["built"] == 2
    assert report["unchanged"] == 1


def test_demo_init_and_incremental_rebuild(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    first = run("project", "build", root)
    assert first["built"] == 5
    mtimes = {e["path"]: Path(e["path"]).stat().st_mtime_ns for e in first["outputs"]}
    second = run("project", "build", root)
    assert second["built"] == 0 and second["unchanged"] == 5
    assert not second["trace"]
    assert all(Path(p).stat().st_mtime_ns == t for p, t in mtimes.items())
    run("project", "set", root, "/params/span", "68")
    plan = run("project", "plan", root)
    changed = {e["name"] for e in plan["outputs"] if e["status"] == "build"}
    assert changed == {"arch", "demo"}
    third = run("project", "build", root)
    assert third["built"] == 2 and third["unchanged"] == 3
    for entry in third["outputs"]:
        assert load_mesh(Path(entry["path"])).is_volume


def test_output_tampering_is_detected(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    first = run("project", "build", root, "--segment", "arch")
    path = Path(first["outputs"][0]["path"])
    path.write_bytes(b"changed outside the builder")
    report = run("project", "build", root, "--segment", "arch")
    assert report["built"] == 1
    assert load_mesh(path).is_volume


def test_layers_dynamic_assembly_selection_and_targeting(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    run("project", "stitch", root, "parts", "--layers", "details", "--mode", "combine")
    run("project", "layer", root, "details", "--enabled", "false")
    info = run("project", "info", root)
    assert "panel" not in info["enabled_outputs"]
    assert "parts" not in info["enabled_outputs"]
    report = run("project", "build", root, "--layer", "structure")
    assert {e["name"] for e in report["outputs"]} == {"arch", "brace"}
    assert not (root/"build/assemblies/demo.stl").exists()
    with pytest.raises(cad.CADError, match="enabled"):
        run("project", "build", root, "--segment", "panel")


def test_revision_conflict_and_rejected_edits_leave_sources_unchanged(tmp_path):
    root = tmp_path/"demo"
    init = run("project", "init", root, "--template", "arch-demo")
    run("project", "set", root, "/params/span", "65", "--if-revision", init["revision"])
    before = (root/"project.json").read_bytes()
    with pytest.raises(cad.CADError, match="Revision conflict"):
        run("project", "set", root, "/params/span", "70", "--if-revision", init["revision"])
    with pytest.raises(cad.CADError, match="Schema"):
        run("project", "set", root, "/params/span", "-10")
    assert (root/"project.json").read_bytes() == before
    assert len(list((root/".cdcad/history").glob("*.json"))) == 1


def test_set_array_index_replaces_instead_of_inserts(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    run("project", "set", root, "/solids/part/size/0", "120", "--segment", "base")
    state = project.load_state(root)
    assert state["segments"]["base"]["solids"]["part"]["size"] == [120, 42]


def test_patch_batch_is_validated_before_any_write(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    state = project.load_state(root)
    ops = [{"op": "replace", "path": "/project/params/span", "value": 70},
           {"op": "test", "path": "/project/params/leg", "value": 999}]
    file = tmp_path/"patch.json"
    file.write_text(json.dumps(ops))
    with pytest.raises(cad.CADError, match="test failed"):
        run("project", "patch", root, "--file", file)
    assert project.load_state(root) == state
    ops[1] = {"op": "replace", "path": "/segments/arch/solids/part/depth", "value": 14}
    file.write_text(json.dumps(ops))
    run("project", "patch", root, "--file", file)
    state2 = project.load_state(root)
    assert state2["project"]["params"]["span"] == 70
    assert state2["segments"]["arch"]["solids"]["part"]["depth"] == 14


def test_attachment_tracks_target_and_prevents_cycles(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    run("project", "set", root, "/solids/part/thickness", "8", "--segment", "base")
    report = run("project", "build", root, "--segment", "arch")
    assert report["outputs"][0]["bounds"][0][2] == pytest.approx(6.2)
    with pytest.raises(cad.CADError, match="Cyclic"):
        run("project", "attach", root, "base", "arch")


def test_remove_references_and_recovery_journal(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    with pytest.raises(cad.CADError, match="Dependent"):
        run("project", "remove", root, "base")
    before = project.load_state(root)
    run("project", "remove", root, "base", "--cascade")
    after = project.load_state(root)
    assert "base" not in after["segments"]
    assert "demo" not in after["project"]["assemblies"]
    assert "attach" not in after["project"]["segments"]["arch"]
    assert (root/"segments/foundation/base.json").exists()
    atomic_bytes(root/".cdcad/pending.json", json_bytes({
        "before": {"project.json": before["project"]}, "after": {"project.json": after["project"]}}))
    with pytest.raises(cad.CADError, match="Unfinished"):
        run("project", "info", root)
    run("project", "recover", root, "--action", "rollback")
    assert project.load_state(root) == before


def test_paths_duplicate_members_and_namespace_protection(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    with pytest.raises(cad.CADError, match="inside"):
        run("project", "set", root, "/segments/arch/path", '"segments/../project.json"')
    with pytest.raises(cad.CADError, match="duplicate"):
        run("project", "stitch", root, "bad", "--members", "base", "base")
    with pytest.raises(cad.CADError, match="source/metadata"):
        run("project", "build", root, "--out-dir", root/"segments")
    with pytest.raises(cad.CADError, match="empty"):
        run("project", "init", root)


def test_lock_exclusion_and_compile_equivalence(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    with project.project_lock(root):
        with pytest.raises(cad.CADError, match="locked"):
            run("project", "check", root)
    compiled = tmp_path/"compiled.json"
    run("project", "compile", root, "-o", compiled)
    report = cad.build_recipe(cad.read_recipe(compiled), tmp_path, out_dir=tmp_path/"standalone", targets=["@arch"])
    assert report["exports"][0]["is_volume"]


def history_demo(tmp_path):
    """arch-demo with two span edits: returns (root, [rev0, rev1, rev2])."""
    root = tmp_path/"demo"
    revisions = [run("project", "init", root, "--template", "arch-demo")["revision"]]
    revisions.append(run("project", "set", root, "/params/span", "65")["revision"])
    revisions.append(run("project", "set", root, "/params/span", "70")["revision"])
    return root, revisions


def span(root):
    return project.load_state(root)["project"]["params"]["span"]


@pytest.mark.parametrize("entry,message", [
    ("../../project", "id shown by project history"),
    ("..\\..\\project", "id shown by project history"),
    ("20260101T000000000000Z-ABCDEF123456", "id shown by project history"),
    ("", "id shown by project history"),
    ("20260101T000000000000Z-0123456789ab", "No history entry"),
])
def test_restore_rejects_bad_entry_ids_without_touching_the_project(tmp_path, entry, message):
    root, revisions = history_demo(tmp_path)
    with pytest.raises(cad.CADError, match=message):
        run("project", "restore", root, entry)
    assert run("project", "info", root)["revision"] == revisions[-1]
    assert len(list((root/".cdcad/history").glob("*.json"))) == 2


def test_restore_with_no_history_and_with_stale_revision_or_pending_write(tmp_path):
    fresh = tmp_path/"fresh"
    run("project", "init", fresh, "--template", "arch-demo")
    with pytest.raises(cad.CADError, match="No history to restore"):
        run("project", "restore", fresh, "last")
    assert run("project", "history", fresh) == {"ok": True, "revision": run("project", "info", fresh)["revision"],
                                               "total": 0, "entries": []}
    root, revisions = history_demo(tmp_path)
    with pytest.raises(cad.CADError, match="Revision conflict"):
        run("project", "restore", root, "last", "--if-revision", revisions[0])
    assert span(root) == 70
    run("project", "restore", root, "last", "--if-revision", revisions[2])
    assert span(root) == 65
    atomic_bytes(root/".cdcad/pending.json", json_bytes({"before": {}, "after": {}}))
    with pytest.raises(cad.CADError, match="Unfinished"):
        run("project", "restore", root, "last")
    with pytest.raises(cad.CADError, match="Unfinished"):
        run("project", "history", root)


def test_restore_rejects_damaged_snapshots(tmp_path):
    root, revisions = history_demo(tmp_path)
    newest, older = sorted((root/".cdcad/history").glob("*.json"), reverse=True)
    newest.write_text('{"action": "set", "after_revision": "x"}')      # no "before"
    older.write_text("{truncated")
    listing = run("project", "history", root)
    assert [e.get("damaged") for e in listing["entries"]] == [True, True]
    for entry in listing["entries"]:
        with pytest.raises(cad.CADError, match="damaged"):
            run("project", "restore", root, entry["id"])
    with pytest.raises(cad.CADError, match="damaged"):
        run("project", "restore", root, "last")
    assert run("project", "info", root)["revision"] == revisions[-1]


def test_history_limit_must_be_positive(tmp_path):
    root, _ = history_demo(tmp_path)
    with pytest.raises(cad.CADError, match="--limit must be at least 1"):
        run("project", "history", root, "--limit", 0)
    assert len(run("project", "history", root, "--limit", 1)["entries"]) == 1


def test_history_and_restore_round_trip(tmp_path):
    root, (rev0, rev1, rev2) = history_demo(tmp_path)
    listing = run("project", "history", root)
    assert listing["revision"] == rev2 and listing["total"] == 2
    newest, older = listing["entries"]
    assert (newest["action"], newest["before_revision"], newest["after_revision"], newest["current"]) == ("set", rev1, rev2, True)
    assert (older["before_revision"], older["after_revision"], older["current"]) == (rev0, rev1, False)
    assert newest["id"] > older["id"] and newest["time"].endswith("Z")
    # Undo the newest edit; the restore is itself an edit, so it is listed and can be undone.
    undone = run("project", "restore", root, "last")
    assert (undone["changed"], undone["revision"], undone["restored_before"]) == (True, rev1, newest["id"])
    assert span(root) == 65
    assert [e["action"] for e in run("project", "history", root)["entries"]] == ["restore", "set", "set"]
    assert run("project", "restore", root, "last")["revision"] == rev2          # restoring again toggles back
    assert span(root) == 70
    # An older entry takes the project back to its initial state, byte for byte.
    initial = run("project", "restore", root, older["id"])
    assert initial["revision"] == rev0 and span(root) == 60
    assert run("project", "build", root)["built"] == 5
    # Nothing to change when the state already matches.
    assert run("project", "restore", root, older["id"])["changed"] is False


def test_history_order_survives_a_coarse_or_backwards_clock(tmp_path, monkeypatch):
    # Windows before Python 3.13 reads the clock in ~15 ms steps, so quick edits (or the
    # persistent worker) can share a timestamp; a clock can also step backwards.
    now = {"value": datetime(2030, 1, 1, tzinfo=timezone.utc)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now["value"]

    monkeypatch.setattr(project, "datetime", Clock)
    root = tmp_path/"demo"
    revisions = [run("project", "init", root, "--template", "arch-demo")["revision"]]
    for value in (61, 62, 63, 64):
        revisions.append(run("project", "set", root, "/params/span", value)["revision"])
    now["value"] = datetime(2020, 1, 1, tzinfo=timezone.utc)          # the clock jumps back
    revisions.append(run("project", "set", root, "/params/span", 65)["revision"])
    listing = run("project", "history", root)
    ids = [entry["id"] for entry in listing["entries"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 5
    # Newest first, each entry's "before" being the previous edit's result.
    assert [e["before_revision"] for e in listing["entries"]] == revisions[:-1][::-1]
    assert [e["current"] for e in listing["entries"]] == [True, False, False, False, False]
    assert run("project", "restore", root, "last")["revision"] == revisions[-2]      # undoes span=65
    assert span(root) == 64


def test_restore_returns_a_removed_segment_and_refuses_to_overwrite_a_stray_file(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root)
    run("project", "put", root, "block", "--spec", '{"op":"box","size":[2,2,2]}')
    run("project", "set", root, "/solids/part/size/0", "5", "--segment", "block")
    run("project", "remove", root, "block")
    assert "block" not in project.load_state(root)["segments"]
    entries = run("project", "history", root)["entries"]            # newest first: remove, set, put
    assert [e["action"] for e in entries] == ["remove", "set", "put"]
    run("project", "restore", root, entries[0]["id"])                # the edited block is back
    assert project.load_state(root)["segments"]["block"]["solids"]["part"]["size"] == [5, 2, 2]
    run("project", "remove", root, "block")
    # The removed segment's file stays on disk with size 5; restoring size 2 would overwrite it.
    with pytest.raises(cad.CADError, match="move it aside"):
        run("project", "restore", root, entries[1]["id"])
    assert "block" not in project.load_state(root)["segments"]
    assert not (root/".cdcad/pending.json").exists()


def test_generated_gitignore_covers_metadata_that_records_absolute_paths(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    run("project", "set", root, "/params/span", "70")
    run("project", "build", root)
    here = json.dumps(str(root.resolve()))[1:-1]               # the way JSON writes the path
    leaking = [p.relative_to(root).as_posix() for p in (root/".cdcad").rglob("*.json") if here in p.read_text()]
    assert {".cdcad/last-build.json", ".cdcad/build-state.json"} <= set(leaking)
    lines = (root/".gitignore").read_text().split()
    for relative in leaking:
        assert relative in lines or any(relative.startswith(line) for line in lines if line.endswith("/")), relative


def test_lock_message_names_the_owning_process(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    with project.project_lock(root):
        with pytest.raises(cad.CADError, match=rf"locked by another command \(process {os.getpid()}\)"):
            run("project", "info", root)
    (root/".cdcad/project.lock").write_text("not a pid")  # a damaged lock still blocks, without a holder
    with pytest.raises(cad.CADError, match="locked by another command; after"):
        run("project", "info", root)
    (root/".cdcad/project.lock").unlink()
    assert run("project", "info", root)["ok"]


def test_unfinished_agentcad_journal_is_not_silently_ignored(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    legacy = root/".agentcad"
    legacy.mkdir()
    (legacy/"pending.json").write_text("{}")
    for command in (("info",), ("set", "/params/span", "70"), ("build",), ("recover", "--action", "rollback")):
        with pytest.raises(cad.CADError, match="rename .agentcad to .cdcad"):
            run("project", command[0], root, *command[1:])
    assert project.load_state(root)["project"]["params"]["span"] == 60
    (legacy/"pending.json").unlink()
    assert run("project", "info", root)["ok"]


def test_worker_multiple_commands_and_error_isolation(tmp_path):
    root = tmp_path/"worker-project"
    requests = [
        {"id": 1, "args": ["project", "init", str(root)]},
        {"id": 2, "args": ["project", "put", str(root), "block", "--spec", '{"op":"box","size":[10,20,3]}']},
        {"id": 3, "args": ["project", "set", str(root), "/params/bad", "null"]},
        {"id": 4, "args": ["project", "build", str(root)]},
        {"id": 5, "args": ["nonsense"]},
        {"id": 6, "args": ["project", "build", str(root)]},
    ]
    result = subprocess.run([sys.executable, "-m", "cdcad", "serve"],
                            input="\n".join(json.dumps(r) for r in requests)+"\n", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3, 4, 5, 6]
    assert [r["ok"] for r in responses] == [True, True, False, True, False, True]
    assert responses[-1]["result"]["built"] == 0


def test_worker_stdin_isolation_and_invalid_json_number(tmp_path):
    recipe = project.segment_recipe({"op": "box", "size": [2, 3, 4]})
    requests = [json.dumps({"id": "stdin", "args": ["build", "-", "--out-dir", str(tmp_path)], "stdin": json.dumps(recipe)}),
                '{"id":NaN,"args":["capabilities"]}',
                json.dumps({"id": "help", "args": ["make", "arch", "--help"]})]
    result = subprocess.run([sys.executable, "-m", "cdcad", "serve"],
                            input="\n".join(requests)+"\n", capture_output=True, text=True)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["ok"] for r in responses] == [True, False, True]
    assert "--span" in responses[-1]["result"]["text"]
    assert load_mesh(tmp_path/"part.stl").volume == pytest.approx(24)


def test_repeat_limits_and_invalid_beam():
    repeated = model({"op": "repeat", "input": "body", "counts": [2, 2, 1], "step": [2, 2, 0], "fuse": False},
                     solids={"body": {"op": "box", "size": [1, 1, 1]}})
    assert repeated.volume == pytest.approx(4)
    assert len(repeated.split()) == 4
    with pytest.raises(cad.CADError, match="must differ"):
        model({"op": "beam", "start": [0, 0, 0], "end": [0, 0, 0], "size": [2, 3]})
    with pytest.raises(cad.CADError, match="10000"):
        model({"op": "repeat", "input": "body", "counts": [100, 101, 1], "step": [2, 2, 2]},
              solids={"body": {"op": "box", "size": [1, 1, 1]}})


@pytest.mark.parametrize("argv", [
    ("place", "nosuch", "--translate", "1", "2", "3"),
    ("attach", "nosuch", "base"),
    ("detach", "nosuch"),
    ("duplicate", "nosuch", "copy"),
    ("set", "/solids/part/size/0", "5", "--segment", "nosuch"),
])
def test_unknown_segment_names_are_cad_errors(tmp_path, argv):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    before = project.load_state(root)
    with pytest.raises(cad.CADError, match="Unknown segment: nosuch"):
        run("project", argv[0], root, *argv[1:])
    assert project.load_state(root) == before
    assert not (root/".cdcad/pending.json").exists()


@pytest.mark.parametrize("operations,message", [
    ([{"op": "add", "value": 1}], "string path"),
    ([{"op": "add", "path": 5, "value": 1}], "string path"),
    ([{"op": "add", "path": "/project/params/extra"}], "needs a value"),
    ([{"op": "test", "path": "/project/params/span"}], "needs a value"),
    ([{"op": "replace", "path": "/project/params/span", "value": 70}, 7], "Unsupported JSON patch operation at index 1"),
])
def test_malformed_patch_operations_are_cad_errors(tmp_path, operations, message):
    root = tmp_path/"demo"
    run("project", "init", root, "--template", "arch-demo")
    before = project.load_state(root)
    file = tmp_path/"patch.json"
    file.write_text(json.dumps(operations))
    with pytest.raises(cad.CADError, match=message):
        run("project", "patch", root, "--file", file)
    assert project.load_state(root) == before


def test_put_spec_that_is_not_json_is_a_cad_error(tmp_path):
    root = tmp_path/"demo"
    run("project", "init", root)
    with pytest.raises(cad.CADError, match="--spec is not valid JSON"):
        run("project", "put", root, "block", "--spec", "{not json")


def test_make_cli_flags_and_import_recipe_asset_paths(tmp_path):
    path = tmp_path/"arch.stl"
    report = run("make", "arch", "--span", 40, "--thickness", 4, "--depth", 8, "-o", path)
    assert report["exports"][0]["is_volume"]
    source = tmp_path/"source"
    source.mkdir()
    atomic_bytes(source/"part.json", json_bytes(project.segment_recipe({"op": "import", "path": "../arch.stl"})))
    root = tmp_path/"project"
    run("project", "init", root)
    run("project", "put", root, "imported", "--file", source/"part.json")
    report = run("project", "build", root)
    assert report["outputs"][0]["is_volume"]
