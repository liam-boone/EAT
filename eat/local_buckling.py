"""
EAT local (plate) buckling — per-wall slenderness checks for extruded
sections, reported alongside the global Euler result rather than instead
of it. The two answer different questions: Euler asks whether the MEMBER
buckles as a column, this asks whether an individual WALL of the section
buckles as a plate first, at a load the column check would pass.

METHOD AND SOURCES
------------------
Each flat wall of the profile is treated as a long rectangular plate of
width b (across the section) and thickness t, uniformly compressed along
the extrusion axis, with its two LONGITUDINAL edges -- the two ends of
the wall in the cross-section -- either supported by the walls that join
there or free. Its elastic critical stress is the classical plate result
(Timoshenko & Gere, *Theory of Elastic Stability*; the same expression
underlies both design codes below):

    sigma_cr = k * pi^2 * E / (12 * (1 - nu^2)) * (t / b)^2

with the long-plate buckling coefficients

    k = 4.0    both longitudinal edges simply supported ("internal" part)
    k = 0.425  one edge simply supported, one free ("outstand")

Both coefficients are VERIFIED FROM FIRST PRINCIPLES in
`eat/verify_local_buckling.py` rather than taken on trust: it discretizes
the plate buckling eigenvalue problem and solves it, recovering 3.99998
and 0.4258 respectively. Simple-support (rather than fixed) edges are the
standard conservative assumption -- real junctions offer some rotational
restraint, so the true k is higher and this under-predicts capacity.

Alongside that, each wall is classified per EN 1999-1-1 (Eurocode 9)
Table 6.2, which is the code-calibrated answer and the one to design to.
The slenderness parameter for a flat part in uniform compression is
beta = b/t, compared against beta_1, beta_2, beta_3 = (limit) * epsilon
with epsilon = sqrt(250 / f_o), f_o the 0.2% proof stress in N/mm^2:

                        beta1/eps  beta2/eps  beta3/eps
    internal part          11         16         22
    outstand part           3        4.5          6

Class 1-3 means the wall can reach f_o without buckling; Class 4 means
local buckling governs first and the section is not fully effective.
Those are the class A, UNWELDED rows -- the usual case for a plain
extrusion. Welded parts and buckling-class-B alloys have tighter limits
(internal 9/13/18 and 13/16.5/18 respectively), so a welded assembly
wants checking against the standard directly; this is stated in every
segment's caveats rather than guessed at.

THE YIELD CAP
-------------
Perfect-plate theory has no upper bound, so a stocky wall's elastic
critical stress comes out far above anything the material can reach -- a
4mm wall on a 60x40 tube in 6063-T6 gives 2,900 MPa against a 214 MPa
proof stress. An "elastic safety factor" of 14 built on that is a true
statement about plate buckling and a misleading statement about the wall,
which would have yielded at a factor of 1.
So each wall carries TWO factors, reported side by side and labelled:

    safety_factor           sigma_cr / sigma_applied      (pure buckling)
    effective_safety_factor min(sigma_cr, f_o) / sigma_applied

The effective one is the wall's real limit and is what `_governing`
ranks on; the elastic one is kept because it is the answer to "how close
is this wall to buckling", which is a different question and still worth
seeing. `yield_governed` says which of the two bound.

Note the two CRITERIA below are not the same and are not meant to be. The
elastic formula is perfect-plate theory; EN 1999-1-1's limits are
calibrated to test data and so allow for imperfections and residual
stress. For 6063-T6 the elastic formula puts the yield-vs-buckling
crossover for an internal part at b/t = 34.8, while Eurocode 9 puts the
Class 3/4 boundary at b/t = 23.8 -- the code is ~1.5x more conservative
in b/t, which is the usual allowance. Both are reported. The CLASS is the
one to design to; the elastic stress is what gives a safety factor a
number to divide.

WHAT IS ASSUMED, AND WHEN IT REFUSES TO ANSWER
----------------------------------------------
Uniform compression across each wall. Real bending puts a stress gradient
across a web, which raises its buckling coefficient a long way (k = 23.9
for a web in pure bending against 4.0 in uniform compression), so
treating every wall as uniformly compressed is conservative for webs and
exact for flanges at the extreme fibre.

A wall is only given a number when its geometry actually fits the model.
Segments are flagged `uncertain` -- reported, with slenderness, but with
no k, no critical stress and no class -- when the wall is tapered, when
its two faces are not parallel, when only one face could be resolved,
when an end cannot be classified as clearly supported or clearly free, or
when both ends come back free (which is not a plate at all). See
`_classify_ends`.

Applied stress per wall is taken as the worst compressive fibre stress
anywhere on that wall: |N|/A + |M| * d_max / I, with d_max the wall's
greatest distance from the relevant centroidal axis. Using the wall's own
d_max rather than the section's keeps a web near the neutral axis from
being judged against a flange's stress, while the sign-free treatment of
M stays conservative.

Run against a profile:

    from eat.local_buckling import analyze_local_buckling
    result = analyze_local_buckling(vertices, holes, material)
    print(result.summary())
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from eat.profile import validate_profile
from eat.thickness import (
    ThicknessSample,
    normalized_rings,
    ordered_by_ring,
    sample_step,
    sample_thickness,
    wall_like,
)

Vertex = tuple[float, float]

# --- Plate buckling coefficients (long plate, uniform compression) -----------
# Verified numerically in eat/verify_local_buckling.py.
K_INTERNAL = 4.0  # both longitudinal edges simply supported
K_OUTSTAND = 0.425  # one edge simply supported, one free

# --- EN 1999-1-1 Table 6.2, buckling class A, unwelded -----------------------
EC9_INTERNAL_LIMITS = (11.0, 16.0, 22.0)  # beta/epsilon for classes 1, 2, 3
EC9_OUTSTAND_LIMITS = (3.0, 4.5, 6.0)
EC9_WELDED_INTERNAL_LIMITS = (9.0, 13.0, 18.0)  # quoted in caveats only
EC9_WELDED_OUTSTAND_LIMITS = (2.5, 4.0, 5.0)

# --- Segmentation tolerances -------------------------------------------------
STRAIGHT_FACE_MAX_TURN_DEG = 6.0  # a single step may kink this much before it is a corner
FACE_MAX_DEVIATION_DEG = 8.0  # ...and the face may wander this far from where it STARTED.
# The cumulative test is the one that matters: an arc is approximated by chords whose individual
# turns shrink as the approximation gets finer -- a 90-degree fillet in 16 steps turns 5.6 degrees
# at a time, under any per-step limit loose enough to tolerate a flat face -- so only accumulated
# deviation reliably tells a fillet from a flat wall, whatever the drawing's chord tolerance.
FACE_THICKNESS_TOLERANCE = 0.08  # ...and thickness this much, fractionally
PARALLEL_FACES_MAX_DEG = 10.0  # the two faces of a plate, measured against each other
MIN_SEGMENT_SAMPLES = 4  # below this there is no run to fit a centreline to
END_PROBE_THICKNESSES = 1.5  # how far past a wall's end to look for continuing material
JUNCTION_TOLERANCE_THICKNESSES = 1.5  # how close another wall's end must be to count as joining
MIN_PLATE_ASPECT = 1.0  # b/t below this is junction metal, not a wall element
JUNCTION_SEARCH_THICKNESSES = 4.0  # how far to look for the wall this one joins
MAX_JUNCTION_EXTENSION_GAPS = 1.6  # ...and how far past the neighbour the crossing may sit


@dataclass(frozen=True)
class PlateSegment:
    """One flat wall of the profile, treated as a plate element."""

    index: int
    ring: str
    width: float  # b, mm -- across the section, between the wall's two longitudinal edges
    thickness: float  # t, mm
    slenderness: float  # b/t
    support: str  # "internal" | "outstand" | "uncertain"
    start: Vertex  # centreline ends
    end: Vertex
    k: float | None
    elastic_critical_stress: float | None  # MPa
    beta_over_epsilon: float | None
    section_class: int | None  # EN 1999-1-1 class 1-4
    applied_stress: float | None  # MPa, compressive, worst fibre on this wall
    safety_factor: float | None  # ELASTIC: elastic_critical_stress / applied_stress

    # The stress this wall can actually reach, MPa: min(sigma_cr, f_o).
    # Perfect-plate theory has no upper bound, so a stocky wall comes back
    # with an elastic critical stress far above the material's proof
    # stress -- 2,900 MPa on a 4mm wall of a 60x40 tube in 6063-T6, which
    # yields at 214. The elastic safety factor built on that is a true
    # statement about plate buckling and a misleading statement about the
    # wall, because the wall would have yielded long before.
    yield_capped_stress: float | None
    # ...and the factor that follows from it. THIS is the limiting number
    # for the wall; `safety_factor` above stays as the pure buckling
    # result, and the two are reported side by side rather than one
    # silently replacing the other.
    effective_safety_factor: float | None
    # True when the cap actually bit (sigma_cr > f_o), i.e. this wall is
    # yield-governed rather than buckling-governed.
    yield_governed: bool = False

    caveats: list[str] = field(default_factory=list)

    @property
    def supports_label(self) -> str:
        return {
            "internal": "both edges supported",
            "outstand": "one edge free (outstand)",
            "uncertain": "boundary conditions unclear",
        }[self.support]


@dataclass
class LocalBucklingResult:
    material: str
    segments: list[PlateSegment]
    governing: PlateSegment | None  # lowest safety factor, or worst class if no load given
    assumptions: list[str]

    def summary(self) -> str:
        lines = [
            f"Local (plate) buckling — {self.material}",
            f"{len(self.segments)} wall segments found"
            + (
                f", governing: segment {self.governing.index}"
                if self.governing is not None
                else ", none classifiable"
            ),
            "",
            f"{'#':>2}  {'b (mm)':>8} {'t (mm)':>7} {'b/t':>7}  {'edges':<26} "
            f"{'k':>5} {'s_cr (MPa)':>11} {'class':>5} {'elastic SF':>11} {'eff. SF':>8}",
        ]
        for s in self.segments:
            lines.append(
                f"{s.index:>2}  {s.width:>8.2f} {s.thickness:>7.2f} {s.slenderness:>7.2f}  "
                f"{s.supports_label:<26} "
                f"{s.k if s.k is not None else float('nan'):>5.3f} "
                f"{s.elastic_critical_stress if s.elastic_critical_stress is not None else float('nan'):>11.1f} "
                f"{s.section_class if s.section_class is not None else 0:>5} "
                f"{s.safety_factor if s.safety_factor is not None else float('nan'):>11.2f} "
                f"{s.effective_safety_factor if s.effective_safety_factor is not None else float('nan'):>8.2f}"
                + ("  (yield)" if s.yield_governed else "")
            )
            for caveat in s.caveats:
                lines.append(f"      ! {caveat}")
        lines.append("")
        lines.extend(f"Assumption: {a}" for a in self.assumptions)
        return "\n".join(lines)


# --- Elastic plate buckling --------------------------------------------------


def elastic_critical_stress(k: float, E: float, nu: float, thickness: float, width: float) -> float:
    """Classical long-plate result, sigma_cr = k pi^2 E / (12(1-nu^2)) (t/b)^2.

    E in MPa gives sigma_cr in MPa. Verified against a direct numerical
    solution of the plate eigenvalue problem in verify_local_buckling."""
    if width <= 0 or thickness <= 0:
        raise ValueError("width and thickness must be positive")
    return k * math.pi**2 * E / (12.0 * (1.0 - nu**2)) * (thickness / width) ** 2


def epsilon(yield_strength: float) -> float:
    """EN 1999-1-1's material factor, eps = sqrt(250 / f_o), f_o in N/mm^2."""
    if yield_strength <= 0:
        raise ValueError("yield strength must be positive")
    return math.sqrt(250.0 / yield_strength)


