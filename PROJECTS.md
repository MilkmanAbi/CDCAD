# CDCAD project modeling

CDCAD supports editable projects for dimensioned demo models, structural mockups, enclosures, frames and assemblies. Use direct CLI commands for a part, or keep a project with one JSON source document per segment.

## Start with a working structure

```powershell
cdcad project init my-demo --template arch-demo
cdcad project build my-demo
cdcad project info my-demo
```

The template contains a drilled base, an arch, a sloping rectangular brace and a panel with a slot. All four segments export individually; `demo` fuses them into one assembly. The arch attaches to the base's top with 0.3 mm overlap, so base changes propagate to its placement. The default base is 110 × 42 × 5 mm. This is a demo mockup, not a structural analysis.

```text
my-demo/
  project.json                  Parameters, logical layers, placements and assemblies
  segments/
    foundation/base.json        Base geometry
    structure/arch.json         Arch geometry
    structure/brace.json        Beam between endpoints
    details/panel.json          Wall with a slot cutout
  assets/                       Drawings and imported mesh sources
  build/
    segments/<layer>/<name>.stl  Individual segment outputs
    assemblies/demo.stl         Fused structure
  .cdcad/
    cache/                      Geometry cache
    history/                    Before-edit snapshots
    build-state.json            Incremental fingerprints and output hashes
    last-build.json             Latest report
```

Source JSON is the editable model. STL files are generated outputs; editing their triangles does not update source dimensions.

## Make structures directly

```powershell
cdcad make arch --span 60 --rise 30 --leg 22 --thickness 6 --depth 12 -o arch.stl
cdcad make wall --length 100 --height 60 --thickness 4 -o wall.stl
cdcad make panel --size 80 50 --thickness 3 --plane xy -o plate.stl
cdcad make frame --size 80 50 --border 5 --depth 8 --plane xz -o frame.stl
cdcad make beam --start 0 0 0 --end 35 0 25 --size 4 6 -o brace.stl
```

`make` also supports `box`, `cylinder` and `sphere`. Dimensions are millimeters. Use `--help` after the shape for exact flags. Add `--force` for an intentional overwrite. These commands use the same geometry engine as recipes and projects.

For cutouts, pass a JSON array through `--cutouts`, or write a segment recipe. This creates a 100 × 60 wall with an arch doorway and a rounded rectangular window:

```json
{
  "version": 1,
  "solids": {
    "part": {
      "op": "wall", "length": 100, "height": 60, "thickness": 4,
      "cutouts": [
        {"shape": "arch", "span": 24, "rise": 12, "leg": 24, "center": [-20, 18]},
        {"shape": "rectangle", "size": [20, 15], "radius": 2, "center": [25, 35]}
      ]
    }
  },
  "exports": [{"solid": "part", "path": "wall.stl"}]
}
```

The doorway is 36 mm tall, so center height 18 places its bottom at zero. Every cutout `center` describes its bounding-box center in the local 2D plane. Shapes: `rectangle` (`size`, optional corner `radius`), `circle` (`radius`), `slot` (`size`), and `arch` (`span`, optional `rise`, `leg`). A cutout can have local `rotate` degrees. Cutouts are combined in 2D and subtracted before one extrusion, avoiding a 3D boolean per hole.

## Create and manipulate segments

```powershell
cdcad project init fixture
cdcad project put fixture deck --layer foundation --spec '{"op":"box","size":[100,50,5]}'
cdcad project put fixture entry --layer structure --spec '{"op":"arch","span":50,"leg":20,"thickness":5,"depth":10}'
cdcad project attach fixture entry deck --anchor bottom --target-anchor top --offset 0 0 -0.2
cdcad project stitch fixture fixture-body --members deck entry --mode union
cdcad project build fixture
```

`put --spec` wraps one solid operation in a recipe named `part`. For profiles or a longer operation chain, use `--file segment.json`. A project segment file may omit `exports`; the manifest's `solid` selects its output (default `part`). Segments support all recipe operations, including SVG/DXF extrusion, imported mesh edits, sweeps, implicit fields and text. References inside a segment are local; cross-segment assemblies and attachments belong in `project.json`. A segment document's own `checks` and `density` are validated but not carried into project builds; put such assertions in a standalone recipe or check the built files with `cdcad inspect`.

```powershell
cdcad project put fixture wall --file wall.json --solid part --layer structure
cdcad project duplicate fixture entry second-entry --translate 0 20 0
cdcad project detach fixture second-entry
cdcad project place fixture second-entry --rotate 0 0 90 --translate 0 20 2.3
cdcad project stitch fixture all-parts --layers foundation structure --mode union
```

