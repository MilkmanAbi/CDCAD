# CDCAD from scripts

Use the `cdcad` executable or `python -m cdcad` with the same arguments. Normal commands emit one JSON value on stdout. Exit status `0` indicates success, and execution errors produce a JSON object on stderr with status `2`. Argument errors use argparse text on stderr with status `2`. `--pretty` indents stdout for reading. Commands apply only the requested operation and never prompt before writing.

## Shell workflows

```powershell
cdcad capabilities
cdcad --pretty resolve examples/enclosure.json --set width=100 --set height=24
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

Scripts can generate recipe input in any language and submit UTF-8 JSON to `cdcad build -`. Use `cdcad schema` for the resolved recipe contract and `cdcad project schema` for the project manifest. Give each variant its own output directory and use `--cache-dir` to reuse shared geometry.

## Python API

```python
from pathlib import Path

from cdcad import build_recipe, read_recipe

source = Path("examples/enclosure.json")
recipe = read_recipe(source, ["width=100", "height=24"])
report = build_recipe(recipe, base=source.parent, out_dir=Path("models"))
print(report["exports"][0]["volume"])
```

Use `resolve_recipe(dictionary, overrides)` when constructing the recipe in memory. `build_recipe` accepts a resolved recipe and returns the same report as the CLI. `Engine(recipe, base=...)` provides access to named geometry with `get("solids", name)` or `get("profiles", name)` in recipe units.

Run the measured plate example from the source directory after installation:

```powershell
python examples/design-sweep.py sweep-results
```

The script computes hole centers with trigonometry, builds three variants, and checks each mesh volume against the area of the polygonal profile times thickness. It shares a geometry cache across variants.

## Persistent subprocess

`cdcad serve` accepts one JSON object per line. Send an argument vector without the executable and an optional `stdin` string. Responses echo `id` and contain `ok`, `seconds`, and either `result` or `error` and `type`. Commands execute sequentially and an invalid request leaves the process available for the next line. Close stdin to end the session.

```jsonl
{"id":"dimensions","args":["resolve","examples/enclosure.json","--set","width=100"]}
{"id":"assembly","args":["project","build","my-demo","--assembly","demo"]}
```

See [examples/project-worker.py](examples/project-worker.py) for a complete subprocess workflow and [PROJECTS.md](PROJECTS.md) for revisions, mutations and incremental builds.