def ec9_class(beta: float, eps: float, limits: tuple[float, float, float]) -> int:
    """EN 1999-1-1 Table 6.2 cross-section class, 1 (stockiest) to 4
    (slender: local buckling before the proof stress is reached)."""
    ratio = beta / eps
    for cls, limit in enumerate(limits, start=1):
        if ratio <= limit:
            return cls
    return 4


# --- Wall segmentation -------------------------------------------------------


def _line_intersection(p0, d0, p1, d1):
    """Where two infinite centrelines cross, or None if they are parallel."""
    cross = float(d0[0] * d1[1] - d0[1] * d1[0])
    if abs(cross) < 1e-9:
        return None
    delta = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
    t0 = float(delta[0] * d1[1] - delta[1] * d1[0]) / cross
    return np.asarray(p0, dtype=float) + t0 * np.asarray(d0, dtype=float)


def _angle_between(a: Vertex, b: Vertex) -> float:
    """Unsigned angle between two unit vectors, in degrees."""
    return abs(
        math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]))
    )


def _face_runs(samples: list[ThicknessSample], step: float) -> list[list[ThicknessSample]]:
    """Contiguous stretches of ONE face that are flat and of constant
    thickness: the outside (or inside) of a single plate element.

    A run breaks where the boundary bends, where the thickness steps, or
    where the sweep skipped samples (a corner, which is not wall-like and
    so is not in this list at all)."""
    runs: list[list[ThicknessSample]] = []
    for ordered in ordered_by_ring(samples).values():
        current: list[ThicknessSample] = []
        for sample in ordered:
            if current:
                previous = current[-1]
                first = current[0]
                # Against the ACTUAL local sample spacing, not the nominal
                # step: `sample_thickness` caps how many samples one edge
                # may take, so on a long face the real spacing is wider
                # than the step, and a nominal test breaks every run on it.
                spacing = max(step, previous.run, sample.run)
                contiguous = abs(sample.s - previous.s) <= 2.5 * spacing
                flat = (
                    _angle_between(previous.normal, sample.normal) <= STRAIGHT_FACE_MAX_TURN_DEG
                    and _angle_between(first.normal, sample.normal) <= FACE_MAX_DEVIATION_DEG
                )
                even = abs(sample.thickness - previous.thickness) <= FACE_THICKNESS_TOLERANCE * max(
                    sample.thickness, previous.thickness
                )
                if not (contiguous and flat and even):
                    if len(current) >= MIN_SEGMENT_SAMPLES:
                        runs.append(current)
                    current = []
            current.append(sample)
        if len(current) >= MIN_SEGMENT_SAMPLES:
            runs.append(current)
    return runs


