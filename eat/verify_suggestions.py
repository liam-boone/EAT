"""
Verification harness for the design-suggestions engine (build step 11).

The thing worth verifying here isn't that the code runs, it's that the
suggestions are ones a competent extrusion engineer would actually make
-- and, just as importantly, that a well-designed profile comes back
quiet. So this runs the engine against profiles whose right answer is
known by inspection:

  L-angle, no fillet      -> exactly the unfilleted inner corner, nothing else
  solid 50x100 bar        -> exactly the wasted core, with hand-checkable figures
  filleted 60x40 tube     -> silent (uniform walls, radiused corners, hollow)
  20x40 KJN T-slot        -> silent (real commercial part, in production)
  deliberately bad box    -> the 0.8mm wall, the square voids, the lopsided material
  heavy-wall tube         -> exactly the 10mm wall it was built with (2.5x the rest)
  2:1 tube                -> silent (8mm on 4mm is the practice limit, not past it)

The filleted tube and the KJN matter as much as the failing cases: the
tube's 3mm inner radii must NOT read as sharp corners (they arrive as
arc polylines, which is exactly what a filleted corner looks like coming
out of a DXF), and the KJN has 28 sharp internal corners that are all
functional slot and keyway features, which the engine is expected to
leave alone.

The last two are the thick-wall check's pair. They are the same tube as
the "good" one with a single wall thickened -- to 10mm, which is past
what extrusion practice will take, and to 8mm, which is exactly the 2:1
limit it quotes. One has to fire and the other has to stay quiet, and
nothing else about either profile changes, so a thick-wall finding can
only have come from the thickness that was altered.

`run_notch_checks` uses the same trick again, cutting slots into that
tube's bottom wall. Its own pair is a slot 3.2mm deep (leaving 0.8mm,
reported) against the identical slot 3.0mm deep (leaving exactly the
1.0mm minimum wall, silent), which is the notch check's entire
sensitivity in two fixtures.

WHAT IS NOT COVERED HERE: the notch check's real false-positive risk is
functional slots on production profiles, and the profiles that prove it
are the confidential supplier PDFs in the project root, which are not in
git and cannot be a fixture. They were used during development -- the
check's floor is set where it is because the thinnest notch-shaped
candidate across all seven of them is 1.476mm -- and `eat/verify_pdf.py`
is where they get exercised when present. If that floor is ever changed,
re-run against those drawings, not against this file alone: every fixture
here would happily pass a much more sensitive check that buries a real
drawing in findings.

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

from shapely.geometry import Polygon, box

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


def _notched(*cuts):
    """The good tube with rectangular slots cut out of it. Built by
    subtraction rather than by splicing vertices so the fixture is
    obviously the good tube plus exactly the cut described."""
    tube = Polygon(rounded_rect(0, 0, 60, 40, 3.0), [rounded_rect(4, 4, 56, 36, 3.0, ccw=False)])
    for c in cuts:
        tube = tube.difference(c)
    return list(tube.exterior.coords)[:-1], [list(r.coords)[:-1] for r in tube.interiors]


def run_notch_checks() -> list[Check]:
    """Slots cut into the good tube's 4mm bottom wall.

    The tube is silent, so anything reported can only be about the cut.
    The pair that matters is the first two: an 0.8mm-wide slot 3.2mm deep
    leaves 0.8mm and is reported, the same slot 3.0mm deep leaves exactly
    1.0mm and is not. That is the check's whole sensitivity -- it reports a
    notch when what the notch leaves is under the practical minimum wall,
    and not otherwise. See the module docstring for why it is scoped that
    way rather than on how deep the cut is relative to the wall.

    The corner check must stay silent on all of these, which is the
    original limitation being demonstrated: the slot's faces are 0.8mm and
    3.2mm against a 5.4mm minimum-face bar, so both are far too short for
    it however deep the slot goes."""
    checks: list[Check] = []

    deep = generate_suggestions(*_notched(box(29.6, -1.0, 30.4, 3.2)))
    kinds = [s.kind for s in deep]
    checks.append(
        Check(
            "Notched tube (0.8 x 3.2mm slot, 0.8mm left): exactly one finding, the notch",
            kinds == ["narrow_notch"],
            f"got {kinds}",
        )
    )
    if kinds == ["narrow_notch"]:
        notch = deep[0]
        checks.append(
            Check(
                "Notched tube: quotes 0.80mm left of a 4.00mm wall, 3.20mm deep",
                "0.80 mm" in notch.title and "4.00 mm" in notch.title and "3.20 mm" in notch.title,
                f"title={notch.title!r}",
            )
        )
        checks.append(
            Check(
                "Notched tube: located at the slot root (x ~ 30, y ~ 3.2)",
                len(notch.points) == 1
                and abs(notch.points[0][0] - 30.0) < 0.6
                and abs(notch.points[0][1] - 3.2) < 0.6,
                f"points={notch.points}",
            )
        )
        checks.append(
            Check(
                "Notched tube: reported once, not once per face of the same slot",
                len([s for s in deep if s.kind == "narrow_notch"]) == 1,
                f"got {len(deep)} findings",
            )
        )

    shallower = generate_suggestions(*_notched(box(29.6, -1.0, 30.4, 3.0)))
    checks.append(
        Check(
            "Same slot 3.0mm deep (leaves exactly the 1.0mm minimum wall): silent",
            shallower == [],
            f"got {[s.title for s in shallower]}",
        )
    )

    shallow = generate_suggestions(*_notched(box(29.6, -1.0, 30.4, 1.0)))
    checks.append(
        Check(
            "Same slot 1.0mm deep (leaves 3mm of a 4mm wall): silent",
            shallow == [],
            f"got {[s.title for s in shallow]}",
        )
    )

    hairline = generate_suggestions(*_notched(box(29.8, -1.0, 30.2, 3.4)))
    checks.append(
        Check(
            "Hairline slot (0.4 x 3.4mm, 0.6mm left): flagged, and reported at 0.60mm",
            [s.kind for s in hairline] == ["narrow_notch"] and "0.60 mm" in hairline[0].title,
            f"got {[s.title for s in hairline]}",
        )
    )

    two = generate_suggestions(*_notched(box(19.6, -1.0, 20.4, 3.2), box(39.6, -1.0, 40.4, 3.2)))
    xs = sorted(round(s.points[0][0]) for s in two if s.kind == "narrow_notch")
    checks.append(
        Check(
            "Two separate slots 20mm apart: reported as two findings, not merged",
            len([s for s in two if s.kind == "narrow_notch"]) == 2 and xs == [20, 40],
            f"got {[s.title for s in two]}",
        )
    )

    # A wide flat-bottomed groove leaves the same 0.8mm but over 8mm of
    # boundary, so it reads as a thin wall. This is the documented
    # deduplication: the thin-wall finding owns that metal and the notch
    # is suppressed rather than both being reported.
    groove = generate_suggestions(*_notched(box(26.0, -1.0, 34.0, 3.2)))
    kinds = [s.kind for s in groove]
    checks.append(
        Check(
            "Wide groove (8 x 3.2mm): reported as a thin wall only, notch deduplicated away",
            "thin_wall" in kinds and "narrow_notch" not in kinds,
            f"got {kinds}",
        )
    )
    return checks


def run_heavy_wall_checks() -> list[Check]:
    """The good 60x40 tube with its right-hand wall left at 10mm instead
    of 4mm -- the mistake the thick-wall check exists to catch, and the
    one the abandoned ray-cast measure could never find because it could
    not tell a thick wall from a long leg.

    Everything else about the profile is identical to the silent good
    tube: same envelope, same 3mm radii, same 4mm walls on the other three
    sides. So the finding can only be about the thickness that changed,
    and the figures are exact by construction -- 10.00mm against a 4.00mm
    typical wall, over the 32mm height of that wall."""
    outer = rounded_rect(0, 0, 60, 40, 3.0)
    holes = [rounded_rect(4, 4, 50, 36, 3.0, ccw=False)]
    found = generate_suggestions(outer, holes)
    by_kind = {s.kind: s for s in found}
    checks = [
        Check(
            "Heavy-wall tube: flags the thick wall",
            "thick_wall" in by_kind,
            f"got {[s.kind for s in found]}",
        )
    ]
    if "thick_wall" in by_kind:
        thick = by_kind["thick_wall"]
        checks.append(
            Check(
                "Heavy-wall tube: measured at 10.00mm against the 4.00mm typical wall, 2.5x",
                "10.00 mm" in thick.title and "4.00 mm" in thick.detail and "2.5x" in thick.title,
                f"title={thick.title!r}",
            )
        )
        checks.append(
            Check(
                "Heavy-wall tube: located on the right-hand wall (x ~ 50 or 60)",
                len(thick.points) == 1 and abs(abs(thick.points[0][0] - 55.0) - 5.0) < 1.0,
                f"points={thick.points}",
            )
        )
        run_mm = float(thick.detail.split("over about ")[1].split(" mm of boundary")[0])
        checks.append(
            Check(
                "Heavy-wall tube: reports most of that wall's 32mm height, not a corner's worth",
                25.0 <= run_mm <= 34.0,
                f"run reported as {run_mm} mm (the wall is 32mm tall between the 3mm radii)",
            )
        )
    checks.append(
        Check(
            "Heavy-wall tube: no thin-wall finding (the other three walls are the 4mm norm)",
            "thin_wall" not in by_kind,
            f"got {[s.kind for s in found]}",
        )
    )
    return checks


def run_two_to_one_tube_checks() -> list[Check]:
    """The same tube with an 8mm right-hand wall: exactly 2:1 against the
    4mm the rest of it runs at, which is the limit extrusion practice
    quotes rather than something past it. It has to come back quiet, or
    the check is nagging about geometry the design manuals allow."""
    outer = rounded_rect(0, 0, 60, 40, 3.0)
    holes = [rounded_rect(4, 4, 52, 36, 3.0, ccw=False)]
    found = generate_suggestions(outer, holes)
    return [
        Check(
            "2:1 tube (8mm on 4mm): silent -- at the practice limit, not past it",
            found == [],
            f"got {[s.title for s in found]}",
        )
    ]


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
        + run_heavy_wall_checks()
        + run_two_to_one_tube_checks()
        + run_notch_checks()
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
