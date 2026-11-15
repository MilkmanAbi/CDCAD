# CDCAD from scripts

Use the `cdcad` executable or `python -m cdcad` with the same arguments. Normal commands emit one JSON value on stdout. Exit status `0` indicates success, and execution errors produce a JSON object on stderr with status `2`: `{"ok": false, "error": ..., "type": ..., "hint": ...}`, where the optional `hint` names a likely fix. Argument errors use argparse text on stderr with status `2`. `--pretty` indents stdout for reading. Commands apply only the requested operation and never prompt before writing.

## Shell workflows

```powershell
cdcad capabilities
cdcad describe                       # every operation and expression function
cdcad describe sweep --text          # one operation, readable, with an example
cdcad --pretty resolve examples/enclosure.json --set width=100 --set height=24
cdcad validate examples/enclosure.json
cdcad render examples/enclosure.json -o enclosure.png --set width=100
cdcad build examples/enclosure.json --set width=100 --out-dir models --dry-run
cdcad build examples/enclosure.json --set width=100 --out-dir models --report models/report.json
```

`resolve` prints the complete resolved recipe. Paths keep their original text, so use the original recipe directory as `--base-dir` when piping resolved output to `build -`. For example, in PowerShell:

```powershell
cdcad resolve examples/enclosure.json --set width=100 |
    cdcad build - --base-dir examples --out-dir models
```

On macOS or Linux, you can also redirect the recipe file straight into the command.

```sh
cdcad build - --base-dir examples --out-dir models < examples/enclosure.json
```

Scripts can generate recipe input in any language and submit UTF-8 JSON to `cdcad build -`. Use `cdcad schema` for the resolved recipe contract and `cdcad project schema` for the project manifest. Give each variant its own output directory and use `--cache-dir` to reuse shared geometry. Put assertions in the recipe's `checks` so a variant that goes wrong fails the build instead of producing a file.

## Python API

```python
from pathlib import Path

from cdcad import build_recipe, read_recipe

source = Path("examples/enclosure.json")
recipe = read_recipe(source, ["width=100", "height=24"])
report = build_recipe(recipe, base=source.parent, out_dir=Path("models"))
print(report["exports"][0]["volume"])
```

Use `resolve_recipe(dictionary, overrides)` when constructing the recipe in memory. `build_recipe` accepts a resolved recipe and returns the same report as the CLI. `Engine(recipe, base=...)` provides access to named geometry with `get("solids", name)` (a `trimesh.Trimesh`) or `get("profiles", name)` (a Shapely geometry) in recipe units.

### The recipe builder

`cdcad.Recipe` writes recipes with Python syntax and still produces plain JSON:

```python
from cdcad import Recipe, fx

r = Recipe(params={"w": 60, "d": 40, "h": 6}, density=1.24)
p = r.p                                        # parameters, as expressions
plate = r.rectangle([p.w, p.d], radius=3) - r.circle(4, center=[p.w/4, 0])
part = plate.extrude(p.h).rename("plate")
lug = r.cylinder(3, 10).translate(0, 0, p.h + 5)
r.export(part | lug, "plate.stl")
r.check(part, volume={"min": 1000}, fits=[220, 220, 250])

r.to_dict()                    # the JSON recipe; r.save("plate.json") writes it
r.build("out", force=True)     # build report, as from cdcad build
r.mesh(part, w=90)             # trimesh.Trimesh in mm, with a parameter override
r.render(part, "plate.png")    # preview picture
```

- Every operation from `cdcad describe` is a method. Positional arguments fill the required properties in order, or the inputs of `union`, `difference`, `intersection`, `combine` and `hull`. Keyword arguments are the properties.
- Nodes support `a | b` and `a + b` (union), `a - b` (difference) and `a & b` (intersection), and shortcuts: `translate`, `rotate`, `scale`, `hull`, `array`, `extrude`, `revolve`, `sweep`, `offset`, `mirror`, `cut`, `align` and `rename`.
- `r.p.name` is a parameter as an expression; arithmetic on it (`p.w/4 + 1`) becomes `"=(w)/4+1"`, and `fx.sin(p.a)` and the other functions build calls. numpy numbers and arrays are converted to plain JSON.
- `r.profile(op, ...)` and `r.solid(op, ...)` are explicit forms, needed for names that exist in both kinds (`union`, `hull`...) when no input says which.

`examples/wheel.py` builds wheels with several spoke counts this way, and `examples/vase.py`, `ripple.py` and `design-sweep.py` show the plain dictionary style.

## Persistent subprocess

`cdcad serve` accepts one JSON object per line. Send an argument vector without the executable and an optional `stdin` string. Responses echo `id` and contain `ok`, `seconds`, and either `result` or `error`, `type` and sometimes `hint`. Commands execute sequentially and an invalid request leaves the process available for the next line. Close stdin to end the session.

```jsonl
{"id":"dimensions","args":["resolve","examples/enclosure.json","--set","width=100"]}
{"id":"assembly","args":["project","build","my-demo","--assembly","demo"]}
```

See [examples/project-worker.py](examples/project-worker.py) for a complete subprocess workflow and [PROJECTS.md](PROJECTS.md) for revisions, mutations and incremental builds.

## MCP server

`cdcad mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server on stdin/stdout, for assistants that call tools: `cdcad_guide`, `cdcad_describe`, `cdcad_validate`, `cdcad_build`, `cdcad_render` (returns the PNG as an image, so the assistant sees the model), `cdcad_inspect` and `cdcad_cli` (any command line). Recipes can be passed inline as JSON objects or as file paths; paths are relative to the directory the server was started in. Register it with your client as a stdio server, for example an `mcpServers` entry `{"command": "cdcad", "args": ["mcp"]}`. It has no dependencies beyond CDCAD's own and no network access.