def _pair_faces(runs: list[list[ThicknessSample]]) -> list[list[list[ThicknessSample]]]:
    """Group the runs into plates. The two faces of one wall are found by
    following the measurement itself: every sample already knows the point
    on the far side its inscribed circle touched, so a run whose contacts
    land mostly inside another run IS that run's opposite face."""
    extents = []
    for run in runs:
        xs = [p.point[0] for p in run]
        ys = [p.point[1] for p in run]
        extents.append((min(xs), min(ys), max(xs), max(ys)))

    def lands_in(contact: Vertex, box: tuple[float, float, float, float], pad: float) -> bool:
        return (
            box[0] - pad <= contact[0] <= box[2] + pad
            and box[1] - pad <= contact[1] <= box[3] + pad
        )

    # Link runs that face each other, then take connected components rather
    # than pairing off one-to-one. A face does not always have exactly one
    # opposite number: an I-beam's flange is a single unbroken run on the
    # outside but two runs on the inside, one either side of the web, and a
    # one-to-one pairing has to discard one of them. The component holds
    # all three, and `_split_at_junctions` then cuts the flange at the web.
    links: dict[int, set[int]] = {i: set() for i in range(len(runs))}
    for i, run in enumerate(runs):
        pad = 0.6 * run[0].thickness
        votes: dict[int, int] = {}
        for sample in run:
            for j, box in enumerate(extents):
                if j != i and lands_in(sample.contact, box, pad):
                    votes[j] = votes.get(j, 0) + 1
        for j, count in votes.items():
            if count >= 0.3 * len(run):
                links[i].add(j)
                links[j].add(i)

    plates: list[list[list[ThicknessSample]]] = []
    seen: set[int] = set()
    for i in range(len(runs)):
        if i in seen:
            continue
        component: list[int] = []
        stack = [i]
        seen.add(i)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbour in links[node]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        plates.append([runs[j] for j in sorted(component)])
    return plates


