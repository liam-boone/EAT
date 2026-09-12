"""
EAT design suggestions — DFM / stiffness advice reasoned from the actual
profile geometry (build step 11).

Every check here reads the real vertex/hole data and only fires when the
geometry supports it; none of them emit generic advice. The thresholds
are chosen from extrusion design practice and documented inline with the
reasoning, because a suggestion engine that cries wolf on legitimate
geometry is worse than no suggestion engine -- a well-designed commercial
profile should come back quiet.

What's checked, and why each threshold:

1. Thin walls. Local wall thickness is sampled along the whole boundary
   (every ~0.4mm) by casting a ray into the material and taking the first
   face it hits. A wall is flagged below 0.6x the profile's typical wall
   (the length-weighted median, so it's the typical millimetre of wall
   rather than the typical face) or below 1.0mm outright, which is about
   the practical floor for filling 6xxx aluminium. Two guards keep it
   honest. A finding must persist over at least twice the typical wall
   thickness of boundary, which distinguishes a real wall from the rib
   junctions and end caps a single ray happens to sample. And the check
   is skipped entirely unless the typical wall is under a quarter of the
   profile's smaller bounding-box dimension: a solid bar has no walls,
   and its two dimensions aren't a uniformity problem.

   KNOWN LIMITATION (v1, accepted): there is deliberately no "thick wall"
   counterpart, despite thickness uniformity cutting both ways in
   principle. Ray casting cannot distinguish genuine thickness from a long
   off-axis leg: a ray normal to a boundary only measures a wall when it
   crosses one, and when it happens to run lengthwise up a leg or a rib it
   measures how long that feature is, which reads as a hugely thick wall.
   In testing that produced a false positive on every profile tried and a
   true positive on none -- the L-angle's 60mm "wall" was the length of
   its vertical leg, and the commercial T-slot fixture's was a ray running
   up the inside of its outer wall -- and tightening the thresholds did
   not separate them. The underlying concern (material that isn't earning
   its mass) is caught by check 4 instead, which integrates area and
   inertia exactly rather than inferring them from ray casts, so nothing
   is actually going unnoticed. A real fix needs a medial-axis or
   maximum-inscribed-circle thickness measure: that's a backlog item, not
   a v1 blocker. Don't re-add a ray-cast version without solving this
   first -- it has already been tried and it doesn't work.

2. Sharp internal corners. A re-entrant (material-concave) vertex is a
   stress riser and an awkward die feature. Flagged when the open-side
   included angle is <= 100 degrees, i.e. the direction turns by >= 80
   degrees at a single vertex. That threshold also does the "lacks any
   fillet" work for free: a fillet imported from DXF arrives as an arc
   approximated to a 0.02mm chord tolerance, which spreads its turn over
   many vertices -- even a 0.25mm-radius fillet only turns ~46 degrees
   per vertex, and a 1mm one ~23. Nothing but a genuinely sharp corner
   turns 80 degrees at once. Two exclusions matter as much as the rule:
   convex (outside) corners are never flagged, because extrusions have
   plenty of legitimate sharp outside corners; and both faces meeting at
   the corner must be at least 20% of sqrt(section area), so the corner
   joins two of the profile's primary faces and sits in the main load
   path. Without that second test the check drowns in functional detail
   -- the 20x40 T-slot fixture has 28 sharp internal corners, every one
   of them a slot mouth, lip tip or keyway that exists on purpose.

   KNOWN LIMITATION (v1, accepted): that face-size filter intentionally
   misses a narrow, deep notch cut into an otherwise clean wall, because
   one face of a notch is short by definition even though the notch root
   is a genuine stress riser. Accepted for v1 given how cleanly the filter
   separates the tested cases on min-face/sqrt(area): KJN 0.12 (all 28
   corners correctly dropped), bad-box 0.87 and L-angle 1.27 (both
   correctly kept). The threshold sits in a 7x gap, so it isn't
   knife-edge -- but anything that narrows that gap, notch detection
   included, wants a rethink rather than a nudged constant.

3. Material distribution vs. the bending axes. Raw Ixx/Iyy anisotropy is
   mostly just the envelope talking -- any profile in a 40x20 box is
   about 4x stiffer one way, and saying so isn't a suggestion. What *is*
   actionable is being more lopsided than the envelope requires, so the
   actual Iyy/Ixx ratio is compared against (W/H)^2, the ratio a solid
   rectangle of the same bounding box would have, and flagged only at
   1.5x off that.

4. Material near the centroid. Area within the middle third of the
   profile's depth contributes to bending in proportion to the square of
   its (small) distance from the neutral axis, so it's mass that isn't
   buying stiffness. Flagged when that band holds >= 25% of the area but
   <= 10% of the inertia, and only when the material there could actually
   come out: at least 6mm thick (below that there's no room to core
   anything and still leave a wall each side), and either a bulk bar
   (hole-free, filling >= 60% of its bounding box) or a core markedly
   chunkier than the profile's own walls. That last guard is what stops
   it firing on a plain L-angle, where a third of the area genuinely does
   sit near the centroid but it's a 10mm leg with nothing to hollow.

Section properties used here (area, centroid, Ixx, Iyy) are computed
exactly from the polygon by the shoelace/Green's-theorem formulas in
`polygon_moments`, not by meshing: this endpoint stays instant, and the
exact values agree with eat.section's FE results to ~1e-9 (cross-checked
in eat/verify_suggestions.py).

Run against a profile:

    from eat.suggestions import generate_suggestions
    for s in generate_suggestions(vertices, holes):
        print(s.title)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.prepared import prep

Vertex = tuple[float, float]

# --- Thresholds (see module docstring for the reasoning behind each) ---------

MIN_PRACTICAL_WALL_MM = 1.0  # below this is hard to fill in 6xxx aluminium at any size
THIN_WALL_FRACTION = 0.6  # flag walls under 60% of the profile's typical wall
THIN_WALLED_PROFILE_FRACTION = 0.25  # typical wall must be under this share of the smaller
# bounding-box dimension for wall-uniformity analysis to mean anything at all: a solid bar's
# "wall" is its own width, and comparing its two dimensions to each other says nothing.
WALL_RUN_THICKNESS_MULTIPLE = 2.0  # a finding must persist this far along the boundary; below
# that it's a corner or rib junction the ray happened to sample, not a wall
SHARP_CORNER_MAX_OPEN_ANGLE_DEG = 100.0  # <= this on the open side is a sharp internal corner
SHARP_CORNER_MIN_FACE_FRACTION = 0.20  # both faces meeting at the corner must be at least this
# share of sqrt(section area), so the corner joins two primary faces rather than a functional
# detail. Measured on the test profiles: KJN 0.12 (all 28 slot/keyway corners dropped),
# bad-box 0.87, L-angle 1.27 -- a 7x gap around the threshold, so it is not knife-edge.
# KNOWN LIMITATION (v1, accepted): this intentionally misses a narrow, deep notch cut into an
# otherwise clean wall, since one face of a notch is short however deep it goes. See the
# module docstring; catching those needs a different test, not a smaller number here.
MIN_CORNER_EDGE_MM = 0.3  # shorter neighbours than this means arc-approximation noise
ANISOTROPY_ENVELOPE_FACTOR = 1.5  # how much worse than the envelope before it's worth saying
CORE_AREA_FRACTION = 0.25  # band must hold at least this share of the area
CORE_INERTIA_FRACTION = 0.10  # ...while contributing no more than this share of I
CORE_MIN_THICKNESS_MM = 6.0  # ~4x a 1.5mm wall: room to core out and leave walls both sides
CORE_SOLIDITY = 0.6  # area/bounding-box area at or above which a hole-free profile is a bulk bar
CORE_WALL_MULTIPLE = 1.5  # ...or, for a walled profile, the band must be this much chunkier
# than the walls the design already proves are enough
MAX_SUGGESTIONS = 6  # keep the list short enough to actually read

# Emitted in this order, most manufacturing-critical first.
_KIND_ORDER = ["thin_wall", "sharp_corner", "material_distribution", "core_material"]


@dataclass
class Suggestion:
    """One grounded suggestion. `points`/`polylines` are world-coordinate
    (mm) geometry for the frontend to highlight; `ring`/`vertex_indices`
    name the same feature by index for traceability."""

    kind: str
    title: str
    detail: str
    ring: str | None = None
    vertex_indices: list[int] = field(default_factory=list)
    points: list[Vertex] = field(default_factory=list)
    polylines: list[list[Vertex]] = field(default_factory=list)


@dataclass
class SectionMoments:
    area: float
    cx: float
    cy: float
    ixx: float  # about the centroid
    iyy: float


# --- Exact polygon moments (shoelace / Green's theorem) ----------------------


def _ring_moments(coords: list[Vertex]) -> tuple[float, float, float, float, float]:
    """Signed (area, integral of x, integral of y, integral of y^2, integral
    of x^2) for one closed ring, about the origin. Sign follows the ring's
    orientation, so a CW hole subtracts naturally."""
    a = sx = sy = ixx = iyy = 0.0
    n = len(coords)
    for i in range(n):
        x0, y0 = coords[i]
        x1, y1 = coords[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        sx += (x0 + x1) * cross
        sy += (y0 + y1) * cross
        ixx += (y0 * y0 + y0 * y1 + y1 * y1) * cross
        iyy += (x0 * x0 + x0 * x1 + x1 * x1) * cross
    return a / 2.0, sx / 6.0, sy / 6.0, ixx / 12.0, iyy / 12.0


def _raw_moments(rings: list[tuple[str, list[Vertex]]]) -> tuple[float, float, float, float, float]:
    """(area, Sx, Sy, Ixx, Iyy) about the origin, summed over oriented rings."""
    a = sx = sy = ixx = iyy = 0.0
    for _, coords in rings:
        ra, rsx, rsy, rixx, riyy = _ring_moments(coords)
        a += ra
        sx += rsx
        sy += rsy
        ixx += rixx
        iyy += riyy
    return a, sx, sy, ixx, iyy


def polygon_moments(vertices: list[Vertex], holes: list[list[Vertex]] | None = None) -> SectionMoments:
    """Area, centroid and centroidal Ixx/Iyy, exactly, straight from the
    polygon -- no meshing. Matches eat.section's FE values (verified)."""
    rings = _normalized_rings(vertices, holes)
    a, sx, sy, ixx_o, iyy_o = _raw_moments(rings)
    if a <= 0:
        raise ValueError("Profile has zero or negative area")
    cx, cy = sx / a, sy / a
    return SectionMoments(area=a, cx=cx, cy=cy, ixx=ixx_o - a * cy * cy, iyy=iyy_o - a * cx * cx)


