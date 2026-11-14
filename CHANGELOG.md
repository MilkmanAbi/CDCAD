# Unreleased

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