def _centreline(plate: list[list[ThicknessSample]]) -> tuple[Vertex, Vertex, float, np.ndarray]:
    """Fit the plate's centreline through the medial-axis points of every
    sample on it, and return its two ends, its length and its direction."""
    centres = np.array([s.centre for run in plate for s in run], dtype=float)
    mean = centres.mean(axis=0)
    # Principal direction of the centre cloud: the line the wall runs along.
    _, _, vt = np.linalg.svd(centres - mean, full_matrices=False)
    direction = vt[0]
    t = (centres - mean) @ direction
    lo, hi = float(t.min()), float(t.max())
    start = tuple(mean + lo * direction)
    end = tuple(mean + hi * direction)
    return start, end, hi - lo, direction


def _straightness(plate: list[list[ThicknessSample]], direction: np.ndarray) -> float:
    """Worst perpendicular deviation of the centre cloud from its own
    fitted line, in mm -- how curved the wall is."""
    centres = np.array([s.centre for run in plate for s in run], dtype=float)
    mean = centres.mean(axis=0)
    normal = np.array([-direction[1], direction[0]])
    return float(np.abs((centres - mean) @ normal).max())


def _classify_ends(
    plate_index: int,
    start: Vertex,
    end: Vertex,
    direction: np.ndarray,
    thickness: float,
    inside,
    junctions: list[tuple[Vertex, int]],
) -> tuple[list[bool], list[str]]:
    """Is each longitudinal edge of this wall supported or free?

    Supported means another wall carries on from there, so it restrains
    the edge; free means the wall simply stops. The test is direct: step a
    little past the end of the wall along its own centreline and ask
    whether there is still material there. At a corner or a junction there
    is (the metal turns and continues); at a tip there is not.

    A wall's end is also taken as supported if ANOTHER wall's end sits on
    it, which is the case the probe alone gets wrong. At the inside corner
    of an angle the probe steps out of the material and would call the
    root a free edge, when in fact the other leg is attached there; and at
    a T-junction a flange's centreline runs on past the web, so the probe
    finds material and says nothing about the support the web provides.
    The plate's own two endpoints are excluded, or every wall would
    trivially be found joined to itself."""
    reach = END_PROBE_THICKNESSES * thickness
    tol = JUNCTION_TOLERANCE_THICKNESSES * thickness
    supported: list[bool] = []
    notes: list[str] = []
    for point, sign in ((start, -1.0), (end, +1.0)):
        probe = (point[0] + sign * direction[0] * reach, point[1] + sign * direction[1] * reach)
        continues = inside.contains(Point(probe))
        joined = any(
            owner != plate_index and math.dist(point, other) <= tol
            for other, _, owner in junctions
        )
        supported.append(bool(continues or joined))
    return supported, notes


