"""The examples in the documentation work as written."""
import json
import os
import re
from pathlib import Path

import pytest

import cdcad as cad
from cdcad.catalog import OPS

ROOT = Path(__file__).resolve().parent.parent


def blocks(name, language):
    return re.findall(rf"```{language}\n(.*?)```", (ROOT/name).read_text(encoding="utf-8"), re.S)


@pytest.mark.parametrize("name", ["README.md", "REFERENCE.md", "PROJECTS.md"])
def test_json_recipes_in_the_docs_build(tmp_path, name):
    recipes = [json.loads(text) for text in blocks(name, "json") if '"version"' in text]
    assert recipes, f"no recipes found in {name}"
    for raw in recipes:
        report = cad.build_recipe(cad.resolve_recipe(raw), tmp_path, tmp_path/"out", force=True)
        assert all(e["is_volume"] for e in report["exports"])
        assert all(c["ok"] for c in report.get("checks", []))


@pytest.mark.parametrize("name", ["README.md", "SCRIPTING.md"])
def test_python_examples_in_the_docs_run(tmp_path, monkeypatch, name):
    snippets = [text for text in blocks(name, "python") if "Recipe(" in text]
    assert snippets
    monkeypatch.chdir(tmp_path)
    for code in snippets:
        exec(compile(code, name, "exec"), {"__name__": "docs"})
    assert any(p.suffix == ".png" for p in tmp_path.iterdir())


def test_reference_tables_list_every_operation():
    text = (ROOT/"REFERENCE.md").read_text(encoding="utf-8")
    for kind, ops in OPS.items():
        for name in ops:
            assert f"| `{name}` |" in text, f"{kind}/{name} is missing from REFERENCE.md"


def test_changelog_mentions_the_current_version():
    text = (ROOT/"CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith(f"# {cad.__version__}") or text.startswith("# Unreleased")


def test_readme_links_work_on_pypi():
    text = (ROOT/"README.md").read_text(encoding="utf-8")
    relative = [link for link in re.findall(r"\]\(([^)]+)\)", text) if not link.startswith("http")]
    assert relative == [], f"relative links break on the PyPI page: {relative}"
    assert os.path.exists(ROOT/"gallery.png")
