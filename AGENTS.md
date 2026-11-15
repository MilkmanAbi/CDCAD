# Notes for coding agents

Two situations: using CDCAD to make a model for someone, and changing CDCAD itself.

## Making models with CDCAD

Start with `cdcad guide --text`: one page with the workflow, a recipe skeleton, the conventions and every operation. `cdcad describe OP --text` gives one operation's properties, defaults and an example that builds. Both come from the code, so they match the installed version.

The loop:

1. Write a recipe JSON file. Put every dimension someone might change in `params` and refer to it with `"=name"`; give nodes meaningful names; use `note` to say what a node is for.
2. `cdcad validate recipe.json`. Fix errors using their `hint` (misspellings, a profile operation used as a solid, a missing property). Read the `warnings`: an unused parameter or an unreachable node usually means a wrong reference.
3. `cdcad render recipe.json -o view.png` and look at the picture. The 2 x 2 sheet shows iso, front, top and right views with axis triads and the overall size. Check proportions, orientation and that holes go where they should.
4. Add `checks` for what the person asked for (a size, a volume, a mass with `density`, `"components": {"approx": 1}` for one piece, `"fits"` for a printer bed), then `cdcad build recipe.json --out-dir out`. Report the numbers from the build report, not your estimate.

Things that commonly go wrong:

- Primitives (`box`, `cylinder`, `cone`, `sphere`, `torus`) are centred on the origin; `extrude` starts at z = 0. Translate deliberately.
- Booleans need overlapping volumes. Faces or edges that only touch can produce a mesh that fails the STL check; extend cutters past the surface (for example a hole cutter taller than the plate, starting below it).
- Angles in properties are degrees; inside expressions `sin`/`cos` take radians.
- Powers are `**`, not `^`.
- `difference` subtracts all later inputs from the first; to subtract several things, list them all.
- A profile is 2D. Make solids with `extrude`, `revolve`, `loft` or `sweep`.
- Field expressions (`implicit`, `graph`, `function`, `curve`, sweep `curve`) are formulas without `=`; inside `implicit` is where f < 0.
- `implicit` cost grows with (size / resolution)³; start coarse.

When you can run Python, `cdcad.Recipe` builds the same JSON with operators (`a - b`) and parameter arithmetic; see SCRIPTING.md. Where an assistant can only call tools, `cdcad mcp` serves the same features over the Model Context Protocol.

## Working on CDCAD

- Setup: `python -m pip install -e ".[test]"`; tests: `python -m pytest -q` (a couple of minutes). CI runs Linux, macOS and Windows on Python 3.11 and 3.13, so keep to 3.11 syntax (no nested same-quote f-strings, no `itertools.batched`).
- Layout: `cli.py` and `commands/` (argument parsing, one JSON result per command); `recipe.py` and `expr.py` (parameters and the expression sandbox); `catalog.py` (every operation's properties, docs and example) and `schema.py` (JSON Schema and validation built from it); `graph.py`; `engine.py` (evaluation and caching); `_profiles.py`, `generate.py`, `mesh.py` (geometry); `build.py` (exports, checks, warnings); `render.py`; `builder.py`; `mcp.py` and `worker.py` (long-running modes); `project_*.py` (project folders).
- Adding an operation: add it to `catalog.py` with docs and an example (the tests build every example), implement it in `engine.py` (usually calling `generate.py`), and regenerate `recipe.schema.json` with `cdcad --pretty schema > recipe.schema.json` (a test compares them).
- References between nodes only work through `input`, `inputs`, `profile`, `target` and `sections[].profile`: dependency checks, cache keys and project namespacing all rely on those names. Source files only work through `path`, which is hashed into cache keys, protected from overwriting and rebased in projects.
- Every command prints exactly one JSON object; raise `CADError(message, hint=...)` for anything the user can fix.
- The cache key includes a hash of the package's code and bundled font, so geometry changes invalidate old caches automatically.
