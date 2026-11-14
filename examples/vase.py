"""A hollow vase whose radius is a formula of height, lathed from a polygon."""
import argparse
import math
from pathlib import Path

from cdcad import build_recipe, resolve_recipe

HEIGHT = 90   # mm
FLOOR = 3     # mm, thickness of the base (a multiple of STEP)
WALL = 2      # mm
STEP = 1.5    # mm between points on the outline


def radius(z):
    return 18 + 6 * math.sin(z / 12) + 4 * math.sin(z / 5)


def outline():
    """Wall cross-section as (radius, height) points: up the outside, down the inside, across the floor."""
    heights = [i * STEP for i in range(round(HEIGHT / STEP) + 1)]
    outside = [[radius(z), z] for z in heights]
    inside = [[radius(z) - WALL, z] for z in reversed(heights) if z >= FLOOR]
    return [[0, 0], *outside, *inside, [0, FLOOR]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--force", action="store_true", help="Replace an existing vase.stl")
    args = parser.parse_args()

    recipe = resolve_recipe({
        "version": 1,
        "profiles": {"wall": {"op": "polygon", "points": outline()}},
        "solids": {"vase": {"op": "revolve", "profile": "wall", "segments": 96}},
        "exports": [{"solid": "vase", "path": "vase.stl"}],
    })
    report = build_recipe(recipe, args.out_dir, args.out_dir, force=args.force)
    part, = report["exports"]
    height = part["extents"][2]
    print(f"{part['path']}: {part['volume'] / 1000:.1f} cm^3, {height:.1f} mm tall, solid={part['is_volume']}")


if __name__ == "__main__":
    main()