`duplicate` copies the recipe and placement/attachment settings. An attached duplicate stays attached to the same target until detached or reattached. Translation is absolute, not an incremental move. `place` updates only supplied components. Transforms apply scale → XYZ Euler rotation → translation, about the origin; attachment runs afterward. While attached, use its `offset` for final displacement.

Attachment aligns axis-aligned bounds after placement. Anchors are `center`, `left` (−X), `right` (+X), `front` (−Y), `back` (+Y), `bottom` (−Z), and `top` (+Z). Face anchors are centers of bounds faces, not points on curved surfaces. This does not infer CAD mating faces or solve general constraints. Target and source geometry are dependencies, so changes propagate. Cycles are rejected.

`stitch --mode union` performs solid fusion. Nonintersecting members remain disconnected; check component count. Prefer positive volume overlap. Point/edge contact can produce invalid topology. `combine` concatenates independent meshes without a boolean; it does not weld arbitrary open mesh boundaries. Assemblies may reference other assemblies. Part and layer names use letters, digits, `_` and `-`, beginning with a letter, and must be unique ignoring case. Segment-local node names may also begin with `_`.

Asset paths are relative to the segment file. Use forward slashes and match the filename case on disk so the same source works on case-sensitive filesystems. `put --file` rewrites imported recipe paths relative to its new location, preserving their original meaning, but does not copy external assets. For a portable project, copy drawings/meshes into `assets/` and reference `../../assets/drawing.svg` from a two-level segment folder. `put --file -` and `--spec` interpret paths relative to the destination segment folder.

## Edit dimensions and layers

```powershell
cdcad project set my-demo /params/span 72
cdcad project set my-demo /solids/part/depth 16 --segment arch
cdcad project set my-demo /solids/part/size/0 125 --segment base
cdcad project plan my-demo
cdcad project build my-demo
```

`set` uses a JSON pointer into `project.json` by default, or the selected source recipe with `--segment`. Values are JSON literals; strings beginning `=` can express arithmetic. Dictionary keys can be added. Existing array indices are replaced; `/-` appends. Parameter precedence: segment defaults → project params → per-segment entry params → build CLI overrides. A literal node dimension is not linked automatically to a same-named parameter; write `"=depth"` where parameter control is needed.

```powershell
cdcad project layer my-demo details --enabled false
cdcad project build my-demo --layer structure
cdcad project build my-demo --segment arch
cdcad project build my-demo --assembly demo --set span=68
cdcad project build my-demo --assembly demo --format glb
```

Selectors repeat and combine by union. A layer selector exports its enabled segments, not assemblies using that layer. An assembly selector evaluates dependencies but exports only that assembly. Layer-based assemblies collect enabled members dynamically; empty assemblies are excluded. An attachment to a disabled target is rejected. Disabled source documents are still checked for schema and local reference errors.

Default builds export every enabled segment and nonempty enabled assembly, replacing managed outputs as needed. `--out-dir` changes the build location; it cannot target the project root, segments, assets or metadata. Project formats: STL, 3MF, OBJ, PLY, OFF and GLB (`--format`). All coordinates, including GLB, are millimeters; viewers assuming meter-based glTF units need scale adjustment. 3MF records the unit, so slicers import it at the right size.

Old output files remain when a part is disabled, removed, renamed or built in another format. They are derived artifacts; the source manifest and latest report define active outputs. A partial build's latest report covers its selection. The builder never silently cleans a directory or deletes source files.

## Incremental builds

`project plan` compares content fingerprints and actual output SHA-256 values without meshing. `project build` skips geometry generation, serialization and output writes when both match. Changing the arch span rebuilds the arch and assembly; unrelated segments stay untouched. Deleted/tampered outputs regenerate. Changed asset bytes invalidate dependent geometry.

Geometry keys depend on resolved dimensions and child geometry rather than arbitrary reference names. Identical renamed subgraphs can reuse cached geometry. Only needed dependency chains are evaluated. A parameter unused by a model does not invalidate it. Script/dependency version changes invalidate geometry caches. `--rebuild` rewrites selected outputs while still allowing geometry-cache reuse.

For repeated commands, keep one subprocess:

```powershell
cdcad serve
```

Write one JSON request per stdin line; read one JSON response per stdout line:

```jsonl
{"id":"resize","args":["project","set","my-demo","/params/span","70"]}
{"id":"plan","args":["project","plan","my-demo"]}
{"id":"build","args":["project","build","my-demo","--assembly","demo"]}
```

Responses contain `id`, `ok`, `result` (or `error`/`type`), and command seconds. Errors do not stop the worker. A request can supply a string `stdin` for commands accepting `-`, isolating them from subsequent protocol lines. Close stdin to stop. Commands run sequentially; there is no network service or Python execution endpoint. This avoids repeated geometry-library imports. See `examples/project-worker.py` for a complete client. Batch related edits with one `patch` to validate the result once.