def _extend_to_edges(
    plate_index: int,
    start: np.ndarray,
    end: np.ndarray,
    direction: np.ndarray,
    thickness: float,
    supported: list[bool],
    inside,
    junctions: list[tuple[Vertex, int]],
) -> tuple[np.ndarray, np.ndarray]:
    """Carry the measured centreline out to where the wall really ends.

    The medial axis stops short at both kinds of end, in opposite ways,
    and neither is the width a code wants. At a free tip it stops half a
    thickness early, because the inscribed circle starts touching the end
    face before it reaches it -- so march on until the material runs out
    and take the tip. At a junction it stops at the clear width between
    the adjoining walls, so snap to where the adjoining wall's own
    centreline crosses. Both together give the mid-line width, which is
    the conservative side of the clear flat width a code measures between
    fillet toes.

    On an L-angle this turns a measured 38.8mm leg into 45.0mm, which is
    exactly the mid-line distance from the other leg's centreline to the
    tip; on a 60x40 tube with 4mm walls it turns 50.5mm into 56.0mm, which
    is exactly 60 - 4."""
    tol = JUNCTION_SEARCH_THICKNESSES * thickness
    out = [start, end]
    for i, (point, sign) in enumerate(((start, -1.0), (end, +1.0))):
        if supported[i]:
            # Extend to where this wall's centreline crosses the centreline
            # of the wall it joins -- the classical thin-walled mid-line
            # junction. Intersecting the lines rather than snapping to the
            # neighbour's endpoint matters because the two medial axes stop
            # well apart when the fillet is large next to a thin wall: on a
            # 1.2mm tube with 3mm radii they end 3mm apart, which no
            # tolerance scaled to the wall thickness can bridge, while
            # their intersection is exact regardless.
            best = None
            for other, direction_other, owner in junctions:
                if owner == plate_index:
                    continue
                gap = math.dist(tuple(point), other)
                if gap <= tol and (best is None or gap < best[0]):
                    best = (gap, other, direction_other)
            if best is not None:
                gap, other, direction_other = best
                crossing = _line_intersection(point, direction, np.array(other), direction_other)
                if crossing is not None:
                    along = float((crossing - point) @ direction)
                    # Only ever extend, and never much past where the
                    # neighbour actually is. Capping against the measured
                    # gap rather than a multiple of thickness is what stops
                    # a near-parallel neighbour dragging the end far up the
                    # wall: the junction is where the other wall stopped,
                    # so the crossing has to be about that far away.
                    if along * sign > 0 and abs(along) <= MAX_JUNCTION_EXTENSION_GAPS * max(
                        gap, 0.5 * thickness
                    ):
                        out[i] = point + along * direction
            continue
        # Free end: march to the tip.
        step = thickness / 16.0
        tip = point
        for _ in range(int(2.0 * thickness / step)):
            nxt = tip + sign * step * direction
            if not inside.contains(Point(nxt[0], nxt[1])):
                break
            tip = nxt
        out[i] = tip
    return out[0], out[1]


