# CDCAD reference

Command details, the operation tables, and the rules for units, coordinates, expressions and file formats. The short version is in [README.md](README.md). Project folders are covered in [PROJECTS.md](PROJECTS.md), scripting and the Python builder in [SCRIPTING.md](SCRIPTING.md), and the workflow for coding assistants in [AGENTS.md](AGENTS.md).

## First model

```powershell
cdcad make box --size 40 25 6 -o block.stl
cdcad --pretty inspect block.stl --require-solid --density 1.24
cdcad render block.stl -o block.png
cdcad make --help
cdcad make cone --help
```

Dimensions default to millimeters. Pass `--units cm`, `--units m`, or `--units in` to model in other units. Exported coordinates use millimeters. `make` covers arch, wall, panel, frame, beam, box, cylinder, cone, sphere and torus; the flags come from the same operation table as recipes.

Check a part against a printer's build volume with `cdcad inspect block.stl --bed 150 150 150`. The report gains `bed_fit`: whether the part fits as modeled, whether it fits with the axes reassigned, and the clearance per axis in mm. `--require-fit` turns a part that fits in no axis-aligned orientation into an error, so a script can gate on it. `--density` (g/cm³; PLA is about 1.24) adds `mass_g`.

## Look before you print

```powershell
cdcad render part.stl -o part.png
cdcad render recipe.json -o part.png --solid lid --set width=90 --view iso --size 1200
```

`render` draws a PNG with flat shading: by default a 2 x 2 sheet of the iso, front (from −Y), top (from +Z) and right (from +X) views, each with an axis triad, and the overall size in the corner. `--view` picks one of `iso`, `front`, `back`, `left`, `right`, `top`, `bottom`. A `.json` input is a recipe: the solid (`--solid`, default the first export) is built in memory and nothing but the picture is written. It needs no OpenGL, so it works over SSH and in CI. Meshes over 250,000 faces are simplified for the picture only.

## Extrude a drawing

```powershell
cdcad extrude --path-data "M0 0 H40 V25 H0 Z M8 8 H16 V16 H8 Z" --height 6 -o plate.stl
cdcad inspect plate.stl --require-solid
```

The first path describes a 40 × 25 rectangle; the second path makes an 8 × 8 hole. The model is 6 mm tall. Literal paths default to even-odd fill, so nested contours become holes irrespective of direction.

Import a drawing:

```powershell
cdcad extrude examples/outline.svg --select plate --height 3 -o plate.stl
cdcad extrude examples/drawing.dxf --select OUTLINE --height 12 -o bracket.stl
```

Use `--force` to replace an existing output. Normal command results are compact JSON on stdout. Put `--pretty` before the command for indented JSON. Application errors are JSON on stderr with exit code 2: `{"ok": false, "error", "type", "hint"}`, where `hint` (when present) suggests the fix, such as the operation or property you probably meant. Argument usage errors are standard argparse text.

## Recipes

```powershell
cdcad build examples/svg-layers.json --out-dir models --report models/report.json
cdcad build examples/enclosure.json --out-dir models --set width=100 --set height=24
```

SVG shape IDs or DXF layers become named profiles. Extrude each profile with its own `height` and starting `z`, then fuse them. For engraved details, make a cutter and subtract it. The included SVG example creates a 3 mm plate and raises the badge to 7.5 mm.

Save this parametric plate as `recipe.json`, or pipe the document to CDCAD through stdin.

```json
{
  "version": 1,
  "units": "mm",
  "description": "A plate with one hole",
  "params": {"w": 60, "d": 40, "h": 6},
  "density": 1.24,
  "profiles": {
    "outline": {"op": "rectangle", "size": ["=w", "=d"], "radius": 3},
    "hole": {"op": "circle", "radius": 4, "center": [15, 0], "note": "M8 clearance"},
    "cut_outline": {"op": "difference", "inputs": ["outline", "hole"]}
  },
  "solids": {
    "part": {"op": "extrude", "profile": "cut_outline", "height": "=h"}
  },
  "exports": [{"solid": "part", "path": "part.stl"}],
  "checks": [{"solid": "part", "volume": {"min": 10000}, "size_z": {"approx": "=h"}, "fits": [220, 220, 250]}]
}
```

```powershell
cdcad validate recipe.json
cdcad --pretty resolve recipe.json --set h=10
cdcad build recipe.json --out-dir models --set h=10
Get-Content recipe.json -Raw | cdcad build - --out-dir models --force
```

