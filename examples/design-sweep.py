"""Build a height sweep of perforated plates and check each computed volume."""
import argparse
import math
from pathlib import Path

from cdcad import build_recipe, resolve_recipe

WIDTH = 72
DEPTH = 48
RADIUS = 2.5
PITCH_RADIUS = 14
HOLES = 6
SEGMENTS = 96


def model():
    profiles = {"plate": {"op": "rectangle", "size": [WIDTH, DEPTH]}}
    for index in range(HOLES):
        angle = math.tau * index / HOLES
        profiles[f"hole_{index}"] = {
            "op": "circle",
            "radius": RADIUS,
            "segments": SEGMENTS,
            "center": [PITCH_RADIUS * math.cos(angle), PITCH_RADIUS * math.sin(angle)],
        }
    profiles["perforated"] = {"op": "difference", "inputs": list(profiles)}
    return {
        "version": 1,
        "units": "mm",
        "params": {"thickness": 4},
        "profiles": profiles,
        "solids": {"part": {"op": "extrude", "profile": "perforated", "height": "=thickness"}},
        "exports": [{"solid": "part", "path": "plate.stl"}],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--force", action="store_true", help="Replace existing plates")
    args = parser.parse_args()

    raw = model()
    # Each circular hole is approximated by a regular polygon.
    hole_area = SEGMENTS * RADIUS**2 * math.sin(math.tau / SEGMENTS) / 2
    for thickness in (3, 4, 6):
        recipe = resolve_recipe(raw, {"thickness": thickness})
        report = build_recipe(recipe, args.out_dir, args.out_dir / f"{thickness}mm",
                              force=args.force, cache_dir=args.out_dir / ".cad-cache")
        part, = report["exports"]
        expected = (WIDTH * DEPTH - HOLES * hole_area) * thickness
        if not math.isclose(part["volume"], expected, rel_tol=1e-9):
            raise RuntimeError(f"Unexpected volume: {part['volume']} mm³ (expected {expected})")
        print(f"{thickness} mm: {part['volume']:.2f} mm^3, {part['path']}")


if __name__ == "__main__":
    main()