def _split_at_junctions(plates: list[dict], _tol_mult: float = JUNCTION_TOLERANCE_THICKNESSES):
    """Walls meet in the middle of other walls. An I-beam's flange is one
    straight plate whose centreline runs the full flange width, but the web
    lands on its midpoint and supports it there, making each half an
    outstand rather than the whole thing a plate free at both ends. So any
    wall whose interior is touched by another wall's end is cut there."""
    ends: list[Vertex] = []
    for plate in plates:
        ends.extend([plate["start"], plate["end"]])

    out: list[dict] = []
    for plate in plates:
        start = np.array(plate["start"])
        end = np.array(plate["end"])
        direction = plate["direction"]
        length = plate["length"]
        tol = _tol_mult * plate["thickness"]
        cuts: list[float] = []
        for other in ends:
            delta = np.array(other) - start
            along = float(delta @ direction)
            across = abs(float(delta @ np.array([-direction[1], direction[0]])))
            if across <= tol and tol < along < length - tol:
                cuts.append(along)
        if not cuts:
            out.append(plate)
            continue
        cuts = sorted(set(round(c, 6) for c in cuts))
        bounds = [0.0] + cuts + [length]
        for a, b in zip(bounds[:-1], bounds[1:]):
            if b - a < 1e-9:
                continue
            piece = dict(plate)
            piece["start"] = tuple(start + a * direction)
            piece["end"] = tuple(start + b * direction)
            piece["length"] = b - a
            piece["split"] = True
            out.append(piece)
    return out


# --- Entry point -------------------------------------------------------------


