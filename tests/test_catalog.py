"""The operation catalog documents exactly what the engine accepts, and its examples work."""
import json
from pathlib import Path

import pytest

import cdcad as cad
from cdcad.catalog import OPS
from cdcad.schema import recipe_schema

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = [(kind, name) for kind, ops in OPS.items() for name in ops]


@pytest.mark.parametrize("kind,name", EXAMPLES, ids=[f"{k}-{n}" for k, n in EXAMPLES])
def test_every_operation_example_builds_a_closed_solid(kind, name):
    entry = OPS[kind][name]
    example = entry["example"]
    assert example, f"{kind}/{name} has no example"
    uses = [n for nodes in (example["profiles"], example["solids"]) for n in nodes.values() if n["op"] == name]
    assert uses, f"the example for {name} does not use it"
    raw = {"version": 1, **example, "exports": [{"solid": "part", "path": "part.stl"}]}
    mesh = cad.Engine(cad.resolve_recipe(raw), ROOT).get("solids", "part")
    assert mesh.is_volume


def test_every_property_is_documented():
    for kind, ops in OPS.items():
        for name, entry in ops.items():
            assert entry["summary"].endswith("."), name
            for prop, spec in {**entry["required"], **entry["optional"]}.items():
                assert spec[1], f"{kind}/{name}.{prop} has no description"


def test_saved_schema_matches_the_code():
    saved = json.loads((ROOT/"recipe.schema.json").read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(recipe_schema())), "regenerate with: cdcad schema > recipe.schema.json"


def test_saved_project_schema_matches_the_code():
    from cdcad.project_model import project_schema
    saved = json.loads((ROOT/"project.schema.json").read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(project_schema()))
