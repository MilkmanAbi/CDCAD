# CDCAD

CDCAD is a small command-line tool that builds STL files from numbers, formulas, drawings and images. You describe the part in a JSON file, an SVG, a grayscale picture or a bit of Python, and it writes the mesh and tells you whether that mesh is a closed solid. The name is short for CLI-driven CAD.

```sh
cdcad make box --size 40 25 6 -o block.stl
cdcad build examples/heightfield.json --out-dir models    # a picture in, a relief out
```

## What it's for

Automating the dull part of making models. In practice that's parts that come out of maths (a vase whose radius follows a sine wave, a ripple surface, holes on a computed circle), parts you want to regenerate with a different number, and images you want to turn into relief or outlines you want to extrude.

You can type all of it by hand. For a one-off that works, but it's a lot of typing, and the commands are really there so that scripts, Makefiles and other programs can do the typing for you.

I built it to kinda just accelerate making STLs for school projects. That's it. I write a function - it makes a STL.

## What it isn't

It isn't a CAD system and doesn't try to compete with one. If you want parametric CAD in Python, look at CadQuery or build123d. CDCAD has no sketch constraints, no STEP or BREP, no GUI, and no fillets or chamfers on solids. It works on triangle meshes, and a curve is a polygon with as many sides as you ask for.

It doesn't design anything for you either. There's no machine learning in it, no network access and no "describe what you want" mode. It does what the recipe says.

It doesn't judge printability. It checks that a mesh is watertight and has a positive volume, and `inspect --bed` compares the part's size with a build volume. Overhangs, wall thickness and supports are for you to look at.

## Install

You need Python 3.11 or newer. Install it from PyPI, ideally inside a virtualenv:

```sh
python -m pip install cdcad
cdcad --version
```

The dependencies (numpy, scipy, trimesh, shapely, manifold3d and a few more) come along automatically. For the development version, install from GitHub instead: `python -m pip install git+https://github.com/MilkmanAbi/CDCAD.git`. To work on it, clone the repo and run `python -m pip install -e ".[test]"`.

## Things to try

The examples are in the repo, so run these from a clone.

By hand:

```sh
cdcad make box --size 40 25 6 -o block.stl
cdcad inspect block.stl
cdcad extrude --path-data "M0 0 H40 V25 H0 Z M8 8 H16 V16 H8 Z" --height 6 -o plate.stl
```

Every command prints one JSON object on stdout (put `--pretty` before the command to indent it). The report for the box says `"is_volume": true` and `"volume": 6000.0`. The plate is a 40 x 25 rectangle with an 8 mm square hole, because a contour inside another one counts as a hole.

With a recipe. This is a JSON file of named profiles and solids, where any value starting with `=` is arithmetic on the parameters:

```json
{
  "version": 1,
  "params": {"w": 60, "d": 40, "h": 6},
  "profiles": {
    "outline": {"op": "rectangle", "size": ["=w", "=d"], "radius": 3},
    "hole": {"op": "circle", "radius": 4, "center": ["=w/4", 0]},
    "plate": {"op": "difference", "inputs": ["outline", "hole"]}
  },
  "solids": {"part": {"op": "extrude", "profile": "plate", "height": "=h"}},
  "exports": [{"solid": "part", "path": "part.stl"}]
}
```

```sh
cdcad build recipe.json --out-dir out
cdcad build recipe.json --out-dir out --set w=90 --set h=10 --force
```

The first makes a 60 x 40 x 6 plate, the second a 90 x 40 x 10 one. You get `sin`, `cos`, `sqrt`, `min`, `max`, `round` and a few others in expressions, and nothing else: no Python gets evaluated.

From a formula. `examples/vase.py` is a hollow vase whose radius is

```python
def radius(z):
    return 18 + 6 * math.sin(z / 12) + 4 * math.sin(z / 5)
```

It samples that every 1.5 mm, builds the wall as a polygon (up the outside, down the inside, across the floor) and revolves it. `python examples/vase.py out` writes `out/vase.stl`, 90 mm tall and about 23 cm³. `examples/ripple.py` does the same for a surface: numpy computes a 200 by 200 height map, saves it as `.npy`, and the `heightfield` operation turns it into an 80 mm square relief of roughly 160,000 triangles. `examples/design-sweep.py` builds one plate at three thicknesses and checks each volume against the formula.

From an image or a drawing:

```sh
cdcad build examples/heightfield.json --out-dir models
cdcad extrude examples/outline.svg --select plate --height 3 -o plate.stl
```

A grayscale image becomes a relief. Black is the base thickness and white is the base plus the height you set. An 8-bit image only has 256 levels, so a slow gradient shows steps, and if that bothers you, compute the height map in numpy instead (see the ripple). SVG and DXF outlines get extruded. From an SVG only filled shapes are read, so strokes and text are ignored. CDCAD doesn't trace photos into outlines or guess depth from them.

## Projects

For something made of several parts there are project folders. Each part is its own small recipe, and the project holds the shared parameters, layers, placements and named assemblies. Builds are incremental: in the arch demo, changing the span rebuilds the two outputs that depend on it and leaves the other three alone.

```sh
cdcad project init demo --template arch-demo
cdcad project build demo
cdcad project set demo /params/span 70
cdcad project build demo          # rebuilds arch and demo, skips the rest
cdcad project restore demo last   # takes that edit back
```

Every edit saves a snapshot, so `project history` lists them and `project restore` goes back to one. [PROJECTS.md](PROJECTS.md) has the rest.

## From scripts

Commands exit 0 on success, or 2 with a JSON error on stderr, so any language can drive it. A recipe can come in on stdin (`cdcad build -`), and from Python it's:

```python
from pathlib import Path
from cdcad import build_recipe, read_recipe

recipe = read_recipe(Path("recipe.json"), ["h=10"])
report = build_recipe(recipe, Path("."), Path("out"))
print(report["exports"][0]["volume"])
```

Each `cdcad` call imports numpy, scipy and the rest, which took about four seconds per command on the Windows machine this was written on. If you're making many models, use the Python API or `cdcad serve`, which keeps one process running and takes one JSON request per line. See [SCRIPTING.md](SCRIPTING.md).

## Limits

- Booleans need closed solids. CDCAD won't write a mesh that isn't one unless you pass `--allow-open`.
- Curves are polygons, 96 sides by default. Raise `segments` where it shows.
- `loft` can self-intersect when the sections differ a lot, and nothing checks for it.
- STL has no units, so everything is written in millimetres. `--units` only changes how you enter dimensions.
- DXF input handles lines, arcs, circles, ellipses, splines and polylines in the XY plane. Open or dangling contours are rejected rather than repaired.
- The tests run in CI on Linux, macOS (Intel and Apple Silicon) and Windows, with Python 3.11 and 3.13. [TEST_RESULTS.md](TEST_RESULTS.md) has the notes from local runs.
- A valid mesh isn't a good print. The checks are about the mesh, not about any printer.

## More

[REFERENCE.md](REFERENCE.md) lists every operation, flag and file format. `cdcad capabilities` and `cdcad schema` print the supported operations straight from the code. Changes are in [CHANGELOG.md](CHANGELOG.md). Tests are `python -m pytest` and take about a minute.

CDCAD is licensed under the GPL v3. See [LICENSE](LICENSE).