Top-level fields: `version` (1), `units`, `description` (free text), `params`, `density` (g/cm³), `profiles`, `solids`, `exports` and `checks`. Every node may carry a `note`; notes are ignored by the geometry and by cache keys. `validate` also returns `warnings`: parameters no node uses, and nodes that no export or check reaches.

Input and default output paths are relative to the recipe file. For stdin they are relative to the current directory. `--base-dir` changes the input/default-output root. `--out-dir` sets the export root and rejects escaping paths; use it whenever you build a recipe you didn't write. `--target NAME` builds only exports of that solid and its dependencies; repeat the flag for multiple targets. Schema and graph checks still cover all nodes.

### Checks

`checks` is a list of assertions about solids, evaluated after meshing and before anything is written. A failing check stops the build with every failure listed. Each check names a `solid` and any of:

- `volume`, `area`, `size_x`, `size_y`, `size_z`: in recipe units (units³, units², units);
- `mass`: grams, using the recipe's `density`;
- `components`: number of separate pieces;

each as `{"min": a, "max": b}`, `{"approx": v}` (relative tolerance 0.001), `{"approx": v, "rel": r}` or `{"approx": v, "abs": a}`. Also `"is_volume": true` and `"fits": [x, y, z]` (mm; any axis-aligned orientation). Values may be `=expressions`. The build report lists every check with its measured value.

## Expressions

Values beginning `=` are arithmetic expressions with named parameters. Parameters can depend on one another in any order; a cycle is reported as `cycle a -> b -> a` and a missing name with a suggestion. Overrides apply to declared parameters and accept JSON numbers or strings beginning `=`.

- Operators: `+ - * / // % **` (powers are `**`; `^` is rejected with a hint), comparisons `< <= > >= == !=` (chains allowed), `a if cond else b`, `and`, `or`, `not`.
- Constants: `pi`, `tau`, `e`.
- Functions: `sin cos tan asin acos atan atan2 sinh cosh tanh sqrt cbrt exp log log10 log2 pow hypot abs sign min max clamp lerp smoothstep smin smax ceil floor round radians degrees`. Trigonometric functions use radians. `round` rounds half to even (`round(2.5)` is 2). `smin(a, b, k)` is a smooth minimum for blending implicit fields. `cdcad describe` lists them with one-line descriptions.

The parser accepts this list only, with no attributes, subscripts, strings, lambdas or keywords. Only after that check is the expression compiled, with no builtins available. An expression is at most 1024 characters, exponents written as numbers are at most 100, and results must be finite with magnitude at most 1e100.

**Field expressions** are properties that are formulas of variables rather than numbers: `f` of `implicit` (x, y, z) and `graph` (x, y), `f` and `base` of `function` (x or y), `x`/`y` of `curve` (t) and `x`/`y`/`z` of a sweep `curve` (t). They are written without `=` (a leading `=` is accepted), may use parameters, and are evaluated on whole grids with numpy. When a recipe is resolved, the parameters a field reads are written into it as numbers, so a resolved or compiled recipe is self-contained and a changed parameter changes the cache key.

Strings that are never expressions: `text` of `text`, `d` of `path`, SVG `ids`, DXF `layers` and notes, even when they begin with `=`.

## Edit STL files

```powershell
cdcad edit source.stl --scale 1 1 1.5 --rotate 0 0 90 --translate 10 0 0 --repair -o changed.stl
cdcad edit source.stl --cut-origin 0 0 12 --cut-normal 0 0 -1 -o shortened.stl
cdcad edit source.stl --faces 2000 -o simplified.stl
cdcad boolean difference body.stl cutter.stl -o drilled.stl
cdcad boolean union base.stl raised.stl -o fused.stl
cdcad split assembly.stl --out-dir components
cdcad section body.stl --z 5 -o section.svg
```

`edit` applies scale → rotate → translate → repair → cut → simplify → convex hull, omitting steps you did not request. Use a recipe when another order is needed. The positive side of the cut plane is retained; normal `[0,0,-1]` retains the material below the plane. Capped cuts use Manifold and require solid inputs, including when the cut crosses a hollow region. `--no-cap` leaves the cut boundary open. Open mesh export needs `--allow-open`.

`split` writes one file per body. A hollow part (an outer shell with an inward-facing shell inside it) stays one body: each void goes with the smallest shell that contains it.