def _polygon_parts(geom) -> list[Polygon]:
    """Just the 2D parts of a geometry. Clipping a profile to a band can
    return a GeometryCollection whose stray lines/points (where the band
    edge grazes the boundary) carry no area and must be dropped."""
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        parts: list[Polygon] = []
        for part in geom.geoms:
            parts.extend(_polygon_parts(part))
        return parts
    return []


def _geometry_moments(geom) -> tuple[float, float, float, float, float]:
    """(area, Sx, Sy, Ixx, Iyy) about the origin for any shapely geometry's
    polygonal parts, with interior rings subtracted."""
    a = sx = sy = ixx = iyy = 0.0
    for poly in _polygon_parts(geom):
        if poly.is_empty:
            continue
        for ring, is_hole in [(poly.exterior, False)] + [(r, True) for r in poly.interiors]:
            coords = list(ring.coords)[:-1]
            if len(coords) < 3:
                continue
            ra, rsx, rsy, rixx, riyy = _ring_moments(coords)
            # Normalize sign by role rather than trusting the ring's winding.
            sign = -1.0 if is_hole else 1.0
            if (ra < 0) != (sign < 0):
                ra, rsx, rsy, rixx, riyy = -ra, -rsx, -rsy, -rixx, -riyy
            a += ra
            sx += rsx
            sy += rsy
            ixx += rixx
            iyy += riyy
    return a, sx, sy, ixx, iyy


