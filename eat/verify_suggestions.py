"""
Verification harness for the design-suggestions engine (build step 11).

The thing worth verifying here isn't that the code runs, it's that the
suggestions are ones a competent extrusion engineer would actually make
-- and, just as importantly, that a well-designed profile comes back
quiet. So this runs the engine against five profiles whose right answer
is known by inspection:

  L-angle, no fillet      -> exactly the unfilleted inner corner, nothing else
  solid 50x100 bar        -> exactly the wasted core, with hand-checkable figures
  filleted 60x40 tube     -> silent (uniform walls, radiused corners, hollow)
  20x40 KJN T-slot        -> silent (real commercial part, in production)
  deliberately bad box    -> the 0.8mm wall, the square voids, the lopsided material

The filleted tube and the KJN matter as much as the failing cases: the
tube's 3mm inner radii must NOT read as sharp corners (they arrive as
arc polylines, which is exactly what a filleted corner looks like coming
out of a DXF), and the KJN has 28 sharp internal corners that are all
functional slot and keyway features, which the engine is expected to
leave alone.

Also cross-checks `polygon_moments`, the exact shoelace area/centroid/
inertia used throughout the engine, against eat.section's meshed FE
results -- the suggestions lean on those numbers, so they need to agree
with the values the rest of the tool reports.

Run with: python -m eat.verify_suggestions
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from eat.dxf_io import import_polygon_from_bytes
from eat.section import Material, analyze_section
from eat.suggestions import generate_suggestions, polygon_moments

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KJN_FIXTURE = PROJECT_ROOT / "eat" / "fixtures" / "20X40_KJN992891.dxf"

MATERIAL = Material(name="Test Steel", E=200_000, nu=0.3, density=7850)

L_ANGLE = [(0, 0), (50, 0), (50, 10), (10, 10), (10, 60), (0, 60)]
RECTANGLE = [(0, 0), (50, 0), (50, 100), (0, 100)]
# 60x40 box, two cells: 4mm walls everywhere except an 0.8mm right-hand wall,
# and square-cornered voids instead of radiused ones.
BAD_BOX = [(0, 0), (60, 0), (60, 40), (0, 40)]
BAD_BOX_HOLES = [
    [(4, 4), (28, 4), (28, 36), (4, 36)],
    [(32, 4), (59.2, 4), (59.2, 36), (32, 36)],
]


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _rel_close(actual: float, expected: float, tol: float = 1e-6) -> bool:
    if expected == 0:
        return abs(actual) < 1e-9
    return abs(actual - expected) / abs(expected) <= tol


def rounded_rect(x0, y0, x1, y1, radius, ccw=True, segments=16):
    """A rectangle with radiused corners, as the arc-approximated polyline a
    filleted profile turns into on import."""
    points = []
    for cx, cy, start in (
        (x1 - radius, y0 + radius, -90),
        (x1 - radius, y1 - radius, 0),
        (x0 + radius, y1 - radius, 90),
        (x0 + radius, y0 + radius, 180),
    ):
        for k in range(segments + 1):
            angle = math.radians(start + 90 * k / segments)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points if ccw else points[::-1]


def _kjn_profile():
    result = import_polygon_from_bytes(KJN_FIXTURE.read_bytes(), source_label=KJN_FIXTURE.name)
    return result.vertices, result.holes


def run_moment_checks() -> list[Check]:
    """The engine's exact polygon moments have to agree with the meshed FE
    section properties the rest of the tool reports, or its advice would be
    reasoning about a different section than the results panel shows."""
    checks: list[Check] = []
    cases = [
        ("rectangle 50x100", RECTANGLE, None),
        ("L-angle", L_ANGLE, None),
        ("rectangle with a 20x20 hole", RECTANGLE, [[(15, 40), (35, 40), (35, 60), (15, 60)]]),
    ]
    for label, vertices, holes in cases:
        exact = polygon_moments(vertices, holes)
        fe = analyze_section(vertices, MATERIAL, mesh_size=0.5, holes=holes)
        checks.append(
            Check(
                f"polygon_moments matches the FE section engine: {label}",
                _rel_close(exact.area, fe.area, 1e-9)
                and _rel_close(exact.cx, fe.cx, 1e-9)
                and _rel_close(exact.cy, fe.cy, 1e-9)
                and _rel_close(exact.ixx, fe.ixx, 1e-9)
                and _rel_close(exact.iyy, fe.iyy, 1e-9),
                f"exact area/ixx/iyy={exact.area:.6f}/{exact.ixx:.4f}/{exact.iyy:.4f}, "
                f"fe={fe.area:.6f}/{fe.ixx:.4f}/{fe.iyy:.4f}",
            )
        )

    # ...and against the textbook values for a shape that has them.
    exact = polygon_moments(RECTANGLE)
    checks.append(
        Check(
            "polygon_moments matches the textbook rectangle formulas",
            _rel_close(exact.ixx, 50 * 100**3 / 12)
            and _rel_close(exact.iyy, 100 * 50**3 / 12)
            and _rel_close(exact.area, 5000.0),
            f"ixx={exact.ixx}, iyy={exact.iyy}",
        )
    )
    return checks


def run_l_angle_checks() -> list[Check]:
    """A plain L-angle with a square inner corner: one thing to say, and it
    is the corner. Its 10mm legs are uniform, so no wall complaint, and
    although a third of its area does sit near the centroid there is
    nothing there to hollow."""
    found = generate_suggestions(L_ANGLE)
    kinds = [s.kind for s in found]
    checks = [
        Check(
            "L-angle: exactly one suggestion, the unfilleted inner corner",
            kinds == ["sharp_corner"],
            f"got {kinds}",
        )
    ]
    if kinds == ["sharp_corner"]:
        corner = found[0]
        checks.append(
            Check(
                "L-angle: the corner named is the re-entrant one at (10, 10)",
                len(corner.points) == 1
                and _rel_close(corner.points[0][0], 10.0, 1e-9)
                and _rel_close(corner.points[0][1], 10.0, 1e-9),
                f"points={corner.points}",
            )
        )
        checks.append(
            Check(
                "L-angle: the corner is reported at 90 degrees, on the outer ring",
                "90" in corner.title and corner.ring == "outer" and corner.vertex_indices == [3],
                f"title={corner.title!r} ring={corner.ring} idx={corner.vertex_indices}",
            )
        )
    return checks


def run_rectangle_checks() -> list[Check]:
    """A solid bar: nothing wrong with how it's made, everything wrong with
    how much of it is doing nothing. The figures quoted are hand-checkable:
    the middle third of the height is 33.3% of a rectangle's area and
    (1/3)^3 = 3.7% of its Ixx."""
    found = generate_suggestions(RECTANGLE)
    kinds = [s.kind for s in found]
    checks = [
        Check(
            "Solid rectangle: exactly one suggestion, the wasted core",
            kinds == ["core_material"],
            f"got {kinds}",
        )
    ]
    if kinds == ["core_material"]:
        title = found[0].title
        checks.append(
            Check(
                "Solid rectangle: quotes the hand-checkable 33% of area for 4% of Ixx",
                "33%" in title and "4%" in title and "Ixx" in title,
                f"title={title!r}",
            )
        )
        checks.append(
            Check(
                "Solid rectangle: highlights the middle-third band, 33.3mm tall",
                len(found[0].polylines) == 1
                and _rel_close(
                    max(y for _, y in found[0].polylines[0])
                    - min(y for _, y in found[0].polylines[0]),
                    100.0 / 3.0,
                    1e-6,
                ),
                f"polyline={found[0].polylines}",
            )
        )
    return checks


def run_good_tube_checks() -> list[Check]:
    """A well-made hollow section: uniform 4mm walls, 3mm radii on every
    internal corner, material out at the extremities. The radii arrive as
    arc polylines, so this is the test that a filleted corner is not
    mistaken for a sharp one."""
    outer = rounded_rect(0, 0, 60, 40, 3.0)
    holes = [rounded_rect(4, 4, 56, 36, 3.0, ccw=False)]
    found = generate_suggestions(outer, holes)
    return [
        Check(
            "Filleted 60x40 tube: no suggestions (uniform walls, radiused corners)",
            found == [],
            f"got {[s.title for s in found]}",
        )
    ]


def run_kjn_checks() -> list[Check]:
    """The real 20x40 T-slot extrusion. It has 28 sharp internal corners,
    all of them slot mouths, lip tips or the keyway in its central bore --
    functional features on a part that is in production. A suggestion
    engine that lectures this profile is one nobody will read."""
    vertices, holes = _kjn_profile()
    found = generate_suggestions(vertices, holes)
    return [
        Check(
            "KJN 20x40 T-slot: no suggestions (well-designed commercial profile)",
            found == [],
            f"got {[s.title for s in found]}",
        )
    ]


def run_bad_box_checks() -> list[Check]:
    """A box built wrong on purpose: one wall taken down to 0.8mm, square
    voids instead of radiused ones, and as a consequence of that missing
    wall, less Iyy than its envelope should give."""
    found = generate_suggestions(BAD_BOX, BAD_BOX_HOLES)
    kinds = [s.kind for s in found]
    by_kind = {s.kind: s for s in found}
    checks = [
        Check(
            "Bad box: flags the thin wall, the square corners and the lopsided material",
            set(kinds) == {"thin_wall", "sharp_corner", "material_distribution"},
            f"got {kinds}",
        )
    ]
    if "thin_wall" in by_kind:
        thin = by_kind["thin_wall"]
        checks.append(
            Check(
                "Bad box: the thin wall is measured at 0.80mm against a 4.00mm typical",
                "0.80 mm" in thin.title and "4.00 mm" in thin.detail,
                f"title={thin.title!r}",
            )
        )
        checks.append(
            Check(
                "Bad box: the thin wall is called out as below the 1.0mm practical minimum",
                "practical minimum" in thin.detail,
                thin.detail,
            )
        )
        checks.append(
            Check(
                "Bad box: the thin wall is located on the right-hand face (x ~ 60)",
                len(thin.points) == 1 and abs(thin.points[0][0] - 60.0) < 1.0,
                f"points={thin.points}",
            )
        )
    if "sharp_corner" in by_kind:
        corners = by_kind["sharp_corner"]
        checks.append(
            Check(
                "Bad box: all 8 void corners are found, and reported as one finding",
                len(corners.points) == 8 and "8 unfilleted" in corners.title,
                f"title={corners.title!r} points={len(corners.points)}",
            )
        )
    if "material_distribution" in by_kind:
        distribution = by_kind["material_distribution"]
        checks.append(
            Check(
                "Bad box: Iyy is identified as falling short of the 60x40 envelope",
                "Iyy" in distribution.detail and "2.25" in distribution.detail,
                distribution.detail,
            )
        )
    return checks


def main() -> int:
    checks = (
        run_moment_checks()
        + run_l_angle_checks()
        + run_rectangle_checks()
        + run_good_tube_checks()
        + run_kjn_checks()
        + run_bad_box_checks()
    )
    width = max(len(c.label) for c in checks) + 2
    all_passed = True
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        detail = f"  ({c.detail})" if c.detail and not c.passed else ""
        print(f"{c.label:<{width}} {status}{detail}")
    print()
    print(
        f"{len(checks)} checks. "
        + ("All passed." if all_passed else "FAILURES DETECTED — see rows marked FAIL above.")
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
