"""A height map computed with numpy and built as a relief (heightfield reads .npy arrays in [0, 1])."""
import argparse
from pathlib import Path

import numpy as np

from cdcad import build_recipe, resolve_recipe

SAMPLES = 200   # grid points per side


def height_map():
    x = np.linspace(-1, 1, SAMPLES)
    r = np.hypot(*np.meshgrid(x, x))
    return (0.5 + 0.5 * np.cos(16 * r)) * np.clip(1 - r, 0, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--force", action="store_true", help="Replace an existing ripple.stl")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    np.save(args.out_dir / "ripple.npy", height_map())
    recipe = resolve_recipe({
        "version": 1,
        "solids": {"ripple": {"op": "heightfield", "path": "ripple.npy", "size": [80, 80], "height": 6, "base": 2}},
        "exports": [{"solid": "ripple", "path": "ripple.stl"}],
    })
    report = build_recipe(recipe, args.out_dir, args.out_dir, force=args.force)
    part, = report["exports"]
    print(f"{part['path']}: {part['faces']} triangles, {part['extents'][2]:.1f} mm tall, solid={part['is_volume']}")


if __name__ == "__main__":
    main()