# --- Geometry setup ---------------------------------------------------------


def _normalized_rings(
    vertices: list[Vertex], holes: list[list[Vertex]] | None
) -> list[tuple[str, list[Vertex]]]:
    """Rings as (label, coords) with the outer boundary CCW and every hole
    CW -- the same convention eat.section._build_geometry normalizes to.
    With that winding, the material always lies to the LEFT of the
    direction of travel, on every ring, which is what the wall-thickness
    and corner checks below rely on."""
    poly = orient(Polygon(vertices, holes or None), sign=1.0)
    rings: list[tuple[str, list[Vertex]]] = [("outer", list(poly.exterior.coords)[:-1])]
    for i, interior in enumerate(poly.interiors):
        rings.append((f"hole {i + 1}", list(interior.coords)[:-1]))
    return rings


def _build_polygon(vertices: list[Vertex], holes: list[list[Vertex]] | None) -> Polygon:
    return orient(Polygon(vertices, holes or None), sign=1.0)


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def _pt(p: Vertex) -> str:
    return f"({_fmt(p[0])}, {_fmt(p[1])})"


# --- Check 1: wall thickness ------------------------------------------------


@dataclass
class WallSample:
    """One thickness measurement, taken at a point on the boundary."""

    ring: str
    index: int  # index of the edge's first vertex within its ring
    point: Vertex  # where on the boundary it was measured
    run: float  # how much boundary length this sample stands for
    thickness: float
    centre: Vertex  # midpoint of the measured through-thickness segment


