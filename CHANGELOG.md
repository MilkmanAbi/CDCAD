# Unreleased

New:

- STEP export: `.step` and `.stp` files hold the mesh as planar faces (coplanar triangles are merged, so a box is six faces and holes are inner loops). Output is deterministic and reads in CAD programs.
- `fillet` and `chamfer` on solids: any closed solid is rounded or bevelled by Minkowski morphology in Manifold. On convex solids every edge is done; on non-convex solids `edges: concave` fills the seams and rounding convex edges is rejected with a hint (fillet the simple parts before the union).
- `fillet` and `chamfer` on profiles: exact corner radii and bevels for 2D outlines.
- `extrude` takes `top` and `bottom` edge fillets or chamfers (exact, holes included); they cannot be combined with twist or scale.
- `spline` profile: a smooth closed outline or a drawn curve through points.
- `loft` takes `smooth`, which curves the sides through all sections with a spline.
- `sweep` takes `smooth`, which runs the profile along a spline through the path points.
- `smooth` solid: a smooth, subdivided surface (Manifold's smooth constructor). Edges sharper than `sharp_angle` stay creased; a faceted sphere becomes a sphere.
- `REFERENCE.md` tables and `recipe.schema.json` list the new operations.
- Hybrid B-rep kernel (optional, `pip install cdcad[brep]`, OpenCascade via cadquery-ocp): primitives, extrusions of plain profiles, booleans, uniform scale/rotate/translate, and all-edge fillets and chamfers are built as exact B-rep solids. Their STEP export keeps true curved faces, and fillets take milliseconds. Tessellation sets the mesh that checks, STL and renders use. Solids that need mesh-only operations, and concave/convex edge selection, stay on the mesh path, which also runs when the kernel is absent. `capabilities` reports `brep_kernel`.

# 0.5.0

Changes that can alter existing results:

- Image heightfields were mirrored top to bottom: the picture's top row landed at Y=0. Pictures now keep the orientation you see on screen (top row at the high-Y edge), like SVG import. `.npy` arrays keep row 0 at Y=0 (numpy's meshgrid convention). The new `flip_y` overrides either. `samples/relief.stl` is rebuilt.
- 16-bit PNG heightmaps were clipped to two levels; they now keep their precision, and 32-bit float images are read as values in [0, 1].
- A filled SVG path or polyline without a closing Z is filled as SVG viewers fill it, instead of being rejected; filled subpaths with no area are skipped. Literal `path` data still needs Z or a stroke width.
- DXF layer selection ignores case, as DXF does.
- `text`, `d`, SVG `ids`, DXF `layers` and notes are never evaluated as expressions, even when they start with `=`.
- `project compile` refuses to replace an existing file unless you pass `--force`.

Fixes:

- 3MF input failed with "No module named 'lxml'". 3MF is now read and written with the standard library: units, components, build-item transforms and production-extension parts are read; output is a millimetre 3MF that slicers import at the right size.
- `split` of a hollow solid failed; a void now stays with the body that contains it.
- `project history` could mark two entries as current after a restore; only the newest one is.
- A missing segment file, a corrupt `build-state.json` (now a warning and a full rebuild), an unreadable or malformed JSON file, and `Engine.node_key` without a cache directory raised raw Python errors; they are `CADError`s with a clear message.
- A corner radius computed as exactly half a side could fail by a rounding error.
- Heightfield faces are built with numpy instead of Python loops: a 512 x 512 field took 5.1 s and now takes 3.0 s on the development machine; most of the rest is the closed-solid check every mesh gets.
- The disk cache key covers every module of the package and the bundled font, so any code change invalidates old cache entries.
- Package metadata declares the licence (GPL-3.0-only, with the DejaVu font licence) and `fonttools`.

New:

- Maths-driven operations: `function` (area under or beside a formula), `curve` (closed parametric outline), `graph` (solid under z = f(x, y)), `implicit` (solid where f(x, y, z) < 0, meshed with Manifold's level set; gyroids, blobs), `sweep` (a profile along a 3D path or parametric curve, with rotation-minimising or up-vector frames, twist, scale and closed loops).
- `extrude` gains `twist`, `scale` and `slices`; `revolve` gains `angle` for partial turns.
- New shapes: `regular_polygon` (radius or across-flats), `text` (bundled DejaVu Sans subset, or any TrueType/OpenType file), `cone` (and frustum), `torus`, `polyhedron` (faces, or the hull of points). New modifiers: `mirror`, profile `array` and `hull`, `rotate` on solid `array` (polar patterns), `hull` of several inputs.
- Expressions: `atan2`, `asin`, `acos`, `atan`, `sinh`, `cosh`, `tanh`, `exp`, `log`, `log10`, `log2`, `cbrt`, `hypot`, `pow`, `sign`, `clamp`, `lerp`, `smoothstep`, `smin`, `smax`, the constant `e`, `//`, comparisons, `a if c else b` and `and`/`or`/`not`. Parameters resolve in dependency order with clear messages for unknown names and cycles. `^` is rejected with a hint to use `**`.
- Field expressions (formulas of x, y, z or t) may use parameters, which are written into them when the recipe is resolved.
- Recipe `checks` (volume, area, mass, components, size, is_volume, bed fit) stop a build before anything is written; recipe `density` adds `mass_g` to reports; reports include the centre of mass. Top-level `description` and per-node `note` document a recipe.
- `cdcad render` draws PNG previews of mesh files or recipe solids (iso, front, top, right, or one view) without OpenGL.
- `cdcad describe` and `cdcad guide` document every operation and property from the code, with examples that the tests build. The JSON Schema carries descriptions and defaults.
- Errors carry a `hint` ("Did you mean 'cylinder'?", "circle is a profile operation", "write powers with **"); `validate` and `build` report `warnings` for unused parameters and unreachable nodes.
- `cdcad.Recipe`: a Python builder with operators (`a - b`), parameter arithmetic and `build`/`mesh`/`render`/`save`.
- `cdcad mcp`: a Model Context Protocol tool server, so MCP clients can validate, build and see renders of recipes.
- `inspect --density`; `make cone` and `make torus`; 3MF in `project build --format`.
- Examples: `spring.json`, `torus-knot.json`, `gyroid.json`, `function-vase.json`, `twisted-vase.json`, `surface-plot.json`, `nameplate.json` and `wheel.py`; `gallery.png` is rendered from them. `AGENTS.md` describes the workflow for coding agents.

# 0.4.0

First release on PyPI.

- The README is now a short introduction. The operation tables, flags and format rules moved to `REFERENCE.md`.
- Added `examples/vase.py` (a vase lathed from a formula) and `examples/ripple.py` (a numpy height map built as a relief), with tests that run them.
- Added `publish.yml`, which builds the package and uploads it to PyPI from a GitHub release once PyPI trusts the workflow, and added project URLs and classifiers to the package metadata.
- Added `project history` and `project restore` to read back the before-edit snapshots every edit already saved. A restore is validated, journaled and itself recorded, so it can be undone.
- Added `inspect --bed X Y Z` and `--require-fit` to check a part against a build volume, as modeled and in any axis-aligned orientation.
- Fixed `section` writing a vertically mirrored SVG; the drawing now matches the part and survives an SVG import round trip.
- Fixed exported models and project files being created owner-only (`0600`) on Linux and macOS. Writes now follow the umask and are synced before they replace the target.
- Unknown segment names, malformed patch operations, invalid `--spec` JSON and arithmetic errors in expressions (`=1/0`, `=sqrt(-1)`) now raise `CADError` instead of `KeyError`, `ZeroDivisionError` and similar.
- The project lock error names the holding process, and an unfinished write left in `.agentcad` is reported with the rename instruction instead of being ignored.
- `capabilities` reports the running `package` directory, and pytest imports the source tree even when another copy is installed.
- Added macOS, Linux, and Windows installation and testing instructions.
- Standardized UTF-8 JSON pipes and made relative paths in saved projects portable across operating systems.
- Added CI for Linux x64, macOS Apple Silicon and Intel, and Windows x64 on Python 3.11 and 3.13.

# 0.3.0

- Rebranded the command, package, documentation, and bundled project metadata to CDCAD.
- Separated the CLI, recipe validation, project storage and compilation, geometry evaluation, and export code.
- Added `resolve`, `--pretty`, primitive/project units, DXF tolerance, project compile overrides, and a Python API with a math-driven example.
- Added source-overwrite protection, strict parameter override names, project pointer validation, and asset rebasing when duplicating segments.
- Kept version-1 recipe and project source formats. You can rename the previous metadata directory to `.cdcad` while the project is idle to retain history and recovery state.

# 0.2.0

- Added editable project folders with separate segment recipes, logical layers, shared parameters and named assemblies.
- Added source manipulation commands: put, duplicate, place, attach/detach, layer, set, patch, stitch and remove.
- Added arch/opening, wall, panel, rectangular frame, oriented beam, drill pattern, slot, profile grid, 3D repeat and bounds alignment operations.
- Added `make` CLI shortcuts with shape-specific dimension flags.
- Added output-level incremental builds, selective segment/layer/assembly exports and build plans. Unchanged outputs skip geometry, serialization and writes, with file-hash verification.
- Made geometry content keys independent of local reference names, allowing identical duplicated subgraphs to share cached geometry.
- Added persistent JSON-lines workers for repeated commands without repeated Python startup, with per-command error/stdin isolation.
- Added discriminated operation validation to avoid testing every schema alternative for each node, and normalized expression-derived integer counts.
- Added project revisions, history snapshots, cooperative locks and recovery journals for source mutations.
- Preserved version-1 recipe and existing mesh-editing workflows.
- Expanded verification from 32 to 56 test cases and shipped an editable structural project plus 17 verified model files.