For a reproducible multi-step STL edit, see `examples/edit-mesh.json`, which imports the included `samples/enclosure.stl`. Rebuild that source with `cdcad build examples/enclosure.json --out-dir samples --force` if you change its parameters. Alternatively change the import path to the location of your own STL.

You can convert an existing mesh by changing the output extension, for example `cdcad edit part.stl -o part.3mf`. Mesh inputs support STL, 3MF, OBJ, PLY, OFF and GLB. Outputs support STL, 3MF, OBJ, PLY, OFF and GLB. Material, texture and scene hierarchy preservation is outside the scope of this geometry tool. OBJ files should contain their geometry inline; do not rely on material data.

## Operation reference

Run `cdcad describe` for the list and `cdcad describe OP` for one operation's properties, types, defaults and a working example. `cdcad schema` prints the exact, machine-readable JSON Schema (with descriptions); `recipe.schema.json` is a saved copy. The schema describes **resolved** recipes; expression strings are resolved by the CLI before validation. Unknown properties are rejected to catch misspellings, with a suggestion. The tables below are generated from the same catalog. Lengths are in recipe units and angles in degrees.

| 2D operation (profiles) | Required | Optional (default) |
|---|---|---|
| `rectangle` | `size` | `center` ([0, 0]), `radius` (0) |
| `circle` | `radius` | `center` ([0, 0]), `segments` (96) |
| `ellipse` | `radii` | `center` ([0, 0]), `segments` (96) |
| `regular_polygon` | `sides` | `radius`, `across_flats`, `center` ([0, 0]), `rotate` (0) |
| `polygon` | `points` | `holes` |
| `stroke` | `points`, `width` | `closed` (false), `cap` (round), `join` (round) |
| `slot` | `size` | `center` ([0, 0]) |
| `function` | `f`, `range` | `var` (x), `base` (0), `samples` (128) |
| `curve` | `x`, `y` | `t` ([0, tau]), `samples` (256) |
| `text` | `text` | `size` (10), `path`, `halign` (left), `valign` (baseline), `spacing` (1), `line_spacing` (1.2), `segments` (6) |
| `path` | `d` | `width`, `step` (0.5), `fill_rule` (evenodd) |
| `svg` | `path` | `ids`, `step` (0.5), `flip_y` (true), `fill_rule` |
| `dxf` | `path` | `layers`, `tolerance` (0.05) |
| `union` | `inputs` | — |
| `difference` | `inputs` | — |
| `intersection` | `inputs` | — |
| `offset` | `input`, `distance` | `join` (round) |
| `transform` | `input` | `scale` ([1, 1]), `rotate` (0), `translate` ([0, 0]) |
| `hull` | — | `input`, `inputs` |
| `array` | `input`, `count` | `step` ([0, 0]), `rotate` (0), `center` ([0, 0]) |
| `grid` | `input`, `counts`, `step` | — |
| `arch_opening` | `span` | `rise` (span/2), `leg` (0), `segments` (64) |
| `arch` | `span`, `thickness` | `rise` (span/2), `leg` (0), `segments` (64) |
| `frame` | `size`, `border` | — |