def analyze_local_buckling(
    vertices: list[Vertex],
    holes: list[list[Vertex]] | None,
    material,
    applied_axial_stress: float | None = None,
    moment: float | None = None,
    bending_axis: str = "y",
    section=None,
) -> LocalBucklingResult:
    """Plate-buckling check on every flat wall of the profile.

    `material` needs `.E`, `.nu`, `.yield_strength` and `.name` (an
    `eat.section.Material`). Pass `section` (an `eat.section.SectionResult`)
    plus `moment`/`applied_axial_stress` to get applied stresses and safety
    factors; without them the geometry is still classified, which is the
    part that does not depend on the load case at all.
    """
    # Same contract as the section engine -- see eat.profile for why this
    # is not a bare polygon.is_valid test, and why all three engines now
    # share one definition instead of three.
    validate_profile(vertices, holes)
    poly = Polygon(vertices, holes or None)
    rings = normalized_rings(vertices, holes)
    step = sample_step(poly)
    samples = wall_like(sample_thickness(poly, rings))
    inside = prep(poly)

    plates: list[dict] = []
    for faces in _pair_faces(_face_runs(samples, step)):
        thicknesses = [s.thickness for run in faces for s in run]
        start, end, length, direction = _centreline(faces)
        if length <= 0:
            continue
        if length < MIN_PLATE_ASPECT * float(np.median(thicknesses)):
            continue  # narrower than it is thick: metal at a junction, not a wall element
        thickness = float(np.median(thicknesses))
        # Robust statistics, not extremes. The last sample or two before a
        # wall runs into a corner already sees round it -- reading a little
        # thicker and a little off-parallel -- and judging the wall by those
        # would call every plate in a filleted tube tapered and skewed.
        spread = float(
            np.percentile(thicknesses, 90) - np.percentile(thicknesses, 10)
        ) / max(thickness, 1e-9)
        opposition = float(
            np.percentile([s.opposition_deg for run in faces for s in run], 75)
        )
        plates.append(
            {
                "ring": faces[0][0].ring,
                "faces": len(faces),
                "start": start,
                "end": end,
                "direction": direction,
                "length": length,
                "thickness": thickness,
                "spread": spread,
                "opposition": opposition,
                "bend": _straightness(faces, direction),
                "split": False,
            }
        )

    plates = _split_at_junctions(plates)
    junctions: list[tuple[Vertex, np.ndarray, int]] = []
    for owner, plate in enumerate(plates):
        junctions.append((plate["start"], plate["direction"], owner))
        junctions.append((plate["end"], plate["direction"], owner))

    eps = epsilon(material.yield_strength) if material.yield_strength else None
    segments: list[PlateSegment] = []

    for index, plate in enumerate(plates, start=1):
        thickness = plate["thickness"]
        direction = plate["direction"]
        supported, notes = _classify_ends(
            index - 1, plate["start"], plate["end"], direction, thickness, inside, junctions
        )
        edge_a, edge_b = _extend_to_edges(
            index - 1,
            np.array(plate["start"]),
            np.array(plate["end"]),
            direction,
            thickness,
            supported,
            inside,
            junctions,
        )
        width = float(np.linalg.norm(edge_b - edge_a))
        slenderness = width / thickness

        caveats = list(notes)
        if plate["faces"] < 2:
            caveats.append(
                "only one face of this wall could be resolved; thickness is from the "
                "inscribed-circle sweep but the opposite face was not matched to it"
            )
        if plate["spread"] > 2 * FACE_THICKNESS_TOLERANCE:
            caveats.append(
                f"thickness varies {plate['spread']:.0%} along this wall; it is tapered, and "
                "a prismatic plate is the wrong model for it"
            )
        if plate["opposition"] > PARALLEL_FACES_MAX_DEG:
            caveats.append(
                f"faces are {plate['opposition']:.0f}° from parallel, not a prismatic plate"
            )
        if plate["bend"] > 0.25 * thickness:
            caveats.append(
                f"centreline bows {plate['bend']:.2f} mm; a curved wall is stiffer than the "
                "flat-plate model assumes, so this would be conservative if it were reported"
            )

        n_supported = sum(1 for s in supported if s)
        blocking = [c for c in caveats if "conservative if it were reported" not in c]
        if n_supported == 2 and not blocking:
            support, k = "internal", K_INTERNAL
            limits, welded_limits = EC9_INTERNAL_LIMITS, EC9_WELDED_INTERNAL_LIMITS
        elif n_supported == 1 and not blocking:
            support, k = "outstand", K_OUTSTAND
            limits, welded_limits = EC9_OUTSTAND_LIMITS, EC9_WELDED_OUTSTAND_LIMITS
        else:
            support, k, limits, welded_limits = "uncertain", None, None, None
            if n_supported == 0:
                caveats.append(
                    "neither end is restrained by another wall, so this is not a plate element "
                    "at all — it is the whole section, and the global Euler check covers it"
                )

        sigma_cr = (
            elastic_critical_stress(k, material.E, material.nu, thickness, width)
            if k is not None
            else None
        )
        beta_over_eps = slenderness / eps if (eps is not None and k is not None) else None
        cls = ec9_class(slenderness, eps, limits) if (beta_over_eps is not None) else None
        if cls is not None and welded_limits is not None:
            welded_cls = ec9_class(slenderness, eps, welded_limits)
            if welded_cls != cls:
                caveats.append(
                    f"class {cls} assumes an unwelded, buckling-class-A alloy; welded it would be "
                    f"class {welded_cls}"
                )

        applied = _applied_stress(plate, section, moment, applied_axial_stress, bending_axis)
        safety = (
            sigma_cr / applied if (sigma_cr is not None and applied and applied > 0) else None
        )
        # Cap the capacity at the proof stress: a wall cannot carry more
        # than the material can, however stocky the plate is.
        f_o = material.yield_strength
        capped = sigma_cr if sigma_cr is None else (min(sigma_cr, f_o) if f_o else sigma_cr)
        yield_governed = bool(sigma_cr is not None and f_o and sigma_cr > f_o)
        effective = (
            capped / applied if (capped is not None and applied and applied > 0) else None
        )
        if yield_governed:
            caveats.append(
                f"elastic plate buckling would not govern this wall — its critical stress "
                f"({sigma_cr:,.0f} MPa) is above the {f_o:,.0f} MPa proof stress, so it yields "
                f"first; the effective safety factor is capped accordingly"
            )

        segments.append(
            PlateSegment(
                index=index,
                ring=plate["ring"],
                width=width,
                thickness=thickness,
                slenderness=slenderness,
                support=support,
                start=(float(edge_a[0]), float(edge_a[1])),
                end=(float(edge_b[0]), float(edge_b[1])),
                k=k,
                elastic_critical_stress=sigma_cr,
                beta_over_epsilon=beta_over_eps,
                section_class=cls,
                applied_stress=applied,
                safety_factor=safety,
                yield_capped_stress=capped,
                effective_safety_factor=effective,
                yield_governed=yield_governed,
                caveats=caveats,
            )
        )

    governing = _governing(segments)
    assumptions = [
        "each wall is treated as a long plate in UNIFORM compression; a web with a stress "
        "gradient across it buckles at a higher load, so this is conservative for webs",
        "wall edges are taken as simply supported where another wall joins; real junctions "
        "offer some rotational restraint, so true capacity is higher",
        "EN 1999-1-1 classes assume an unwelded, buckling-class-A alloy",
        "b is the extent of each wall's medial axis, between the clear flat width a code measures and the full mid-line width",
    ]
    return LocalBucklingResult(
        material=material.name, segments=segments, governing=governing, assumptions=assumptions
    )


