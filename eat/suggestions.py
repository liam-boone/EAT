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

1. Wall thickness, in both directions. Local thickness is sampled along
   the whole boundary (every ~0.4mm) by `eat.thickness`, which measures
   the diameter of the largest circle that fits inside the profile and
   touches the boundary at that point. That is a medial-axis measure, not
   a ray cast -- see that module for why the ray cast was abandoned and
   why the inscribed circle cannot make the same mistake. The profile's
   "typical wall" is the length-weighted median of the sweep, so it is
   the thickness of the typical millimetre of wall rather than of the
   typical face.

   Thin is flagged below 0.6x the typical wall, or below 1.0mm outright,
   which is about the practical floor for filling 6xxx aluminium.

   Thick is flagged above 2.0x the typical wall. Extrusion design practice
   puts the workable limit on thickness variation within one profile at
   roughly 2:1: past that the thin sections fill and freeze while the
   thick ones are still moving, so the die cannot be balanced to run the
   profile straight, and the thick section sets the extrusion speed for
   the whole part. Thick metal near the neutral axis is a separate
   concern with a separate check (5) -- this one is about making the part.

   Three guards keep both halves honest.

   * The measurement has to be of a *wall*: the circle's far contact must
     land on a face within 30 degrees of parallel with the one it started
     from (`eat.thickness`'s opposition angle). A circle sitting in a
     corner or a rib junction touches a face square-on to its own, and its
     diameter -- while a perfectly true width of material -- is not a wall
     thickness in the sense this check is about. Without this, every
     convex corner reads as a knife-edge (thickness genuinely goes to zero
     at a sharp corner) and every junction reads as a heavy wall.
   * A finding has to persist along the boundary: 2x the typical wall for
     thin, 3x for thick. Thick readings bleed out of corners -- the
     inscribed circle at the end of a wall, where it meets another wall,
     is legitimately larger than either wall -- so the thick half needs
     more persistence before it is describing a wall rather than a
     junction.
   * The whole check is skipped unless the typical wall is under a quarter
     of the profile's smaller bounding-box dimension: a solid bar has no
     walls, and its two dimensions aren't a uniformity problem.

   Measured on the test profiles, wall-like thickness peaks at 1.14x
   typical on the L-angle and the bad box, 1.41x on the filleted tube and
   1.95x on the KJN (a real profile, in production) -- and none of them
   has a single millimetre of qualifying boundary above 2.0x. The
   deliberately heavy-walled fixture peaks at 2.53x with 57mm of it, and
   an 8mm-on-4mm wall sitting exactly at the 2:1 practice limit stays
   quiet. So the separation here is not a knife-edge on the ratio; it is
   0mm versus 57mm of run against a 12mm bar.

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

   That face-size filter cannot see a narrow, deep notch cut into an
   otherwise clean wall, because one face of a notch is short by
   definition however deep it goes. That is left exactly as it is -- the
   filter separates the tested cases cleanly on min-face/sqrt(area) (KJN
   0.12, all 28 corners correctly dropped; bad-box 0.87 and L-angle 1.27,
   both correctly kept, a 7x gap around the threshold) and narrowing it
   would wreck that. Notches are caught by check 3 instead, off a
   different signal entirely.

3. Notches that cut past the minimum wall. A slot cut into a wall is a
   stress riser at its root and a fragile tongue in the die, and it is
   invisible to both of the checks above: too short a run to be a thin
   wall, too short a face to be a sharp corner. It is, however,
   unmistakable in the thickness sweep. Walking the boundary across a
   slot, the wall reads its full thickness, the slot's two side faces read
   as corners and drop out, and the slot ROOT reads the metal left behind
   it -- a wall-like reading, square to the far face, over only the root's
   own width.

   Finding that shape is easy. Deciding it is a MISTAKE is not, and that
   governs how this check is scoped. A functional T-slot lip, a retention
   hook root and an accidental slit are the same geometry; what separates
   them is intent, which is not in the vertex data. Measured on the seven
   real supplier drawings in the project root, a contrast-only test
   (a local minimum at least 2x thinner than the wall either side,
   recovering nearby on both sides) produces 67, 75, 15, 24 and 8
   candidates on five of them -- every one a slot mouth, a hook root or a
   web between two cells, all of them drawn on purpose. Five different
   discriminators were tried against that and none separates: root width
   vs depth (real slots reach aspect 17.8 against the test notch's 2.0),
   root thickness vs the profile's own wall, the depth ratio itself
   (a real slot sits at 2.94 against a threshold that would have to be 3),
   how far the wall recovers, and whether the far face runs straight past
   the cut (a real slot lip scores 2.10 against the test notch's 2.06).
   They do not separate because there is nothing to separate: the shapes
   are the same.

   So this check does not try to guess intent. It asks a manufacturing
   question instead, which the geometry CAN answer: does the notch cut
   past what the profile can be made from? A root is flagged only when
   what it leaves is under the ~1.0mm practical minimum wall -- the same
   floor check 1 uses -- and it is otherwise identical to a thin-wall
   finding except for being too short to persist. That is exactly the gap
   check 1 leaves, and nothing more is claimed.

   The contrast test is still required on top of the floor, because
   without it the check would just be check 1 with the run guard removed
   and would fire on every knife-edge and tip. A candidate must be at
   least 2x thinner than the wall on BOTH sides, with that wall coming
   back within 2.5 wall thicknesses of travel each way. Recovery on both
   sides separates a cut from a step (metal that thins and stays thin is a
   wall change, and has its own check). Requiring the recovery to be
   nearby separates it from a long thin wall, and self-scales: a notch
   cannot be deeper than the wall it is cut into, so root-to-full-wall is
   bounded by roughly 1.5x that wall whatever size the profile is.

   Measured: every constructed notch leaving under 1mm fires; the
   thinnest notch-shaped candidate on any of the seven real drawings is
   1.476mm, so all seven stay silent with 1.85x of margin, as do the KJN,
   the L-angle, the bad box and both tubes.

   KNOWN LIMITATION (accepted, and a real one): a slot that leaves 1.5mm
   in a 4mm wall is a genuine stress riser and this will not report it,
   because it is indistinguishable from the functional slots on every real
   profile tested. Raising the sensitivity to catch it means 15-75
   findings on a production drawing, which is worse than saying nothing.
   Do not "fix" this by lowering the floor without new evidence that
   separates designed slots from accidental ones -- five geometric
   discriminators have already been tried and failed.

   The measurement is local throughout -- the surrounding wall is whatever
   is actually either side of the root, not the profile's median -- so
   this check needs no walled-profile gate and will find a keyway cut in a
   solid bar as readily as a slot in a 1.5mm extrusion.

   The 30-degree opposition filter does useful double duty here. For a
   V-notch it is a narrowness test in its own right: the root of a narrow
   V faces the far wall almost square-on and reads, while a groove opened
   out past about 60 degrees included stops reading -- which is right,
   because by then it is a chamfer, not a notch.

   DEDUPLICATION: a notch overlapping an emitted thin-wall finding is
   suppressed. If the reduced section persists far enough to read as a
   wall in its own right then the manufacturing problem dominates, the
   thin-wall finding already names the same metal, and the fix it asks for
   (thicken it) removes the stress riser too -- so the second finding adds
   nothing actionable and costs a slot in a deliberately short list. The
   two are nearly disjoint by construction anyway: a thin wall has to
   persist over 2x the typical wall, a notch has to recover within 2.5x,
   and only a feature in that narrow overlap can trip both -- a wide
   flat-bottomed groove is the case that does, and the thin wall wins it.

4. Material distribution vs. the bending axes. Raw Ixx/Iyy anisotropy is
   mostly just the envelope talking -- any profile in a 40x20 box is
   about 4x stiffer one way, and saying so isn't a suggestion. What *is*
   actionable is being more lopsided than the envelope requires, so the
   actual Iyy/Ixx ratio is compared against (W/H)^2, the ratio a solid
   rectangle of the same bounding box would have, and flagged only at
   1.5x off that.

5. Material near the centroid. Area within the middle third of the
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

from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

from eat.profile import validate_profile
from eat.thickness import (
    ThicknessSample,
    normalized_rings,
    ordered_by_ring,
    sample_step,
    sample_thickness,
    wall_like as _wall_like,
    weighted_median,
)

Vertex = tuple[float, float]

# --- Thresholds (see module docstring for the reasoning behind each) ---------

MIN_PRACTICAL_WALL_MM = 1.0  # below this is hard to fill in 6xxx aluminium at any size
THIN_WALL_FRACTION = 0.6  # flag walls under 60% of the profile's typical wall
THICK_WALL_MULTIPLE = 2.0  # ...and over 200% of it. Extrusion practice puts the workable
# thickness-variation limit within one profile at about 2:1; past that the die cannot be
# balanced to fill the thin and thick sections at the same rate.
THIN_WALLED_PROFILE_FRACTION = 0.25  # typical wall must be under this share of the smaller
# bounding-box dimension for wall-uniformity analysis to mean anything at all: a solid bar's
# "wall" is its own width, and comparing its two dimensions to each other says nothing.
WALL_RUN_THICKNESS_MULTIPLE = 2.0  # a thin finding must persist this far along the boundary;
# below that it's a corner or rib junction, not a wall
THICK_RUN_THICKNESS_MULTIPLE = 3.0  # ...and a thick one this far, because a thick reading
# bleeds out of every corner (the circle where two walls meet is bigger than either wall)
SHARP_CORNER_MAX_OPEN_ANGLE_DEG = 100.0  # <= this on the open side is a sharp internal corner
SHARP_CORNER_MIN_FACE_FRACTION = 0.20  # both faces meeting at the corner must be at least this
# share of sqrt(section area), so the corner joins two primary faces rather than a functional
# detail. Measured on the test profiles: KJN 0.12 (all 28 slot/keyway corners dropped),
# bad-box 0.87, L-angle 1.27 -- a 7x gap around the threshold, so it is not knife-edge.
# KNOWN LIMITATION (v1, accepted): this intentionally misses a narrow, deep notch cut into an
# otherwise clean wall, since one face of a notch is short however deep it goes. See the
# module docstring; catching those needs a different test, not a smaller number here.
MIN_CORNER_EDGE_MM = 0.3  # shorter neighbours than this means arc-approximation noise
NOTCH_DEPTH_RATIO = 2.0  # the wall either side of a notch root must be at least this many times
# it, which is what makes the reading a cut rather than a wall. Not the check's sensitivity knob:
# that is MIN_PRACTICAL_WALL_MM, shared with check 1, and the docstring explains at length why a
# contrast threshold cannot be the thing that decides. Two is where ordinary wall-to-wall
# variation stops -- check 1 shows it topping out at 1.4x on a clean profile, 1.95x on a real one.
NOTCH_REACH_MULTIPLE = 2.5  # ...and must come back within this many of ITS OWN thicknesses of
# boundary, on both sides. A notch cannot be deeper than the wall it is cut into, so the travel
# from root to full wall is ~1.5x that wall however big the profile is; 2.5x leaves margin
# without admitting a long thin wall, which recovers only at its far end.
NOTCH_MIN_DEPTH_MM = 0.5  # below half a millimetre of metal removed it is drawing tolerance and
# arc-approximation noise, not a feature anyone cut on purpose
NOTCH_SAME_FEATURE_MULTIPLE = 1.0  # two notch readings closer together than the wall they are cut
# into are one feature. Usually that is one slot read twice: once at its own root, and once from
# the face opposite, whose inscribed circle the slot also pinches from the side. Two real cuts
# that close together are one problem as well -- the metal between them is what fails.
MAX_NOTCH_FINDINGS = 2  # they cluster; naming the two worst is enough to act on
ANISOTROPY_ENVELOPE_FACTOR = 1.5  # how much worse than the envelope before it's worth saying
CORE_AREA_FRACTION = 0.25  # band must hold at least this share of the area
CORE_INERTIA_FRACTION = 0.10  # ...while contributing no more than this share of I
CORE_MIN_THICKNESS_MM = 6.0  # ~4x a 1.5mm wall: room to core out and leave walls both sides
CORE_SOLIDITY = 0.6  # area/bounding-box area at or above which a hole-free profile is a bulk bar
CORE_WALL_MULTIPLE = 1.5  # ...or, for a walled profile, the band must be this much chunkier
# than the walls the design already proves are enough
MAX_SUGGESTIONS = 6  # keep the list short enough to actually read

# Emitted in this order, most manufacturing-critical first.
_KIND_ORDER = [
    "thin_wall",
    "thick_wall",
    "narrow_notch",
    "sharp_corner",
    "material_distribution",
    "core_material",
]


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
    rings = normalized_rings(vertices, holes)
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


def _build_polygon(vertices: list[Vertex], holes: list[list[Vertex]] | None) -> Polygon:
    return orient(Polygon(vertices, holes or None), sign=1.0)


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def _pt(p: Vertex) -> str:
    return f"({_fmt(p[0])}, {_fmt(p[1])})"


# --- Check 1: wall thickness ------------------------------------------------


def _cluster_samples(samples: list[ThicknessSample], step: float) -> list[list[ThicknessSample]]:
    """Group flagged samples that describe the same physical wall.

    Clustering is on the midpoint of each measured through-thickness
    segment, not on the boundary point, which means the two faces of one
    wall land on the same centre-line and merge into a single finding
    instead of being reported once from each side."""
    clusters: list[list[ThicknessSample]] = []
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


def _ring_runs(cluster: list[ThicknessSample]) -> dict[str, float]:
    per_ring: dict[str, float] = {}
    for sample in cluster:
        per_ring[sample.ring] = per_ring.get(sample.ring, 0.0) + sample.run
    return per_ring


def _cluster_run(cluster: list[ThicknessSample]) -> float:
    """Boundary length this finding spans, counted per ring so that a wall
    measured from both of its faces isn't double-counted."""
    return max(_ring_runs(cluster).values())


def _representative(cluster: list[ThicknessSample]) -> ThicknessSample:
    """The sample a thick finding should quote and point at: the
    length-weighted median thickness, at the middle of the stretch that
    reads it, on whichever face contributes most of the run.

    The thin half of the check quotes its extreme instead, and the
    asymmetry is deliberate. The thinnest point of a thin wall is the
    design fact -- that is where it stops filling. The thickest point of a
    thick wall usually isn't: thickness rises at the ends of any wall,
    where the inscribed circle starts to see around the corner into the
    wall it joins, so the extreme overstates the wall by whatever its
    corner radii happen to be and points at a corner rather than at the
    offending face. The median is the thickness someone would measure with
    a caliper."""
    ring = max(_ring_runs(cluster).items(), key=lambda kv: kv[1])[0]
    on_ring = [s for s in cluster if s.ring == ring] or cluster
    median = weighted_median([s.thickness for s in on_ring], [s.run for s in on_ring])
    at_median = [
        s for s in on_ring if abs(s.thickness - median) <= 1e-9 * max(median, 1.0)
    ] or on_ring
    at_median.sort(key=lambda s: s.s)
    return at_median[len(at_median) // 2]


def _wall_thickness_suggestions(
    samples: list[ThicknessSample], typical: float, step: float
) -> list[Suggestion]:
    """Both halves of wall uniformity, off one sweep: walls too thin to
    fill, and walls too thick for the die to stay balanced around them.
    Only wall-like samples take part -- see `_wall_like`."""
    walls = _wall_like(samples)
    if typical <= 0 or len(walls) < 2:
        return []

    suggestions: list[Suggestion] = []

    # --- too thin ---
    thin = [
        s
        for s in walls
        if s.thickness < THIN_WALL_FRACTION * typical or s.thickness < MIN_PRACTICAL_WALL_MM
    ]
    thin_min_run = WALL_RUN_THICKNESS_MULTIPLE * typical
    thin_clusters = [c for c in _cluster_samples(thin, step) if _cluster_run(c) >= thin_min_run]
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

    # --- too thick ---
    thick = [s for s in walls if s.thickness > THICK_WALL_MULTIPLE * typical]
    thick_min_run = THICK_RUN_THICKNESS_MULTIPLE * typical
    thick_clusters = [c for c in _cluster_samples(thick, step) if _cluster_run(c) >= thick_min_run]
    thick_clusters.sort(key=lambda c: -max(s.thickness for s in c))
    for cluster in thick_clusters[:2]:
        worst = _representative(cluster)
        run = _cluster_run(cluster)
        ratio = worst.thickness / typical
        suggestions.append(
            Suggestion(
                kind="thick_wall",
                title=f"Heavy wall — {_fmt(worst.thickness, 2)} mm near {_pt(worst.point)}, {ratio:.1f}x the typical wall",
                detail=(
                    f"The wall here measures {_fmt(worst.thickness, 2)} mm through the section "
                    f"over about {_fmt(run, 1)} mm of boundary, against {_fmt(typical, 2)} mm for "
                    f"the profile's typical wall — {ratio:.1f}x. Thickness variation much beyond "
                    "2:1 within one profile is hard to extrude well: metal moves faster through "
                    "the thick section and the thin ones fill and freeze first, so the die has to "
                    "be worked to balance it and the profile still tends to come out bowed or "
                    "twisted. The thick section also dictates the quench rate and the extrusion "
                    "speed for the whole part, and pulls in as it cools. If the metal is there "
                    "for strength, a rib or a pair of thinner walls usually buys more stiffness "
                    f"per kilo; if it isn't, taking it down toward {_fmt(typical, 2)} mm is free "
                    "weight and cycle time. Where it has to change, taper into it rather than "
                    "stepping."
                ),
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


# --- Check 3: narrow deep notches -------------------------------------------


def _wall_samples_by_ring(samples: list[ThicknessSample]) -> dict[str, list[ThicknessSample]]:
    """Wall-like samples grouped by ring and put in boundary order.
    Corner samples are dropped before ordering, which is what lets the
    walk step straight over a notch's two side faces (both of which read
    as corners) and land on the wall the notch is cut into."""
    return ordered_by_ring(_wall_like(samples))


def _walk_to_recovery(
    ordered: list[ThicknessSample], start: int, need: float, give_up: float, direction: int
) -> tuple[ThicknessSample | None, float]:
    """Walk one way around the ring from `ordered[start]` until the wall
    comes back up to `need`, returning that sample and how far along the
    boundary it was. Gives up past `give_up`: a wall that only recovers a
    long way off was never notched, it just gets thinner."""
    count = len(ordered)
    ring_length = ordered[start].ring_length
    origin = ordered[start].s
    for k in range(1, count):
        sample = ordered[(start + direction * k) % count]
        gap = (
            (sample.s - origin) % ring_length
            if direction > 0
            else (origin - sample.s) % ring_length
        )
        if gap > give_up:
            return None, gap
        if sample.thickness >= need:
            return sample, gap
    return None, float("inf")


def _notch_suggestions(
    samples: list[ThicknessSample], step: float, already_reported: list[Suggestion]
) -> list[Suggestion]:
    """Slots cut into a wall, found as local minima of the thickness sweep
    that recover to a much thicker wall on both sides within a short
    distance. See the module docstring for why those two conditions are
    the whole test, and for the deduplication rule applied at the end."""
    surrounding_by_sample: dict[ThicknessSample, float] = {}

    for ordered in _wall_samples_by_ring(samples).values():
        if len(ordered) < 3:
            continue
        thickest = max(s.thickness for s in ordered)
        give_up = NOTCH_REACH_MULTIPLE * thickest  # nothing can recover further than this
        for i, sample in enumerate(ordered):
            if sample.thickness >= MIN_PRACTICAL_WALL_MM:
                continue  # what it leaves is still a manufacturable wall -- see the docstring
            need = NOTCH_DEPTH_RATIO * sample.thickness
            if need > thickest:
                continue  # no wall on this ring is thick enough for this to be a notch in
            ahead, gap_ahead = _walk_to_recovery(ordered, i, need, give_up, +1)
            if ahead is None:
                continue
            behind, gap_behind = _walk_to_recovery(ordered, i, need, give_up, -1)
            if behind is None:
                continue  # recovers on one side only: a step in the wall, not a cut into it
            surrounding = min(ahead.thickness, behind.thickness)
            if max(gap_ahead, gap_behind) > NOTCH_REACH_MULTIPLE * surrounding:
                continue  # too far back to full wall: a thinning, not a notch
            if surrounding - sample.thickness < NOTCH_MIN_DEPTH_MM:
                continue
            surrounding_by_sample[sample] = surrounding

    if not surrounding_by_sample:
        return []

    # One finding per notch, not one per sample across its root.
    clusters = _cluster_samples(list(surrounding_by_sample), step)
    clusters.sort(key=lambda c: min(s.thickness for s in c))

    claimed: list[Vertex] = []
    for other in already_reported:
        for line in other.polylines:
            claimed.extend(line)
        claimed.extend(other.points)

    suggestions: list[Suggestion] = []
    emitted: list[tuple[Vertex, float]] = []  # (root point, surrounding wall)
    for cluster in clusters:
        root = min(cluster, key=lambda s: s.thickness)
        surrounding = surrounding_by_sample[root]
        depth = surrounding - root.thickness
        width = _cluster_run(cluster)

        # DEDUPLICATION (see module docstring): if this metal has already
        # been reported as a thin wall, that finding covers it. Both
        # tolerances scale with the wall the notch is cut into, not with
        # what is left at its root -- the feature's size is the wall's.
        tol = max(2.0 * step, surrounding)
        if any(math.hypot(cx - root.point[0], cy - root.point[1]) <= tol for cx, cy in claimed):
            continue
        # ...and the same notch read from the far side of the wall is not
        # a second notch. Clusters arrive deepest-first, so the reading
        # kept is the one at the actual root.
        if any(
            math.hypot(px - root.point[0], py - root.point[1])
            <= NOTCH_SAME_FEATURE_MULTIPLE * max(surrounding, other_wall)
            for (px, py), other_wall in emitted
        ):
            continue
        emitted.append((root.point, surrounding))
        suggestions.append(
            Suggestion(
                kind="narrow_notch",
                title=(
                    f"Narrow notch — {_fmt(depth, 2)} mm deep at {_pt(root.point)}, "
                    f"leaving {_fmt(root.thickness, 2)} mm of a {_fmt(surrounding, 2)} mm wall"
                ),
                detail=(
                    f"The wall reads {_fmt(surrounding, 2)} mm either side of this and "
                    f"{_fmt(root.thickness, 2)} mm at the root, over about {_fmt(width, 2)} mm of "
                    f"boundary — a cut {_fmt(depth, 2)} mm deep taking "
                    f"{depth / surrounding:.0%} of the wall, over too short a stretch for the "
                    "wall-thickness check to see it and between faces too short for the corner "
                    f"check. What it leaves is under the ~{_fmt(MIN_PRACTICAL_WALL_MM, 1)} mm "
                    "practical minimum wall for extruded aluminium, so this is not just a thin "
                    "spot: it may not fill reliably at all, and the matching feature in the die is "
                    "a thin tongue standing proud into the flow, which wears fast and chips. It is "
                    "a stress riser on top of that — the root concentrates stress the way an "
                    "unfilleted corner does, and the metal behind it carries the full wall's load "
                    "through a fraction of the section. If the slot is functional, take it back to "
                    f"leave at least {_fmt(MIN_PRACTICAL_WALL_MM, 1)} mm and put the largest radius "
                    "you can in its root — even 0.3-0.5 mm transforms the stress concentration. If "
                    "it isn't functional, it is the cheapest thing on the profile to delete."
                ),
                ring=root.ring,
                vertex_indices=sorted({s.index for s in cluster if s.ring == root.ring}),
                points=[root.point],
                polylines=[[s.point for s in cluster if s.ring == root.ring]],
            )
        )
        if len(suggestions) >= MAX_NOTCH_FINDINGS:
            break

    return suggestions


# --- Check 4: material distribution vs. the bending axes --------------------


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


# --- Check 5: material near the centroid ------------------------------------


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
    # Same contract as the section engine -- see eat.profile for why this
    # is not a bare polygon.is_valid test, and why all three engines now
    # share one definition instead of three.
    validate_profile(vertices, holes)

    poly = _build_polygon(vertices, holes)
    rings = normalized_rings(vertices, holes)
    moments = polygon_moments(vertices, holes)

    minx, miny, maxx, maxy = poly.bounds
    step = sample_step(poly)
    samples = sample_thickness(poly, rings)

    # A "typical wall" only means something on a walled profile, and it is
    # measured over the samples that are actually crossing a wall. On a
    # solid bar the measurement just returns the bar's own dimensions, and
    # comparing those to each other would flag the long axis of every plain
    # rectangle as a heavy wall.
    typical_wall: float | None = None
    walls = _wall_like(samples)
    if walls:
        candidate = weighted_median([s.thickness for s in walls], [s.run for s in walls])
        if 0 < candidate < THIN_WALLED_PROFILE_FRACTION * min(maxx - minx, maxy - miny):
            typical_wall = candidate

    suggestions: list[Suggestion] = []
    if typical_wall is not None:
        suggestions += _wall_thickness_suggestions(samples, typical_wall, step)
    # The notch check is deliberately not gated on `typical_wall`: it reads
    # the wall either side of each candidate rather than the profile's
    # median, so it means something on a solid bar too.
    suggestions += _notch_suggestions(samples, step, suggestions)
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
