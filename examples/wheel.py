"""Spoked wheels built with the Python recipe builder: one design, several spoke counts.

Each wheel is a rim, a hub and a polar array of spokes, unioned in 2D and extruded once.
The recipe carries checks (it must be one piece and fit a 220 x 220 x 250 mm bed) and a
density, so every report includes the mass. A preview picture is drawn for each wheel.

    python examples/wheel.py out
"""
import argparse
from pathlib import Path

from cdcad import Recipe, fx


def wheel(spokes):
    r = Recipe(params={"radius": 40, "rim": 4, "hub": 8, "bore": 3, "spoke": 3.5, "thickness": 6},
               density=1.24, description=f"Wheel with {spokes} spokes")
    p = r.p
    rim = r.circle(p.radius) - r.circle(p.radius - p.rim)
    hub = r.circle(p.hub)
    spoke = r.rectangle([p.radius - p.rim/2, p.spoke], center=[(p.radius - p.rim/2)/2, 0])
    arms = spoke.array(spokes, rotate=360/spokes)
    bore = r.circle(p.bore)
    body = ((rim | hub | arms) - bore).extrude(p.thickness)
    # A raised boss around the bore, at least 2 mm proud and half the thickness again otherwise.
    boss = (r.circle(p.hub*0.75) - bore).extrude(p.thickness + fx.max(2, p.thickness/2))
    part = (body | boss).rename("wheel")
    r.export(part, f"wheel-{spokes}.stl")
    r.check(part, components={"approx": 1}, fits=[220, 220, 250])
    return r, part


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    for spokes in (3, 5, 8):
        r, part = wheel(spokes)
        report = r.build(args.out_dir, force=True, cache_dir=args.out_dir/".cad-cache")
        r.render(part, args.out_dir/f"wheel-{spokes}.png", view="iso", size=400)
        export, = report["exports"]
        print(f"{spokes} spokes: {export['volume']:.0f} mm^3, {export['mass_g']:.1f} g, solid={export['is_volume']}")


if __name__ == "__main__":
    main()
