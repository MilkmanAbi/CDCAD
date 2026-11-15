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
