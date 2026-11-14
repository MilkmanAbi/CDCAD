# Structural arch demo

Editable CDCAD project. All dimensions are millimeters.

- project.json: parameters, layers, placements, attachments and assemblies.
- segments/<layer>/<name>.json: local parametric geometry for each part.
- assets/: drawings and imported meshes. Paths inside segments are relative to the segment file.
- build/: managed STL/GLB outputs, one file per segment and assembly.
- .cdcad/: content cache, build reports, revision history and recovery journal.

Run `cdcad project build .`, then `cdcad project info .`.
Use `cdcad project set . /params/span 70` to resize an arch-demo project.
