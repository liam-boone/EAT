"""
Verification harness for the medial-axis wall-thickness measure.

Thickness is the primitive the wall-uniformity, notch and local-buckling
checks are all built on, so "it runs" is worth nothing here -- what matters
is that the number it returns is the number a draughtsman would measure.
Four independent kinds of evidence:

  1. CLOSED FORM. Against a planar opposite face slanted at T to the one
     being measured from, the largest tangent circle has diameter
     2*d*cos(T)/(1 + cos(T)) where d is the distance along the normal.
     Derived by hand (below), checked at six angles to 1e-9. T = 0 is the
     parallel-wall case and must give exactly d.

  2. SHAPES WITH A KNOWN ANSWER. A uniform tube reads its wall thickness
     everywhere on the flats. An annulus reads its wall thickness the whole
     way round. A solid 50x100 bar reads 50 on both faces -- not 100 on the
     short one, which is the whole reason this measure replaced the ray
     cast that did read 100 there.

  3. AN INDEPENDENT IMPLEMENTATION. The fast path is a vectorized
     shrinking-ball iteration over numpy arrays of edges. Every sample on
     every fixture is re-derived by plain bisection on "does a circle of
     radius r centred at p + r*n stay clear of the boundary", asked of
     GEOS via shapely rather than of our own arithmetic. Different
     algorithm, different distance code, same answer required.

  4. THE OPPOSITION ANGLE. The measure is a true material width
     everywhere, but only a *wall* thickness where the circle crosses
     between two roughly parallel faces. The angle that says so has to be
     0 on a flat wall and 90 in a square corner, and the corner samples it
     rejects have to be the ones whose thickness collapses toward zero.

Run with: python -m eat.verify_thickness
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point, Polygon

from eat.dxf_io import import_polygon_from_bytes
from eat.thickness import (
    WALL_OPPOSITION_MAX_DEG,
    normalized_rings,
    sample_thickness,
    thickness_at,
    typical_thickness,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KJN_FIXTURE = PROJECT_ROOT / "eat" / "fixtures" / "20X40_KJN992891.dxf"

RECTANGLE = [(0, 0), (50, 0), (50, 100), (0, 100)]
L_ANGLE = [(0, 0), (50, 0), (50, 10), (10, 10), (10, 60), (0, 60)]
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


def _rel_close(actual: float, expected: float, tol: float = 1e-9) -> bool:
    if expected == 0:
        return abs(actual) < 1e-12
    return abs(actual - expected) / abs(expected) <= tol


def rounded_rect(x0, y0, x1, y1, radius, ccw=True, segments=16):
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


def circle(radius: float, segments: int = 720, ccw: bool = True):
    step = 2 * math.pi / segments
    pts = [(radius * math.cos(k * step), radius * math.sin(k * step)) for k in range(segments)]
    return pts if ccw else pts[::-1]


def _fixtures() -> list[tuple[str, list, list | None]]:
    kjn = import_polygon_from_bytes(KJN_FIXTURE.read_bytes(), source_label=KJN_FIXTURE.name)
    return [
        ("solid 50x100 bar", RECTANGLE, None),
        ("L-angle", L_ANGLE, None),
        ("filleted 60x40 tube", rounded_rect(0, 0, 60, 40, 3.0), [rounded_rect(4, 4, 56, 36, 3.0, ccw=False)]),
        ("bad box", BAD_BOX, BAD_BOX_HOLES),
        ("KJN 20x40 T-slot", kjn.vertices, kjn.holes),
    ]


# --- 1. Closed form ---------------------------------------------------------


def run_closed_form_checks() -> list[Check]:
    """A wall whose far face is slanted.

    Measure from p = (0, 0) on a face lying along y = 0 with the material
    above it, so the inward normal is n = (0, 1). Put the opposite face
    through (0, d) tilted by T, so its outward unit normal is
    u = (-sin T, cos T). A circle touching p has centre c = p + r*n, and it
    fits exactly when its centre is r from that face:

        ((0, d) - c) . u  =  (d - r) cos T  >=  r
                                     r  <=  d cos T / (1 + cos T)

    so the thickness 2r = 2 d cos T / (1 + cos T). At T = 0 that is d --
    the parallel-wall case, where this measure and a ray cast agree. Every
    other angle is where they part company, and the ray cast is the one
    that is wrong: it keeps reporting d however far the far face leans.
    """
    checks: list[Check] = []
    d = 10.0
    reach = 400.0  # long enough that the ends of the wedge never govern
    for degrees in (0.0, 10.0, 20.0, 30.0, 45.0, 60.0):
        T = math.radians(degrees)
        far_left = (-reach, d - reach * math.tan(T))
        far_right = (reach, d + reach * math.tan(T))
        wedge = Polygon([(-reach, 0.0), (reach, 0.0), far_right, far_left])
        got = thickness_at(wedge, (0.0, 0.0), (0.0, 1.0))
        want = 2 * d * math.cos(T) / (1 + math.cos(T))
        checks.append(
            Check(
                f"Slanted far face at {degrees:>4.1f} deg: thickness = 2 d cosT/(1+cosT)",
                _rel_close(got, want, 1e-9),
                f"got {got:.12f}, closed form {want:.12f}",
            )
        )
    return checks


# --- 2. Shapes with a known answer ------------------------------------------


def run_known_shape_checks() -> list[Check]:
    checks: list[Check] = []

    # A uniform 4mm tube: every sample on a flat reads exactly 4.
    outer = rounded_rect(0, 0, 60, 40, 3.0)
    holes = [rounded_rect(4, 4, 56, 36, 3.0, ccw=False)]
    poly = Polygon(outer, holes)
    samples = sample_thickness(poly, normalized_rings(outer, holes))
    flats = [
        s
        for s in samples
        if (abs(s.point[0] - 0) < 1e-9 or abs(s.point[0] - 60) < 1e-9)
        and 8.0 < s.point[1] < 32.0
    ]
    worst = max(abs(s.thickness - 4.0) for s in flats) if flats else float("inf")
    checks.append(
        Check(
            "Uniform 60x40 tube: every sample on a flat face reads its 4.00mm wall",
            len(flats) >= 40 and worst < 1e-9,
            f"{len(flats)} flat samples, worst deviation {worst:.3e}",
        )
    )
    checks.append(
        Check(
            "Uniform 60x40 tube: typical wall is 4.00mm",
            _rel_close(typical_thickness(samples), 4.0, 1e-9),
            f"typical = {typical_thickness(samples):.9f}",
        )
    )

    # An annulus: 6mm of wall the whole way round. The rings are 720-gons,
    # so the exact answer is 6*cos(pi/720) = 5.999943, not 6 -- the
    # measure is exact for the polygon it is actually given.
    ann_outer, ann_inner = circle(20.0), circle(14.0, ccw=False)
    ann = Polygon(ann_outer, [ann_inner])
    ann_samples = sample_thickness(ann, normalized_rings(ann_outer, [ann_inner]))
    exact = 6.0 * math.cos(math.pi / 720)
    spread = max(s.thickness for s in ann_samples) - min(s.thickness for s in ann_samples)
    checks.append(
        Check(
            "Annulus R20/R14: uniform 6mm wall all the way round",
            _rel_close(min(s.thickness for s in ann_samples), exact, 1e-6) and spread < 1e-6,
            f"min {min(s.thickness for s in ann_samples):.9f} max "
            f"{max(s.thickness for s in ann_samples):.9f}, exact for a 720-gon {exact:.9f}",
        )
    )

    # The solid bar is the case the ray cast got wrong. A circle touching
    # the middle of the 50mm-wide bottom face is pinched by the two sides
    # at 50mm, not by the 100mm-away top face.
    bar = Polygon(RECTANGLE)
    long_face = thickness_at(bar, (0.0, 50.0), (1.0, 0.0))
    short_face = thickness_at(bar, (25.0, 0.0), (0.0, 1.0))
    checks.append(
        Check(
            "Solid 50x100 bar: 50mm from the long face (across the bar)",
            _rel_close(long_face, 50.0, 1e-9),
            f"got {long_face:.9f}",
        )
    )
    checks.append(
        Check(
            "Solid 50x100 bar: 50mm from the SHORT face too -- pinched by the sides, not 100",
            _rel_close(short_face, 50.0, 1e-9),
            f"got {short_face:.9f} (a ray cast reports 100 here, which is the bug this replaced)",
        )
    )

    # The L-angle is the other case the ray cast got wrong: a ray up the
    # inside of the bottom face runs the full 60mm height of the vertical
    # leg. The inscribed circle is stopped by the 10mm leg width.
    l_poly = Polygon(L_ANGLE)
    l_samples = sample_thickness(l_poly, normalized_rings(L_ANGLE, None))
    checks.append(
        Check(
            "L-angle: nothing reads thicker than 11.41mm (a ray cast reports its 60mm leg length)",
            max(s.thickness for s in l_samples) < 11.5,
            f"max = {max(s.thickness for s in l_samples):.4f} at "
            f"{max(l_samples, key=lambda s: s.thickness).point}",
        )
    )
    checks.append(
        Check(
            "L-angle: typical wall is its 10mm leg thickness",
            _rel_close(typical_thickness(l_samples), 10.0, 1e-9),
            f"typical = {typical_thickness(l_samples):.9f}",
        )
    )

    # The deliberately thin wall in the bad box has to come back at its
    # drawn 0.8mm, and the rest of it at 4mm.
    box_poly = Polygon(BAD_BOX, BAD_BOX_HOLES)
    box_samples = sample_thickness(box_poly, normalized_rings(BAD_BOX, BAD_BOX_HOLES))
    right_wall = [
        s for s in box_samples if abs(s.point[0] - 60.0) < 1e-9 and 6.0 < s.point[1] < 34.0
    ]
    checks.append(
        Check(
            "Bad box: the 0.8mm right-hand wall measures 0.800mm along its length",
            len(right_wall) >= 20
            and max(abs(s.thickness - 0.8) for s in right_wall) < 1e-9,
            f"{len(right_wall)} samples, worst deviation "
            f"{max(abs(s.thickness - 0.8) for s in right_wall):.3e}"
            if right_wall
            else "no samples found on the right-hand wall",
        )
    )
    checks.append(
        Check(
            "Bad box: typical wall is the 4.00mm the rest of it is drawn at",
            _rel_close(typical_thickness(box_samples), 4.0, 1e-9),
            f"typical = {typical_thickness(box_samples):.9f}",
        )
    )
    return checks


# --- 3. An independent implementation ---------------------------------------


def _bisect_thickness(poly: Polygon, point, normal, upper: float) -> float:
    """The same quantity by an obviously-correct route: bisect on whether
    the circle of radius r centred at p + r*n clears the boundary, asking
    GEOS for the distance rather than our own edge arithmetic.

    Those circles are nested as r grows, so "it fits" is monotone and
    bisection is valid. Slow -- one GEOS query per step per point -- which
    is exactly why the fast path exists and why it needs checking.

    The feasibility test needs a relative slack: below the answer the
    distance to the boundary IS the radius (the circle is tangent at the
    point it was grown from), so the two sides of the comparison agree to
    the last few bits and an absolute tolerance either swamps the answer
    or is lost in it."""
    boundary = poly.boundary
    px, py = point
    nx, ny = normal
    lo, hi = 0.0, upper
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        centre = Point(px + nx * mid, py + ny * mid)
        if boundary.distance(centre) >= mid * (1.0 - 1e-12):
            lo = mid
        else:
            hi = mid
    return 2.0 * lo


def run_cross_check_checks() -> list[Check]:
    """Every sample on every fixture, re-derived by bisection against GEOS.

    This is the check that the vectorized shrinking ball is solving the
    problem it claims to: same definition, different algorithm, different
    distance implementation, agreement required to 1e-9 relative."""
    checks: list[Check] = []
    for label, vertices, holes in _fixtures():
        poly = Polygon(vertices, holes or None)
        rings = normalized_rings(vertices, holes)
        samples = sample_thickness(poly, rings)
        minx, miny, maxx, maxy = poly.bounds
        span = math.hypot(maxx - minx, maxy - miny)
        # Every sample on the small fixtures; a spread subset on the KJN,
        # where 1084 x 80 GEOS queries would dominate the run time.
        stride = max(1, len(samples) // 220)
        subset = samples[::stride]
        worst = 0.0
        worst_at = None
        for s in subset:
            reference = _bisect_thickness(poly, s.point, s.normal, span)
            err = abs(reference - s.thickness) / max(reference, span * 1e-6)
            if err > worst:
                worst, worst_at = err, s.point
        checks.append(
            Check(
                f"{label}: shrinking ball agrees with GEOS bisection ({len(subset)} samples)",
                worst < 1e-9,
                f"worst relative disagreement {worst:.3e} at {worst_at}",
            )
        )
    return checks


# --- 4. The opposition angle ------------------------------------------------


def run_opposition_checks() -> list[Check]:
    checks: list[Check] = []

    outer = rounded_rect(0, 0, 60, 40, 3.0)
    holes = [rounded_rect(4, 4, 56, 36, 3.0, ccw=False)]
    poly = Polygon(outer, holes)
    samples = sample_thickness(poly, normalized_rings(outer, holes))
    flats = [
        s
        for s in samples
        if (abs(s.point[0] - 0) < 1e-9 or abs(s.point[0] - 60) < 1e-9)
        and 8.0 < s.point[1] < 32.0
    ]
    checks.append(
        Check(
            "Tube: a circle crossing a flat wall lands on a parallel face (0 degrees)",
            all(s.opposition_deg < 1e-6 for s in flats),
            f"worst {max(s.opposition_deg for s in flats):.6f} deg",
        )
    )

    # In a square corner the circle lands on a face at right angles to the
    # one it started from, and its diameter collapses to zero as the
    # corner is approached. Both facts are correct; both are why a wall
    # check has to filter on the angle.
    bar = Polygon(RECTANGLE)
    bar_samples = sample_thickness(bar, normalized_rings(RECTANGLE, None))
    corner = [
        s for s in bar_samples if math.hypot(s.point[0] - 50.0, s.point[1] - 0.0) < 1.5
    ]
    checks.append(
        Check(
            "Solid bar: samples inside a square corner report 90 degrees of opposition",
            bool(corner) and all(s.opposition_deg > 89.0 for s in corner),
            f"{len(corner)} samples, min angle "
            f"{min((s.opposition_deg for s in corner), default=float('nan')):.2f} deg",
        )
    )
    checks.append(
        Check(
            "Solid bar: thickness really does collapse at the corner (so the filter is needed)",
            bool(corner) and min(s.thickness for s in corner) < 1.5,
            f"thinnest corner sample {min((s.thickness for s in corner), default=float('nan')):.4f} mm",
        )
    )
    checks.append(
        Check(
            "Solid bar: every collapsed corner sample is excluded by the wall filter",
            all(
                s.opposition_deg > WALL_OPPOSITION_MAX_DEG
                for s in bar_samples
                if s.thickness < 49.0
            ),
            f"{sum(1 for s in bar_samples if s.thickness < 49.0)} sub-49mm samples, "
            f"min angle among them "
            f"{min((s.opposition_deg for s in bar_samples if s.thickness < 49.0), default=float('nan')):.2f} deg",
        )
    )

    # And the filter must not eat real walls: the whole of the bad box's
    # 0.8mm wall has to survive it.
    box_poly = Polygon(BAD_BOX, BAD_BOX_HOLES)
    box_samples = sample_thickness(box_poly, normalized_rings(BAD_BOX, BAD_BOX_HOLES))
    thin_run = sum(
        s.run
        for s in box_samples
        if s.thickness < 1.0 and s.opposition_deg <= WALL_OPPOSITION_MAX_DEG
    )
    checks.append(
        Check(
            "Bad box: the filter keeps all ~64mm of the 0.8mm wall (32mm from each face)",
            63.0 < thin_run < 65.5,
            f"{thin_run:.2f} mm of sub-1mm wall-like boundary",
        )
    )
    return checks


def main() -> int:
    checks = (
        run_closed_form_checks()
        + run_known_shape_checks()
        + run_cross_check_checks()
        + run_opposition_checks()
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