| 3D operation (solids) | Required | Optional (default) |
|---|---|---|
| `box` | `size` | — |
| `cylinder` | `radius`, `height` | `segments` (96) |
| `cone` | `radius`, `height` | `radius_top` (0), `segments` (96) |
| `sphere` | `radius` | `subdivisions` (3) |
| `torus` | `major`, `minor` | `segments` (96), `minor_segments` (48) |
| `polyhedron` | `points` | `faces` |
| `extrude` | `profile`, `height` | `z` (0), `twist` (0), `scale` (1), `slices` (auto) |
| `revolve` | `profile` | `angle` (360), `segments` (96) |
| `loft` | `sections` | `samples` (96) |
| `sweep` | `profile` | `points`, `curve`, `closed` (false), `frame` (rmf), `up` ([0, 0, 1]), `twist` (0), `scale` (1) |
| `implicit` | `f`, `bounds` | `resolution` (largest side/80), `level` (0) |
| `graph` | `f`, `x`, `y` | `base` (0), `samples` ([128, 128]) |
| `heightfield` | `path`, `size`, `height` | `base` (1), `max_samples` (256), `invert` (false), `flip_y` |
| `import` | `path` | `units` (recipe units), `repair` (false) |
| `union` | `inputs` | — |
| `difference` | `inputs` | — |
| `intersection` | `inputs` | — |
| `combine` | `inputs` | — |
| `transform` | `input` | `scale` ([1, 1, 1]), `rotate` ([0, 0, 0]), `translate` ([0, 0, 0]), `matrix` |
| `mirror` | `input` | `normal` ([1, 0, 0]), `origin` ([0, 0, 0]), `keep` (false) |
| `array` | `input`, `count` | `step` ([0, 0, 0]), `rotate` ([0, 0, 0]), `fuse` (true) |
| `repeat` | `input`, `counts`, `step` | `fuse` (true) |
| `hull` | — | `input`, `inputs` |
| `cut` | `input`, `origin`, `normal` | `cap` (true) |
| `repair` | `input` | `fill_holes` (true) |
| `simplify` | `input`, `faces` | — |
| `align` | `input`, `target` | `anchor` (bottom), `target_anchor` (top), `offset` ([0, 0, 0]) |
| `drill` | `input`, `positions`, `radius`, `depth` | `axis` (z), `segments` (64) |
| `panel` | `size`, `thickness` | `cutouts`, `plane` (xy), `center` ([0, 0, 0]), `radius` (0) |
| `wall` | `length`, `height`, `thickness` | `cutouts`, `origin` ([0, 0, 0]) |
| `frame` | `size`, `border`, `depth` | `plane` (xz), `center` ([0, 0, 0]) |
| `arch` | `span`, `thickness`, `depth` | `rise` (span/2), `leg` (0), `segments` (64), `origin` ([0, 0, 0]) |
| `beam` | `start`, `end`, `size` | `roll` (0) |

Each node also requires `op`. References point to nodes in the same namespace, except a solid's `profile` and loft sections which reference `profiles`. Forward references are allowed; cycles are rejected. Difference subtracts all later inputs from the first. Intersection keeps the overlap of all inputs. `hull` takes `input` or `inputs`.

### Notes on the maths-driven operations