def _flatten_points(geom) -> list[Vertex]:
    if geom.is_empty:
        return []
    kind = geom.geom_type
    if kind == "Point":
        return [(geom.x, geom.y)]
    if kind in ("LineString", "LinearRing"):
        return list(geom.coords)
    if kind == "Polygon":
        return list(geom.exterior.coords)
    if kind in ("MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection"):
        out: list[Vertex] = []
        for part in geom.geoms:
            out.extend(_flatten_points(part))
        return out
    return []


def _wall_samples(poly: Polygon, rings: list[tuple[str, list[Vertex]]]) -> list[WallSample]:
    """Local wall thickness sampled along the whole boundary, by casting a
    ray into the material and taking the first boundary it meets.

    Sampled at intervals rather than once per edge because a single
    midpoint sample on a long face is easily unrepresentative -- on a
    box section with an internal rib, the one ray from the middle of the
    bottom face runs straight up the rib and reads the full height of the
    part. Dense sampling lets `_cluster_samples` tell that kind of
    one-off reading (a few mm of boundary) from a real wall (tens of mm).
    """
    minx, miny, maxx, maxy = poly.bounds
    span = math.hypot(maxx - minx, maxy - miny)
    step = max(0.4, span / 300.0)
    boundary = poly.boundary
    inside = prep(poly)
    eps = span * 1e-7
    samples: list[WallSample] = []

    for ring_label, coords in rings:
        n = len(coords)
        for i in range(n):
            x0, y0 = coords[i]
            x1, y1 = coords[(i + 1) % n]
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length < eps:
                continue
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux  # left normal: into the material for both windings
            count = max(1, min(60, int(round(length / step))))
            run = length / count
            for j in range(count):
                f = (j + 0.5) / count
                px, py = x0 + dx * f, y0 + dy * f
                start = (px + nx * eps, py + ny * eps)
                if not inside.contains(Point(start)):
                    continue  # sliver or unexpected winding -- don't guess
                ray = LineString([start, (px + nx * span * 1.5, py + ny * span * 1.5)])
                hits = _flatten_points(ray.intersection(boundary))
                distances = [
                    math.hypot(hx - px, hy - py)
                    for hx, hy in hits
                    if math.hypot(hx - px, hy - py) > eps * 10
                ]
                if not distances:
                    continue
                thickness = min(distances)
                samples.append(
                    WallSample(
                        ring=ring_label,
                        index=i,
                        point=(px, py),
                        run=run,
                        thickness=thickness,
                        centre=(px + nx * thickness / 2.0, py + ny * thickness / 2.0),
                    )
                )
    return samples