## Project units

Set units when you create a blank project, for example `cdcad project init inch-fixture --units in`. Segments inherit the project units when their recipe omits `units`. Exports use millimeters.

## Revisions, editing and recovery

```powershell
cdcad project info my-demo
cdcad project set my-demo /params/span 75 --if-revision <revision-from-info>
cdcad project patch my-demo --file changes.json
cdcad project history my-demo
cdcad project restore my-demo last
```

Patch operations are `add`, `replace`, `remove`, `test` against `{project: manifest, segments: source_documents}`:

```json
[
  {"op":"test","path":"/project/params/span","value":60},
  {"op":"replace","path":"/project/params/span","value":70},
  {"op":"replace","path":"/segments/arch/solids/part/depth","value":14}
]
```

Mutations check schema, expressions and references before writing; a build checks geometry. Each edit saves a full before-state snapshot in `.cdcad/history/`. A lock excludes simultaneous cooperating CLI commands. `--if-revision` rejects an edit based on a stale revision. Direct file edits do not participate in locking; finish them before invoking commands.

Each file is replaced atomically and multi-file mutations have a recovery journal; they are not one filesystem transaction. After an interrupted mutation use `project recover DIR --action rollback` or `--action commit`. A crashed process can leave `.cdcad/project.lock`; the lock error names the process that wrote it, so confirm that process is gone before removing the lock manually. Recovery keeps its journal until writes complete. A project created before the CDCAD rename that still has an unfinished `.agentcad/pending.json` is refused until you rename `.agentcad` to `.cdcad` while the project is idle.

`project history DIR [--limit N]` lists the saved snapshots, newest first (20 by default). Each entry has an `id`, the `action` that made it, the project revision before and after it, and `current` on the edit that produced the present state. `project restore DIR ENTRY` returns the project to the state saved before that entry, which undoes that edit and every later one; `last` means the newest entry. A restore is checked before anything is written, honours `--if-revision`, uses the recovery journal, and is added to history itself, so `restore last` twice returns to where you started. A segment source file that `remove` left on disk is never overwritten silently: if restoring would replace it with different content, move the file aside first. History is source snapshots, not a CAD undo solver; generated outputs are rebuilt by `project build`.

`project remove DIR NAME` rejects referenced parts. `--cascade` removes dependent assemblies and detaches dependents. Source files are retained. Changing a segment's logical `layer` does not move its source file; update its `path` in the same batch patch if desired. The new file is written and the old retained.

## Structural conventions

| Operation | Dimensions and origin |
|---|---|
| `arch` | `span` is clear opening width. `rise` defaults to span/2; `leg` defaults to 0. `thickness` adds to outer horizontal/vertical radii and sets post width. Overall width = span + 2×thickness; top Z = leg + rise + thickness. `depth` is along Y. `origin` offsets bottom-center. Elliptical arches have varying normal thickness. |
| `wall` | Length along X, height above Z=0, thickness centered on Y=0. `origin` offsets bottom-center. Cutouts use local X/Z coordinates. |
| `panel` | Local width/height `size` centered on the origin, thickness centered on its normal. `plane` defaults XY, optionally XZ/YZ. `center` places it in 3D. |
| `frame` | Centered rectangular frame; `size`, inset `border`, extrusion `depth`. Defaults to XZ. Border must leave a positive opening. |
| `beam` | Rectangular `size` about the centerline from `start` to `end`. `roll` rotates its cross section in degrees. End faces are normal to the line. |
| `drill` | Existing `input`, 3D `positions`, `radius`, `depth`, `axis` X/Y/Z (default Z). Positions are cylinder centers; extend cutters through the part. |
| `repeat` | Existing `input`, integer XYZ `counts`, XYZ `step`, `fuse` (true). At most 10,000 instances. |
| Profile `grid` | Existing 2D `input`, XY `counts`, XY `step`. Unions repeated profiles before extrusion. |

YZ panels map width→Y, height→Z, normal→+X. XZ panels map width→X, height→Z, normal→−Y. Curves are sampled. `schema` documents all recipe operations; `project schema` documents the manifest.

## Flatten for ordinary recipe builds

```powershell
cdcad project compile my-demo --set span=72 -o compiled.json
cdcad build compiled.json --out-dir exported
cdcad render compiled.json --solid @demo -o demo.png
```

The result is a regular namespaced recipe with placement, attachment and assembly graph nodes: segment `arch` becomes solid `@arch`, and its local nodes are `arch::name`. External asset paths in compiled recipes are absolute, and field expressions carry their parameter values. `compile` refuses to replace an existing file unless you pass `--force`. Existing mesh inspection, conversion, rendering and editing commands remain available.
