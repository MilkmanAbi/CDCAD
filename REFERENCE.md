# CDCAD reference

Command details, the operation tables, and the rules for units, coordinates and file formats. The short version is in [README.md](README.md). Project folders are covered in [PROJECTS.md](PROJECTS.md) and scripting in [SCRIPTING.md](SCRIPTING.md).

## First model

```powershell
cdcad make box --size 40 25 6 -o block.stl
cdcad --pretty inspect block.stl --require-solid
cdcad make --help
cdcad make panel --help
```

Dimensions default to millimeters. Pass `--units cm`, `--units m`, or `--units in` to model in other units. Exported coordinates use millimeters.

Check a part against a printer's build volume with `cdcad inspect block.stl --bed 150 150 150`. The report gains `bed_fit`: whether the part fits as modeled, whether it fits with the axes reassigned, and the clearance per axis in mm. `--require-fit` turns a part that fits in no axis-aligned orientation into an error, so a script can gate on it.

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

Use `--force` to replace an existing output. Normal command results are compact JSON on stdout. Put `--pretty` before the command for indented JSON. Application errors are JSON on stderr with exit code 2. Argument usage errors are standard argparse text.

## Assign different heights to drawing regions

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
  "params": {"w": 60, "d": 40, "h": 6},
  "profiles": {
    "outline": {"op": "rectangle", "size": ["=w", "=d"], "radius": 3},
    "hole": {"op": "circle", "radius": 4, "center": [15, 0]},
    "cut_outline": {"op": "difference", "inputs": ["outline", "hole"]}
  },
  "solids": {
    "part": {"op": "extrude", "profile": "cut_outline", "height": "=h"}
  },
  "exports": [{"solid": "part", "path": "part.stl"}]
}
```

```powershell
cdcad validate recipe.json
cdcad --pretty resolve recipe.json --set h=10
cdcad build recipe.json --out-dir models --set h=10
Get-Content recipe.json -Raw | cdcad build - --out-dir models --force
```

Values beginning `=` are arithmetic expressions with named parameters. Parameters can depend on one another; cycles and missing names are rejected. Supported functions are `sin`, `cos`, `tan`, `sqrt`, `ceil`, `floor`, `radians`, `degrees`, `min`, `max`, `abs`, `round`; constants are `pi` and `tau`. Angles in trig functions are radians. The expression parser accepts arithmetic only, with no Python evaluation, attributes, imports or subscripting. Overrides apply to declared parameters and accept JSON numbers or strings beginning `=`.

Input and default output paths are relative to the recipe file. For stdin they are relative to the current directory. `--base-dir` changes the input/default-output root. `--out-dir` sets the export root and rejects escaping paths. `--target NAME` builds only exports of that solid and its dependencies; repeat the flag for multiple targets. Schema and graph checks still cover all nodes.

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

For a reproducible multi-step STL edit, see `examples/edit-mesh.json`, which imports the included `samples/enclosure.stl`. Rebuild that source with `cdcad build examples/enclosure.json --out-dir samples --force` if you change its parameters. Alternatively change the import path to the location of your own STL.

You can convert an existing mesh by changing the output extension, for example `cdcad edit part.stl -o part.obj`. Mesh inputs support STL, OBJ, PLY, OFF, GLB and 3MF. Outputs support STL, OBJ, PLY, OFF and GLB. Material, texture and scene hierarchy preservation is outside the scope of this geometry tool. OBJ files should contain their geometry inline; do not rely on material data.

## Operation reference

Find structural operations and project commands in [PROJECTS.md](PROJECTS.md). The tables below cover profile and mesh operations. The CLI schema includes the full operation set.

Run `cdcad schema` for the exact, machine-readable schema. `recipe.schema.json` is a saved copy. The schema describes **resolved** recipes; expression strings are resolved by the CLI before validation. Unknown properties are rejected to catch misspellings.

| 2D operation | Required properties | Optional properties |
|---|---|---|
| `rectangle` | `size: [width,depth]` | `center`, rounded corner `radius` |
| `circle` | `radius` | `center`, `segments` (96) |
| `ellipse` | `radii: [rx,ry]` | `center`, `segments` (96) |
| `polygon` | `points: [[x,y],...]` | `holes: [point_ring,...]` |
| `stroke` | `points`, `width` | `closed`, `cap`, `join` |
| `path` | SVG path data in `d` | `width` for strokes, `step` (0.5), `fill_rule` |
| `svg` | `path` | shape `ids`, `step` (0.5 SVG px), `flip_y` (true), `fill_rule` override |
| `dxf` | `path` | `layers`, curve `tolerance` (0.05) |
| `union`, `difference`, `intersection` | `inputs: [profile_name,...]` | — |
| `offset` | `input`, signed `distance` | `join` |
| `transform` | `input` | `scale: [sx,sy]`, `rotate` degrees, `translate: [x,y]` |

| 3D operation | Required properties | Optional properties |
|---|---|---|
| `extrude` | `profile`, `height` | starting `z` (0) |
| `loft` | `sections: [{profile,z},...]` | boundary `samples` (96) |
| `revolve` | `profile` | `segments` (96), full 360° revolution |
| `box` | `size: [x,y,z]` | — |
| `cylinder` | `radius`, `height` | `segments` (96) |
| `sphere` | `radius` | `subdivisions` (3) |
| `import` | `path` | source `units`, `repair` (false) |
| `heightfield` | image/NPY `path`, XY `size`, `height` | `base` (1), `max_samples` (256), `invert` |
| `union`, `difference`, `intersection` | `inputs: [solid_name,...]` | — |
| `combine` | `inputs` | concatenation without fusion |
| `transform` | `input` | 3D `scale`, `rotate`, `translate`; OR affine 4×4 `matrix` |
| `array` | `input`, `count`, XYZ `step` | `fuse` (true) |
| `repair` | `input` | `fill_holes` (true) |
| `hull` | `input` | — |
| `simplify` | `input`, target `faces` | — |
| `cut` | `input`, plane `origin`, plane `normal` | `cap` (true) |

Each node also requires `op`. References point to nodes in the same namespace, except a solid's `profile` and loft sections which reference `profiles`. Forward references are allowed; cycles are rejected. Difference subtracts all later inputs from the first. Intersection keeps the overlap of all inputs.

## Coordinates, units and source formats

- Recipe dimensions use `mm` (default), `cm`, `m`, or `in`. Exported coordinates are always **millimeters**. STL has no embedded unit metadata. Mesh inspection and quick mesh editing assume their input coordinates are already mm; use explicit scale otherwise.
- Extrusions start at Z=0 by default and grow upward. Rectangles, circles and ellipses are centered on their `center` (default `[0,0]`). Primitive boxes, cylinders and spheres are centered on the origin. This differs deliberately from extrusion's Z origin.
- Coordinates are right-handed, and extrusion grows along +Z. Transforms use the origin as pivot and apply scale, then rotation, then translation. Rotation is fixed-axis XYZ Euler in degrees; a positive Z rotation turns +X toward +Y. Matrices act on homogeneous column vectors. A `cut` keeps the side its `normal` points toward.
- SVG physical dimensions and `viewBox` are respected at 96 PPI; a unitless pixel is 25.4/96 mm. SVG transforms are applied. The Y axis is flipped by default for right-handed CAD coordinates, which can produce negative Y positions. Use a profile transform to move it. Import precision is limited by SVG parsing and curve sampling.
- Only filled SVG geometric shapes are imported. Stroke-only SVG, text, CSS styling, clipping, masks and filter semantics are not modeled. Convert text/strokes to filled outlines first. Selection uses shape IDs, not group IDs. SVG elements are unioned; holes must be contours inside the same path with an appropriate fill rule. Literal `path` nodes can stroke open paths with `width`.
- DXF supports LINE, ARC, CIRCLE, ELLIPSE, SPLINE, LWPOLYLINE and planar POLYLINE contours. It reads modelspace at Z=0; nested closed contours use even-odd fill. Open/dangling segments and unsupported selected entities are rejected. Select clean layers or explode blocks/text first. Coordinates use recipe units; `$INSUNITS` is not automatically applied. Endpoint gaps are not automatically snapped closed. DXF bulges/curves are flattened with `tolerance`.
- Heightfields accept grayscale images or a 2D `.npy` array of normalized values in `[0,1]`. Black is `base`; white is `base+height`. Base must be positive. The result includes a flat bottom and sidewalls. Images may be downsampled to `max_samples` along the longer dimension. `relief.png` and `heightfield.json` are included.
- Loft takes one hole-free contour per strictly increasing Z section. It resamples boundaries and connects them with triangles; sharp corners can change and extreme changes can self-intersect. Matching contour shapes work best. Subtract holes afterward. Revolve takes a hole-free 2D polygon with X≥0, with X as radius and Y as height.

## Build planning and reports

```powershell
cdcad capabilities
cdcad build recipe.json --out-dir models --cache-dir .cad-cache --report models/build.json
cdcad build recipe.json --out-dir models --cache-dir .cad-cache --force --set h=8
cdcad build recipe.json --out-dir models --dry-run
```

The engine builds only exported dependency chains, memoizes shared nodes, and performs planar holes/offsets before triangulation when you model them that way. Native Manifold performs mesh booleans. Curves expose sampling controls rather than forcing huge meshes. Heightfields use indexed grid meshes.

The optional disk cache stores profiles as WKB and meshes as compressed NumPy arrays with pickle disabled. Keys include the node, recursive dependencies, source-file bytes, recipe units, geometry code and relevant dependency versions. Changed dimensions or source contents invalidate dependent nodes. Corrupt cache entries are recomputed. Cache read/write and hashing have overhead; this is most useful for repeated builds with expensive shared subgraphs. No performance comparison to other CAD tools is claimed.

Reports include mesh bounds, extents, volume, surface area, component count, watertightness, winding, output SHA-256, and per-node inclusive timing/cache hits. A cache hit at a parent skips evaluating its children, so those children do not appear in that run's trace. Export reports use mm, mm² and mm³. Stdout is one JSON object; `--report` writes a pretty-printed copy.

`validate` and `--dry-run` check syntax, schema and graph references only. A real build evaluates source files and geometry. Selected exports are all computed and checked before models are written. Each file is replaced atomically, but a multi-file build is not a filesystem transaction: a disk error can leave some exports written. Existing models require `--force`; a report file is overwritten when explicitly requested.

## Mesh validity and scope

Normal exports require a nonempty, finite, watertight, consistently wound positive-volume mesh. STL bytes are also reloaded and checked after binary float32 rounding and vertex merging, before writing. Boolean inputs require solid meshes. These checks do not certify freedom from self intersections, overlapping shells, adequate wall thickness, printer tolerance, or suitability for engineering use. `combine` can retain overlapping shells; use `union` to fuse solids. Prefer overlapping volumes over point/edge contact.

Repair merges vertices, removes duplicate/degenerate faces, fixes normals, and fills simple triangle/quad holes supported by Trimesh. It does not reconstruct arbitrary missing surfaces. Simplification is approximate and can change dimensions or topology; normal export validation still applies. This tool edits triangles, rather than recovering parametric CAD features from STL.

CDCAD uses polygonal profiles and triangle meshes. Rounded rectangle corners and profile buffers cover common 2D rounding operations. Exact STEP/BREP surfaces, general sweeps and a sketch constraint solver are outside the current geometry model.

## Examples and tests

The editable project in `projects/arch-demo/` includes its source segments and generated assembly. Additional structural recipes are `examples/structural-wall.json`, `examples/structural-bridge.json`, and `examples/structural-panel.json`. `examples/project-worker.py` demonstrates the persistent subprocess protocol.

- `enclosure.json`: parametric enclosure with mounting holes, raised walls and a boss.
- `svg-layers.json`: selected SVG regions assigned different heights.
- `dxf-layers.json`: DXF contour layers assigned different heights.
- `paths.json`: curved SVG paths with a hole and a buffered instruction path.
- `loft.json`: tapered adapter with a through bore.
- `revolve.json`: lathed knob.
- `heightfield.json`: raster relief with a solid base.
- `edit-mesh.json`: imported enclosure, side port, height cut and rotation.
- `samples/`: prebuilt STLs and build reports from these examples.

```powershell
python -m pip install ".[test]"
python -m pytest tests -q
```

Tests check analytic volumes, units, topology, booleans, loft/revolve, SVG transforms/fill rules, DXF layers/line chains, source-change cache invalidation, malformed inputs, output guards, stdin JSON, and CLI editing, sections and splitting. See [TEST_RESULTS.md](TEST_RESULTS.md) for the recorded run.

The pytest configuration puts the source tree first on the import path, so both `pytest` and `python -m pytest` test the checkout even when another copy of CDCAD is installed in the environment. `python -m pip install .` installs a snapshot: after you change the source, reinstall it, or install once with `python -m pip install -e . --no-deps`, so that the `cdcad` command runs the code you are editing. `cdcad capabilities` reports the `package` directory that is running.

Geometry backends: [Trimesh](https://trimesh.org/trimesh.html), [Manifold booleans](https://trimesh.org/trimesh.boolean.html), [Shapely](https://shapely.readthedocs.io/en/stable/), [svgelements](https://pypi.org/project/svgelements/), and [ezdxf](https://ezdxf.readthedocs.io/).