def _weighted_median(values: list[float], weights: list[float]) -> float:
    """Median weighted by wall length -- the thickness of the typical
    millimetre of wall, rather than of the typical face, so a profile
    isn't skewed by a handful of short faces."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    total = sum(weights)
    running = 0.0
    for i in order:
        running += weights[i]
        if running >= total / 2.0:
            return values[i]
    return values[order[-1]]


def _cluster_samples(samples: list[WallSample], step: float) -> list[list[WallSample]]:
    """Group flagged samples that describe the same physical wall.

    Clustering is on the midpoint of each measured through-thickness
    segment, not on the boundary point, which means the two faces of one
    wall land on the same centre-line and merge into a single finding
    instead of being reported once from each side."""
    clusters: list[list[WallSample]] = []
    centres: list[list[Vertex]] = []
    for sample in samples:
        tol = max(2.0 * step, 0.6 * sample.thickness)
        placed = False
        for cluster, cluster_centres in zip(clusters, centres):
            if any(
                math.hypot(cx - sample.centre[0], cy - sample.centre[1]) <= tol
                for cx, cy in cluster_centres
            ):
                cluster.append(sample)
                cluster_centres.append(sample.centre)
                placed = True
                break
        if not placed:
            clusters.append([sample])
            centres.append([sample.centre])
    return clusters


def _cluster_run(cluster: list[WallSample]) -> float:
    """Boundary length this finding spans, counted per ring so that a wall
    measured from both of its faces isn't double-counted."""
    per_ring: dict[str, float] = {}
    for sample in cluster:
        per_ring[sample.ring] = per_ring.get(sample.ring, 0.0) + sample.run
    return max(per_ring.values())


def _wall_thickness_suggestions(
    samples: list[WallSample], typical: float, step: float
) -> list[Suggestion]:
    if typical <= 0 or len(samples) < 2:
        return []
    min_run = WALL_RUN_THICKNESS_MULTIPLE * typical

    thin = [
        s
        for s in samples
        if s.thickness < THIN_WALL_FRACTION * typical or s.thickness < MIN_PRACTICAL_WALL_MM
    ]

    suggestions: list[Suggestion] = []

    thin_clusters = [c for c in _cluster_samples(thin, step) if _cluster_run(c) >= min_run]
    thin_clusters.sort(key=lambda c: min(s.thickness for s in c))
    for cluster in thin_clusters[:2]:
        worst = min(cluster, key=lambda s: s.thickness)
        run = _cluster_run(cluster)
        detail = (
            f"The wall here measures {_fmt(worst.thickness, 2)} mm through the section over about "
            f"{_fmt(run, 1)} mm of boundary, against {_fmt(typical, 2)} mm for the profile's "
            f"typical wall ({worst.thickness / typical:.0%} of it). Extrusions want reasonably "
            "uniform walls: a thin section fills more slowly and cools faster than its "
            "neighbours, which pulls the profile out of tolerance (bow and twist) and makes the "
            f"die harder to balance. Thicken it toward {_fmt(typical, 2)} mm, or blend into it "
            "gradually rather than stepping."
        )
        if worst.thickness < MIN_PRACTICAL_WALL_MM:
            detail += (
                f" It is also under the ~{_fmt(MIN_PRACTICAL_WALL_MM, 1)} mm practical minimum "
                "wall for extruded aluminium, so it may not fill reliably at all."
            )
        suggestions.append(
            Suggestion(
                kind="thin_wall",
                title=f"Thin wall — {_fmt(worst.thickness, 2)} mm near {_pt(worst.point)}",
                detail=detail,
                ring=worst.ring,
                vertex_indices=sorted({s.index for s in cluster if s.ring == worst.ring}),
                points=[worst.point],
                polylines=[[s.point for s in cluster if s.ring == worst.ring]],
            )
        )

    return suggestions


# --- Check 2: sharp internal corners ---------------------------------------


