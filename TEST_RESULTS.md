# Cross-platform verification

- Windows x64 with CPython 3.13.14: 62 tests passed.
- Ubuntu 24.04 x64 under WSL, on the Linux filesystem with CPython 3.12.3: 62 tests passed. The installed CLI generated and inspected a solid box from outside the source tree.
- macOS Apple Silicon and Intel are configured in the CI matrix. Those runners have yet to execute the suite.

## 2026-11-14 audit run (branch `audit-improvements`, commit `e29cfe1`)

- Windows x64, CPython 3.13.14, pinned dependencies: 104 passed, 1 skipped (the POSIX file-mode test).
- Ubuntu 24.04 under WSL2 on the ext4 filesystem, CPython 3.12.3, dependencies from `requirements-tested.txt` installed into a scratch directory: 105 passed, 0 skipped. This run executes the POSIX file-mode test.
- A non-editable `pip install --no-deps .` into a clean Windows environment, then `cdcad --version`, `make box`, `inspect --require-solid --bed 150 150 150 --require-fit`, `capabilities` and `project init`, `set`, `history`, `restore` from outside the source tree: all behaved as expected.
- Not run: macOS, Python 3.11, and the dependency lower bounds declared in `pyproject.toml`.

## CDCAD 0.3.0 verification

Verified on Windows x64 with CPython 3.13.14 and the pinned dependencies.

- 60 tests passed, including 56 existing modeling and project cases.
- Built all 11 standalone example recipes, producing 12 valid solid exports.
- Reloaded all 17 archived STLs and verified that each is a closed, consistently wound, positive-volume mesh.
- Installed the package wheel and ran the command outside the source directory.
- Built three plate variants from computed hole centers and checked each mesh volume against its analytical polygonal volume.
- Ran the project worker through an initial build, a dimensional edit and an unchanged rebuild. Those passes rebuilt 5, 2 and 0 outputs.

## 0.2.0 historical verification

Full suite on Windows x64, CPython 3.13.14:

```text
python -m pytest tests -q
56 passed in 40.93s
```

The 24 additional test cases cover arch dimensions (including odd curve sampling counts), wall/panel cutouts in all planes, frames, oriented beams, drilling, 2D grids, 3D repeats, expression-derived integer counts, required cutout properties, content-cache reuse across renamed subgraphs, project creation/duplication/placement/fusion, unchanged rebuilds and source edits, layer selection, dynamic assembly membership, output tampering, revision conflicts, batch patch failure isolation, array-index assignment, attachment propagation/cycles, source-path protection, locks, removal and recovery, flattened recipe equivalence, CLI primitives, imported recipe asset paths, and persistent-worker error/stdin isolation.

The complete package contains **17 saved STL files**, including the original nine, three structural examples and five outputs from the editable arch project. Every file was reloaded from disk and checked as a watertight, consistently wound positive volume. See `samples/all-stl-verification.json`.

The arch demo's measured in-process build was 0.334 seconds for five outputs. An unchanged build took 0.173 seconds with **zero geometry evaluations and zero model output writes**. Changing only the arch span rebuilt the arch and fused assembly while retaining the other three outputs (0.235 seconds). See `samples/project-benchmark.json`. These are illustrative local measurements, exclude interpreter startup/imports, and are not performance guarantees. The persistent worker example was also run successfully through init, build, edit, rebuild and unchanged rebuild.

`projects/arch-demo/` contains the editable source and all generated parts. `structural-preview.png` is a direct triangle rendering of the saved models. Project source edits validate schema/parameters/dependencies before writing; builds validate geometry. The tool does not perform self-intersection certification, tolerance analysis, general CAD constraint solving or structural engineering analysis.

## Prior 0.1 verification

Environment: Windows x64, CPython 3.13.14. Exact installed dependency versions are recorded in `requirements-tested.txt`.

```text
python -m pytest tests -q
32 passed in 25.91s
```

The suite exercises analytic volumes for holes, extrusions, unions, differences, intersections, lofts, revolves and heightfields; physical SVG units, inherited transforms and fill rules; SVG circle closure precision; DXF layers, circles and chained LINE contours; capped cuts through both solid and hollow regions; mesh repair, simplification, sections, split components, unit conversion and reflection transforms; safe arithmetic expressions, schema errors, cyclic/missing references, stdin JSON and error exit codes; lazy target selection and in-memory memoization; disk cache hits, source/parameter invalidation and corrupt-cache recovery; overwrite/path guards and validation before multi-export writes; and serialized STL rejection of coincident edge contacts.

All eight included recipe workflows were built successfully, producing nine STL files. Every saved STL was independently reloaded from disk and checked as a watertight, consistently wound positive volume. Full results are in `samples/stl-roundtrip-verification.json`; build reports and SHA-256 output hashes are in `samples/*-report.json`.

The sample workflows completed in roughly 0.09–0.57 seconds each inside an already running Python interpreter in this environment. These measurements include the content-addressed cache's writes and STL serialization checks; they exclude Python startup and importing packages. They are example measurements, not performance guarantees or comparisons to other CAD tools. Reports retain the paths from the original verification workspace.

The preview is a simple orthographic triangle rendering of the actual exported meshes. It is illustrative and does not establish dimensional tolerances, self-intersection freedom, printability or engineering suitability. Geometry checks do not replace design review. See README.md for supported formats and limitations.