- `function`: with `var` x the region lies between y = f(x) and y = `base` over `range`; with `var` y it lies between x = f(y) and x = `base`, the shape `revolve` needs. `base` may itself be a formula of the same variable. Where f crosses the base the region splits into lobes.
- `curve`: `samples` points of (x(t), y(t)) for t in `t` (default 0 to tau), joined into a closed outline. A self-intersecting curve is filled even-odd (the inner pentagon of a pentagram is empty).
- `graph`: the surface is sampled on a `samples` = [nx, ny] grid over the `x` and `y` ranges; the solid reaches down to the flat `base`, and the build stops if f touches or dips below it.
- `implicit`: inside is f < `level` (0). The field is meshed by Manifold's level set (marching tetrahedra on a body-centred cubic grid) with grid spacing `resolution` (default: the largest side of `bounds` / 80); the result is clipped and closed by `bounds`. At most 4 million grid cells. Features thinner than about two cells vanish.
- `sweep`: path from `points` or a parametric `curve` ({x, y, z, t, samples}). With `frame` rmf the profile follows rotation-minimising frames (the double reflection method of Wang, Jüttler, Zheng and Liu, 2008); a closed path spreads the leftover twist evenly so the ends meet. With `frame` up, profile Y stays as close to `up` as the path allows and profile X points outward along the path's normal, so on a helix around Z a profile drawn like a revolve profile (X radial, Y axial) stays radial; this suits threads and springs. `twist` adds roll; `scale` changes size linearly to the end (open paths only).
- `extrude` with `twist` or `scale`: the outline is stacked in `slices` layers (default one per 5° of twist), each rotated about and scaled from the origin.
- `revolve` with `angle` below 360 leaves flat end faces; profile points on the axis are merged.
- `text`: `size` is the height of capital letters (measured on the font's H). The bundled font is a subset of DejaVu Sans with Latin, Greek and common maths symbols; `path` uses any TrueType/OpenType file instead. No kerning is applied.
- `polyhedron`: faces may wind either way (they are made consistent); polygon faces are fanned into triangles, so give convex faces. Without `faces`, the convex hull of the points.
- `mirror`: reflects in the plane through `origin` with `normal`; `keep` unions the reflection with the original.
- `array`: copy i is rotated by i × `rotate` and then moved by i × `step`, so rotate [0, 0, 360/n] with no step is a polar pattern. Profiles take a single angle and a rotation `center`.

## Coordinates, units and source formats

- Recipe dimensions use `mm` (default), `cm`, `m`, or `in`. Exported coordinates are always **millimeters**. STL has no unit metadata; 3MF output declares millimeters. Mesh inspection and quick mesh editing assume their input coordinates are already mm; 3MF input is converted from the file's declared unit.
- Extrusions start at Z=0 by default and grow upward. Rectangles, circles and ellipses are centered on their `center` (default `[0,0]`). Primitive boxes, cylinders, cones, spheres and tori are centered on the origin. This differs deliberately from extrusion's Z origin.
- Coordinates are right-handed, and extrusion grows along +Z. Transforms use the origin as pivot and apply scale, then rotation, then translation. Rotation is fixed-axis XYZ Euler in degrees; a positive Z rotation turns +X toward +Y. Matrices act on homogeneous column vectors. A `cut` keeps the side its `normal` points toward.
- SVG physical dimensions and `viewBox` are respected at 96 PPI; a unitless pixel is 25.4/96 mm. SVG transforms are applied. The Y axis is flipped by default for right-handed CAD coordinates, which can produce negative Y positions. Use a profile transform to move it. Import precision is limited by SVG parsing and curve sampling.
- Only filled SVG geometric shapes are imported. A filled open path or polyline is closed the way SVG renderers fill it; subpaths that enclose no area are skipped. Stroke-only SVG, text, CSS clipping, masks and filter semantics are not modeled. Convert text/strokes to filled outlines first. Selection uses shape IDs, not group IDs. SVG elements are unioned; holes must be contours inside the same path with an appropriate fill rule. Literal `path` nodes still need `Z` (or a stroke `width`) on each subpath.
- DXF supports LINE, ARC, CIRCLE, ELLIPSE, SPLINE, LWPOLYLINE and planar POLYLINE contours. It reads modelspace at Z=0; nested closed contours use even-odd fill. Layer selection ignores case, as DXF does. Open/dangling segments and unsupported selected entities are rejected. Select clean layers or explode blocks/text first. Coordinates use recipe units; `$INSUNITS` is not automatically applied. Endpoint gaps are not automatically snapped closed. DXF bulges/curves are flattened with `tolerance`.
- Heightfields accept grayscale images (8- or 16-bit, or 32-bit float in [0,1]; colour images use their luminance) or a 2D `.npy` array of normalized values in `[0,1]`. Black is `base`; white is `base+height`. Base must be positive. The result includes a flat bottom and sidewalls. Pictures keep their on-screen orientation: the top row of the picture is the high-Y edge. Arrays follow numpy's meshgrid convention instead: row 0 is the Y=0 edge. `flip_y` overrides either. Images may be resampled to `max_samples` along the longer dimension. `relief.png` and `heightfield.json` are included.
- 3MF input reads the core specification: objects with meshes or components, build items and their transforms, components in other model parts (production extension `p:path`, as written by Bambu Studio and PrusaSlicer), and the model unit. Colours, materials and print settings are ignored. 3MF output is a single millimeter object, byte-identical for identical meshes.
- Loft takes one hole-free contour per strictly increasing Z section. It resamples boundaries and connects them with triangles; sharp corners can change and extreme changes can self-intersect. Matching contour shapes work best. Subtract holes afterward. Revolve takes a hole-free 2D polygon with X≥0, with X as radius and Y as height.

## Build planning and reports

```powershell
cdcad capabilities
cdcad build recipe.json --out-dir models --cache-dir .cad-cache --report models/build.json
cdcad build recipe.json --out-dir models --cache-dir .cad-cache --force --set h=8
cdcad build recipe.json --out-dir models --dry-run
```

The engine builds only exported dependency chains, memoizes shared nodes, and performs planar holes/offsets before triangulation when you model them that way. Native Manifold performs mesh booleans. Curves expose sampling controls rather than forcing huge meshes. Heightfields and graphs use indexed grid meshes built with numpy.

The optional disk cache stores profiles as WKB and meshes as compressed NumPy arrays with pickle disabled. Keys include the node, recursive dependencies, source-file bytes, recipe units, a hash of the package's code and bundled font, and dependency versions. Changed dimensions or source contents invalidate dependent nodes; notes do not. Corrupt cache entries are recomputed. Cache read/write and hashing have overhead; this is most useful for repeated builds with expensive shared subgraphs. No performance comparison to other CAD tools is claimed.

Reports include mesh bounds, extents, volume, surface area, center of mass, component count, watertightness, winding, output SHA-256, per-node inclusive timing/cache hits, `warnings`, and with a `density`, `mass_g`. A cache hit at a parent skips evaluating its children, so those children do not appear in that run's trace. Export reports use mm, mm² and mm³. Stdout is one JSON object; `--report` writes a pretty-printed copy.

`validate` and `--dry-run` check syntax, schema, expressions and graph references only. A real build evaluates source files, geometry and checks. Selected exports are all computed and checked before models are written. Each file is replaced atomically, but a multi-file build is not a filesystem transaction: a disk error can leave some exports written. Existing models require `--force`; a report file is overwritten when explicitly requested.

## Mesh validity and scope

Normal exports require a nonempty, finite, watertight, consistently wound positive-volume mesh. STL and 3MF bytes are also reloaded and checked after rounding and vertex merging, before writing. Boolean inputs require solid meshes. These checks do not certify freedom from self intersections, overlapping shells, adequate wall thickness, printer tolerance, or suitability for engineering use. `combine` can retain overlapping shells; use `union` to fuse solids. Prefer overlapping volumes over point/edge contact.

Repair merges vertices, removes duplicate/degenerate faces, fixes normals, and fills simple triangle/quad holes supported by Trimesh. It does not reconstruct arbitrary missing surfaces. Simplification is approximate and can change dimensions or topology; normal export validation still applies. This tool edits triangles, rather than recovering parametric CAD features from STL.

CDCAD uses polygonal profiles and triangle meshes. Rounded rectangle corners, profile offsets, sweeps and implicit fields cover many rounded shapes. Exact STEP/BREP surfaces, fillets on solids and a sketch constraint solver are outside the current geometry model.

## Examples and tests

The editable project in `projects/arch-demo/` holds its source segments; `cdcad project build projects/arch-demo` generates its parts into the ignored `build/` folder. Structural recipes are `examples/structural-wall.json`, `examples/structural-bridge.json`, and `examples/structural-panel.json`. `examples/project-worker.py` demonstrates the persistent subprocess protocol.

- `enclosure.json`: parametric enclosure with mounting holes, raised walls and a boss.
- `svg-layers.json`, `dxf-layers.json`: drawing regions assigned different heights.
- `paths.json`: curved SVG paths with a hole and a buffered instruction path.
- `loft.json`: tapered adapter with a through bore. `revolve.json`: lathed knob.
- `heightfield.json`: raster relief with a solid base.
- `edit-mesh.json`: imported enclosure, side port, height cut and rotation.
- `function-vase.json`: a vase from a radius formula, without Python.
- `twisted-vase.json`: a lobed wall extruded with twist and taper.
- `spring.json`, `torus-knot.json`: sweeps along a helix and a closed knot.
- `gyroid.json`: a sheet-gyroid block from an implicit field.
- `surface-plot.json`: z = sin(r)/r as a printable plot.
- `nameplate.json`: engraved text, mounting holes, density and checks, exported as 3MF.
- `vase.py`, `ripple.py`, `design-sweep.py`, `wheel.py`: the same ideas from Python, the last one with the recipe builder.
- `samples/`: prebuilt STLs and build reports from the original examples.

```powershell
python -m pip install ".[test]"
python -m pytest tests -q
```

Tests check analytic volumes (extrusions, booleans, frustums, tori, partial revolves, sweeps, implicit spheres, graphs), the expression language and its sandbox, units, topology, SVG transforms/fill rules, DXF layers, 3MF round trips with units and components, heightfield orientation and precision, source-change cache invalidation, malformed inputs, output guards, checks, rendering, the MCP server, the Python builder, every catalog example and every example recipe. See [TEST_RESULTS.md](TEST_RESULTS.md) for the recorded runs.

The pytest configuration puts the source tree first on the import path, so both `pytest` and `python -m pytest` test the checkout even when another copy of CDCAD is installed in the environment. `python -m pip install .` installs a snapshot: after you change the source, reinstall it, or install once with `python -m pip install -e . --no-deps`, so that the `cdcad` command runs the code you are editing. `cdcad capabilities` reports the `package` directory that is running.

Geometry backends: [Trimesh](https://trimesh.org/trimesh.html), [Manifold](https://github.com/elalish/manifold) (booleans, cuts and level sets), [Shapely](https://shapely.readthedocs.io/en/stable/), [mapbox-earcut](https://pypi.org/project/mapbox-earcut/), [svgelements](https://pypi.org/project/svgelements/), [ezdxf](https://ezdxf.readthedocs.io/) and [fontTools](https://fonttools.readthedocs.io/).