def _sharp_corner_suggestions(
    rings: list[tuple[str, list[Vertex]]], area: float
) -> list[Suggestion]:
    """Re-entrant corners sharp enough, and structural enough, to be worth
    raising. See the module docstring for both thresholds -- in particular
    why a corner between two small functional faces (a slot mouth, a lip,
    a keyway) is deliberately left alone."""
    min_face = SHARP_CORNER_MIN_FACE_FRACTION * math.sqrt(area)
    found: list[tuple[float, str, int, Vertex]] = []

    for ring_label, coords in rings:
        n = len(coords)
        if n < 3:
            continue
        for i in range(n):
            prev_pt = coords[i - 1]
            here = coords[i]
            next_pt = coords[(i + 1) % n]
            ax, ay = here[0] - prev_pt[0], here[1] - prev_pt[1]
            bx, by = next_pt[0] - here[0], next_pt[1] - here[1]
            len_a, len_b = math.hypot(ax, ay), math.hypot(bx, by)
            if len_a < MIN_CORNER_EDGE_MM or len_b < MIN_CORNER_EDGE_MM:
                continue  # a chord of a discretized fillet, not a corner
            if min(len_a, len_b) < min_face:
                continue  # a local detail feature, not a primary structural junction
            cross = ax * by - ay * bx
            if cross >= 0:
                continue  # left turn: the material is convex here, which is fine
            dot = ax * bx + ay * by
            turn = math.degrees(math.atan2(abs(cross), dot))
            open_angle = 180.0 - turn
            if open_angle <= SHARP_CORNER_MAX_OPEN_ANGLE_DEG:
                found.append((open_angle, ring_label, i, here))

    if not found:
        return []

    found.sort(key=lambda item: item[0])
    sharpest_angle, sharpest_ring, _, sharpest_point = found[0]
    named = ", ".join(_pt(point) for _, _, _, point in found[:3])
    if len(found) == 1:
        where = f"at {_pt(sharpest_point)} on the {sharpest_ring} boundary"
        title = f"Unfilleted internal corner ({_fmt(sharpest_angle, 0)}°) at {_pt(sharpest_point)}"
    else:
        where = f"at {named}" + (f" and {len(found) - 3} more" if len(found) > 3 else "")
        title = f"{len(found)} unfilleted internal corners, sharpest {_fmt(sharpest_angle, 0)}° at {_pt(sharpest_point)}"

    return [
        Suggestion(
            kind="sharp_corner",
            title=title,
            detail=(
                f"The material turns through {_fmt(180.0 - sharpest_angle, 0)}° at a single vertex "
                f"{where}, leaving a re-entrant corner with no radius on it — a corner joining two "
                "of the profile's primary faces, so it sits in the main load path rather than "
                "being a local detail. Internal corners concentrate stress (a sharp one typically "
                "runs 2-3x the nominal stress of a filleted one), and the matching feature in the "
                "die is a thin tongue that wears and chips. Even a 0.5-1 mm radius relieves most "
                "of the concentration and extrudes considerably more happily."
            ),
            ring=sharpest_ring,
            vertex_indices=[index for _, ring, index, _ in found if ring == sharpest_ring],
            points=[point for _, _, _, point in found],
        )
    ]


# --- Check 3: material distribution vs. the bending axes --------------------