def _applied_stress(plate, section, moment, axial_stress, bending_axis) -> float | None:
    """Worst compressive fibre stress on this wall: |N|/A + |M| d_max / I,
    with d_max the wall's own greatest distance from the bending axis, so
    a web near the neutral axis isn't judged against a flange's stress.
    Unsigned throughout, which keeps it on the conservative side."""
    if section is None:
        return axial_stress
    total = abs(axial_stress) if axial_stress else 0.0
    if moment:
        if bending_axis == "y":
            centre, inertia = section.cy, section.ixx
            reach = max(abs(plate["start"][1] - centre), abs(plate["end"][1] - centre))
        else:
            centre, inertia = section.cx, section.iyy
            reach = max(abs(plate["start"][0] - centre), abs(plate["end"][0] - centre))
        if inertia > 0:
            total += abs(moment) * reach / inertia
    return total or None


def _governing(segments: list[PlateSegment]) -> PlateSegment | None:
    """The wall that governs: lowest EFFECTIVE safety factor if a load was
    given, otherwise the worst (highest) class, then the most slender.

    Ranked on the yield-capped factor rather than the elastic one because
    that is the wall's real limit -- ranking on the uncapped elastic
    factor can nominate a wall that would in fact yield later than another
    whose elastic factor merely looks worse."""
    rated = [s for s in segments if s.effective_safety_factor is not None]
    if rated:
        return min(rated, key=lambda s: s.effective_safety_factor)
    classified = [s for s in segments if s.section_class is not None]
    if classified:
        return max(classified, key=lambda s: (s.section_class, s.slenderness))
    return None


def as_dict(result: LocalBucklingResult) -> dict[str, Any]:
    return {
        "material": result.material,
        "assumptions": result.assumptions,
        "governing_index": result.governing.index if result.governing else None,
        "segments": [
            {
                "index": s.index,
                "ring": s.ring,
                "width": s.width,
                "thickness": s.thickness,
                "slenderness": s.slenderness,
                "support": s.support,
                "supports_label": s.supports_label,
                "start": list(s.start),
                "end": list(s.end),
                "k": s.k,
                "elastic_critical_stress": s.elastic_critical_stress,
                "beta_over_epsilon": s.beta_over_epsilon,
                "section_class": s.section_class,
                "applied_stress": s.applied_stress,
                "safety_factor": s.safety_factor,
                "yield_capped_stress": s.yield_capped_stress,
                "effective_safety_factor": s.effective_safety_factor,
                "yield_governed": s.yield_governed,
                "caveats": s.caveats,
            }
            for s in result.segments
        ],
    }