def _distribution_suggestions(moments: SectionMoments, poly: Polygon) -> list[Suggestion]:
    minx, miny, maxx, maxy = poly.bounds
    width, height = maxx - minx, maxy - miny
    if width <= 0 or height <= 0 or moments.ixx <= 0 or moments.iyy <= 0:
        return []

    envelope_ratio = (width / height) ** 2  # Iyy/Ixx for a solid rectangle of this envelope
    actual_ratio = moments.iyy / moments.ixx
    relative = actual_ratio / envelope_ratio

    if ANISOTROPY_ENVELOPE_FACTOR >= relative >= 1.0 / ANISOTROPY_ENVELOPE_FACTOR:
        return []  # no worse than the bounding box already dictates

    weak_about_x = relative > 1.0  # Ixx is the one that came up short
    if weak_about_x:
        weak_i, weak_label, load_dir = moments.ixx, "Ixx", "Y-direction"
        extreme = max(maxy - moments.cy, moments.cy - miny)
        edges = f"y = {_fmt(miny)} / y = {_fmt(maxy)}"
        span_note = "top and bottom"
    else:
        weak_i, weak_label, load_dir = moments.iyy, "Iyy", "X-direction"
        extreme = max(maxx - moments.cx, moments.cx - minx)
        edges = f"x = {_fmt(minx)} / x = {_fmt(maxx)}"
        span_note = "left and right"

    radius_of_gyration = math.sqrt(weak_i / moments.area)
    leverage = (extreme / radius_of_gyration) ** 2

    box_poly = box(minx, miny, maxx, maxy)
    fill = moments.area / box_poly.area

    return [
        Suggestion(
            kind="material_distribution",
            title=f"Material is distributed more lopsidedly than the {_fmt(width)} x {_fmt(height)} mm envelope requires",
            detail=(
                f"Iyy/Ixx is {actual_ratio:.2f}, where a solid rectangle in the same envelope "
                f"would sit at {envelope_ratio:.2f} — so the shortfall is in how the material is "
                f"arranged, not just the overall proportions. {weak_label} is the one falling "
                f"short of what the envelope allows, and it is what {load_dir} loads bend about. "
                f"The profile fills {fill:.0%} of its "
                "bounding box, and because a unit of area contributes to bending in proportion to "
                "the square of its distance from the neutral axis, area moved out to the "
                f"{span_note} extremities ({edges}) is worth about {leverage:.1f}x as much to "
                f"{weak_label} as the same area sitting at the section's current radius of "
                f"gyration ({_fmt(radius_of_gyration, 2)} mm)."
            ),
            polylines=[
                [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
            ],
        )
    ]


# --- Check 4: material near the centroid ------------------------------------


def _max_inscribed_radius(geom, upper_bound: float) -> float:
    """Largest circle that fits inside `geom`, by bisection on a negative
    buffer. Used to tell a solid blob from thin walls crossing the band."""
    if geom.is_empty or upper_bound <= 0:
        return 0.0
    low, high = 0.0, upper_bound
    for _ in range(18):
        mid = (low + high) / 2.0
        if geom.buffer(-mid).is_empty:
            high = mid
        else:
            low = mid
    return low


def _core_material_suggestions(
    moments: SectionMoments, poly: Polygon, typical_wall: float | None
) -> list[Suggestion]:
    """Mass sitting near the neutral axis that isn't buying stiffness.

    Only worth saying if the material there could actually be removed,
    which takes one of two forms: the profile is a bulk bar (hole-free and
    filling most of its envelope, so there's obviously solid stock in the
    middle), or it's a walled profile whose core is markedly chunkier than
    the walls the design already proves are sufficient. Without that
    guard this fires on things like a plain L-angle, where a third of the
    area does sit near the centroid but it's a 10 mm leg with nothing to
    hollow."""
    minx, miny, maxx, maxy = poly.bounds
    width, height = maxx - minx, maxy - miny
    bbox_area = width * height
    is_bulk_bar = not list(poly.interiors) and bbox_area > 0 and moments.area / bbox_area >= CORE_SOLIDITY
    candidates = []

    for axis, depth, total_i, i_label, load_dir in (
        ("x", height, moments.ixx, "Ixx", "Y-direction"),
        ("y", width, moments.iyy, "Iyy", "X-direction"),
    ):
        if depth <= 0 or total_i <= 0:
            continue
        half = depth / 6.0  # the middle third of the profile's depth on this axis
        if axis == "x":
            band = box(minx - 1.0, moments.cy - half, maxx + 1.0, moments.cy + half)
        else:
            band = box(moments.cx - half, miny - 1.0, moments.cx + half, maxy + 1.0)

        core = poly.intersection(band)
        if core.is_empty:
            continue
        area, _, sy, ixx_o, iyy_o = _geometry_moments(core)
        if area <= 0:
            continue
        # Second moment of the band's material about the *section's*
        # centroid, via the parallel-axis relation.
        if axis == "x":
            core_i = ixx_o - 2.0 * moments.cy * sy + moments.cy**2 * area
        else:
            _, sx, _, _, _ = _geometry_moments(core)
            core_i = iyy_o - 2.0 * moments.cx * sx + moments.cx**2 * area

        area_fraction = area / moments.area
        inertia_fraction = core_i / total_i
        if area_fraction < CORE_AREA_FRACTION or inertia_fraction > CORE_INERTIA_FRACTION:
            continue
        thickness = 2.0 * _max_inscribed_radius(core, min(width, height) / 2.0)
        if thickness < CORE_MIN_THICKNESS_MM:
            continue  # nothing there thick enough to core out and still leave walls
        chunkier_than_walls = typical_wall is not None and thickness >= CORE_WALL_MULTIPLE * typical_wall
        if not (is_bulk_bar or chunkier_than_walls):
            continue  # thin walls crossing the band, not a blob that can be cored
        candidates.append((area_fraction, inertia_fraction, thickness, band, i_label, load_dir, axis, half))

    if not candidates:
        return []

    area_fraction, inertia_fraction, thickness, band, i_label, load_dir, axis, half = max(
        candidates, key=lambda c: c[0]
    )
    span = "height" if axis == "x" else "width"
    bminx, bminy, bmaxx, bmaxy = band.bounds
    clipped = box(max(bminx, minx), max(bminy, miny), min(bmaxx, maxx), min(bmaxy, maxy))

    return [
        Suggestion(
            kind="core_material",
            title=f"{area_fraction:.0%} of the area sits in the middle third of the {span}, contributing {inertia_fraction:.0%} of {i_label}",
            detail=(
                f"Material this close to the neutral axis earns very little: contribution to "
                f"bending goes with the square of the distance from it, so this band carries "
                f"{area_fraction:.0%} of the section's mass for {inertia_fraction:.0%} of "
                f"{i_label}. It is {_fmt(thickness, 1)} mm thick at its widest, which leaves room "
                "to core it out (or replace it with ribs) and still hold a manufacturable wall on "
                f"each side. If {load_dir} loads dominate, that is close to free mass saving; "
                "keep enough web to carry shear and to stop the remaining flanges buckling."
            ),
            polylines=[
                [
                    (clipped.bounds[0], clipped.bounds[1]),
                    (clipped.bounds[2], clipped.bounds[1]),
                    (clipped.bounds[2], clipped.bounds[3]),
                    (clipped.bounds[0], clipped.bounds[3]),
                    (clipped.bounds[0], clipped.bounds[1]),
                ]
            ],
        )
    ]


# --- Entry point ------------------------------------------------------------


def generate_suggestions(
    vertices: list[Vertex], holes: list[list[Vertex]] | None = None
) -> list[Suggestion]:
    """Every suggestion the geometry actually supports, most
    manufacturing-critical first, capped at MAX_SUGGESTIONS."""
    if len(vertices) < 3:
        raise ValueError("A profile needs at least 3 vertices")

    poly = _build_polygon(vertices, holes)
    if not poly.is_valid:
        raise ValueError("Profile polygon is self-intersecting or otherwise invalid")
    rings = _normalized_rings(vertices, holes)
    moments = polygon_moments(vertices, holes)

    minx, miny, maxx, maxy = poly.bounds
    span = math.hypot(maxx - minx, maxy - miny)
    step = max(0.4, span / 300.0)
    samples = _wall_samples(poly, rings)

    # A "typical wall" only means something on a walled profile. On a solid
    # bar the measurement just returns the bar's own dimensions, and
    # comparing those to each other would flag the long axis of every plain
    # rectangle as a heavy wall.
    typical_wall: float | None = None
    if samples:
        candidate = _weighted_median([s.thickness for s in samples], [s.run for s in samples])
        if 0 < candidate < THIN_WALLED_PROFILE_FRACTION * min(maxx - minx, maxy - miny):
            typical_wall = candidate

    suggestions: list[Suggestion] = []
    if typical_wall is not None:
        suggestions += _wall_thickness_suggestions(samples, typical_wall, step)
    suggestions += _sharp_corner_suggestions(rings, moments.area)
    suggestions += _distribution_suggestions(moments, poly)
    suggestions += _core_material_suggestions(moments, poly, typical_wall)

    suggestions.sort(key=lambda s: _KIND_ORDER.index(s.kind) if s.kind in _KIND_ORDER else 99)
    return suggestions[:MAX_SUGGESTIONS]


def as_dicts(suggestions: list[Suggestion]) -> list[dict[str, Any]]:
    return [
        {
            "kind": s.kind,
            "title": s.title,
            "detail": s.detail,
            "ring": s.ring,
            "vertex_indices": s.vertex_indices,
            "points": [tuple(p) for p in s.points],
            "polylines": [[tuple(p) for p in line] for line in s.polylines],
        }
        for s in suggestions
    ]
