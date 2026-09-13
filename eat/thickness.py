"""
EAT local wall thickness — a medial-axis / maximum-inscribed-circle measure.

Definition used throughout: the **local wall thickness at a boundary point
p is the diameter of the largest circle that fits entirely inside the
profile and touches the boundary at p**. That circle's centre is a point
of the profile's medial axis, and the circle touches the boundary again
somewhere on the far side of the wall, so the diameter is the width of the
material at p in the only direction that is geometrically meaningful there.

Why this and not a ray cast
---------------------------
The obvious cheap measure -- fire a ray along the inward normal and take
the distance to the first boundary it meets -- was tried first (build step
11) and abandoned. A ray only measures a wall when it happens to cross
one; when it runs lengthwise up a leg or a rib it measures how long that
feature is, which reads as an enormously thick wall. That made a
"wall too thick" check impossible: false positive on every profile tried,
true positive on none.

The inscribed circle cannot do that. It is pinned at p and must stay
inside the material, so the moment the wall it sits in ends -- at a
corner, at a rib junction, at the end of a leg -- the circle is cut off by
the boundary that ends it. Formally the inscribed measure is always <= the
ray measure and equals it exactly when the opposite face is parallel:
against a planar opposite face at perpendicular distance d whose normal
makes an angle T with the ray, the largest tangent circle has diameter
2*d*cos(T)/(1 + cos(T)), which is d at T = 0 and falls away smoothly as
the face slants. So it degrades gracefully where the ray cast blew up.

How it is computed
------------------
By the shrinking-ball iteration (the standard medial-axis-from-samples
construction, Ma/Bae/Choi 2012), done exactly against the polygon's own
edges rather than on a rasterized distance transform -- so there is no
pixel size to trade accuracy against, and a 0.8mm wall in a 60mm profile
is resolved as exactly as a 40mm one.

For a boundary point p with inward unit normal n, every candidate circle
touching p is centred at c(r) = p + r*n, and those circles are nested: a
bigger one contains a smaller one. So "the circle fits" is monotone in r,
and the largest fitting circle is found by starting too big and shrinking:

  1. pick r larger than anything that can fit (half the bounding diagonal)
  2. find q, the nearest boundary point to c(r). If it is no nearer than
     r, nothing intrudes, so the circle fits and we are done.
  3. otherwise shrink to the circle that is tangent at p and passes
     through q, which from |p + r*n - q| = r is

         r = |q - p|^2 / (2 * (q - p).n)

  4. repeat from 2.

Each step strictly shrinks r and the sequence is bounded below, so it
converges -- in practice in a handful of iterations. Step 2 is the whole
cost, and it is done for every sample at once against every edge at once
in numpy, which is what keeps a dense sweep of the boundary fast enough to
sit behind an interactive endpoint.

Sampling
--------
Thickness is sampled along the whole boundary at ~0.4mm intervals rather
than once per edge, because one midpoint reading on a long face is easily
unrepresentative. Each sample carries the boundary length it stands for
(`run`) and its arc-length position within its ring (`s`), so callers can
tell a real wall (tens of mm of boundary) from a corner or a tip (a
fraction of a mm), and can compare a sample against its neighbours along
the boundary without re-deriving the ordering.

Used by `eat.suggestions` (thin-wall, thick-wall and narrow-notch checks)
and `eat.local_buckling` (plate width-to-thickness ratios). Verified in
`eat/verify_thickness.py` against shapes whose exact medial-axis thickness
is known by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient
from shapely.prepared import prep

Vertex = tuple[float, float]

# Sampling density along the boundary. 0.4mm is fine enough to resolve the
# features extrusion DFM cares about (a 1mm wall, a 0.5mm fillet) without
# making the sweep expensive on a large profile; the span/300 term keeps
# the sample count bounded on a big one.
MIN_SAMPLE_STEP_MM = 0.4
SAMPLE_STEPS_PER_SPAN = 300
MAX_SAMPLES_PER_EDGE = 60  # a single edge can't dominate the sweep

_SHRINK_ITERATIONS = 64  # comfortably more than convergence actually takes
_NEAREST_CHUNK = 192  # samples per vectorized batch, to bound peak memory

# "The circle fits" is tested relative to the circle's own radius, not
# against an absolute distance. Where the far contact is nearly grazing --
# a steeply-angled face, or a convex fillet the circle is sliding along --
# an intrusion of 1e-11 mm can still mean the radius is 1e-8 mm too big,
# because the intrusion depth there is ~1e-3 of the radius error. A
# relative test keeps those cases converging to full double precision
# instead of stalling three decades short. 1e-13 is ~1000x the ~1e-16
# relative noise floor of the distance arithmetic itself.
_CONVERGENCE_REL = 1e-13

# How close to its own radius the far contact has to sit before it counts
# as a tangency rather than "the nearest thing left after masking". Loose
# compared with _CONVERGENCE_REL because it is a classification, not a
# solve: a real tangency lands within ~1e-13, a masked-out one is typically
# several times the radius away.
_TANGENCY_REL = 1e-6

# How far from parallel the two faces of a "wall" may be. A wall with 3
# degrees of draft on it is still a wall; a circle whose far contact is a
# face at 90 degrees is sitting in a corner, and calling its diameter a
# wall thickness is how the old ray-cast measure got into trouble. 30
# degrees is deliberately generous -- it admits tapered walls and the
# chord-to-chord splay of a discretized fillet, and still excludes every
# corner and rib junction, which cluster near 90.
WALL_OPPOSITION_MAX_DEG = 30.0


@dataclass(frozen=True)
class ThicknessSample:
    """One local-thickness measurement, taken at a point on the boundary.

    `thickness` is the diameter of the largest inscribed circle touching
    `point`; `centre` is that circle's centre (a medial-axis point) and
    `contact` the point on the far side of the wall where it touches the
    boundary again. `run` is how much boundary length this sample stands
    for, and `s` its arc-length position along its own ring, measured from
    that ring's first vertex.

    `opposition_deg` is the angle between the inward normal at `point` and
    the outward normal of the face the circle lands on. It answers "is this
    a wall?": between two parallel faces it is 0, and it grows as the two
    faces splay apart, reaching 90 degrees when the circle is really
    sitting in a corner or a junction rather than crossing a wall. The
    circle's diameter is a valid thickness whatever this angle is -- it is
    the true width of the material there -- but only a small angle means
    that width is a *wall thickness* in the sense extrusion DFM uses the
    term. Callers that reason about walls should filter on it (see
    `WALL_OPPOSITION_MAX_DEG`); callers that just want the local width of
    the material (notch depth, plate slenderness) need not.
    """

    ring: str  # "outer", "hole 1", ... -- matches eat.suggestions' ring labels
    index: int  # index of the edge's first vertex within its ring
    point: Vertex
    normal: Vertex  # inward unit normal at `point`
    run: float  # mm of boundary this sample represents
    s: float  # mm along the ring, from its first vertex
    ring_length: float  # mm, total perimeter of this ring (for wrap-around)
    thickness: float  # mm, the inscribed-circle diameter
    centre: Vertex  # medial-axis point
    contact: Vertex  # where the circle touches the far side
    contact_normal: Vertex  # OUTWARD unit normal of the boundary at `contact`
    opposition_deg: float  # angle between `normal` and `contact_normal` -- see below


# --- Ring/geometry setup -----------------------------------------------------


def normalized_rings(
    vertices: list[Vertex], holes: list[list[Vertex]] | None = None
) -> list[tuple[str, list[Vertex]]]:
    """Rings as (label, coords) with the outer boundary CCW and every hole
    CW -- the same winding `eat.section` and `eat.suggestions` normalize to.
    With it, the material always lies to the LEFT of the direction of
    travel on every ring, so the left normal is the inward normal
    everywhere and no per-ring sign handling is needed below."""
    poly = orient(Polygon(vertices, holes or None), sign=1.0)
    rings: list[tuple[str, list[Vertex]]] = [("outer", list(poly.exterior.coords)[:-1])]
    for i, interior in enumerate(poly.interiors):
        rings.append((f"hole {i + 1}", list(interior.coords)[:-1]))
    return rings


def sample_step(poly: Polygon) -> float:
    """Boundary sampling interval used by `sample_thickness`. Exposed so
    callers that cluster or group samples can size their tolerances in the
    same units the sweep was taken in."""
    minx, miny, maxx, maxy = poly.bounds
    span = math.hypot(maxx - minx, maxy - miny)
    return max(MIN_SAMPLE_STEP_MM, span / SAMPLE_STEPS_PER_SPAN)


class _Edges:
    """Every ring edge as flat numpy arrays -- the fixed geometry the
    shrinking ball is queried against. Degenerate edges are dropped.
    `outward` is each edge's outward unit normal, which follows from the
    ring winding: with the material on the left, the right normal points
    out."""

    def __init__(self, rings: list[tuple[str, list[Vertex]]], eps: float):
        starts: list[tuple[float, float]] = []
        directions: list[tuple[float, float]] = []
        for _, coords in rings:
            n = len(coords)
            for i in range(n):
                x0, y0 = coords[i]
                x1, y1 = coords[(i + 1) % n]
                dx, dy = x1 - x0, y1 - y0
                if math.hypot(dx, dy) < eps:
                    continue
                starts.append((x0, y0))
                directions.append((dx, dy))
        self.a = np.asarray(starts, dtype=float).reshape(-1, 2)
        self.d = np.asarray(directions, dtype=float).reshape(-1, 2)
        self.dd = (self.d * self.d).sum(axis=1)
        length = np.sqrt(self.dd)
        with np.errstate(invalid="ignore", divide="ignore"):
            self.outward = np.stack([self.d[:, 1] / length, -self.d[:, 0] / length], axis=1)

    def __len__(self) -> int:
        return len(self.a)


def _nearest_on_boundary(
    points: np.ndarray,
    edges: _Edges,
    exclude_near: tuple[np.ndarray, np.ndarray] | None = None,
):
    """For each query point, the nearest point anywhere on the boundary,
    its distance, and which edge it landed on. Chunked over the queries so
    the (queries x edges x 2) intermediate stays a few MB whatever the
    profile's vertex count.

    `exclude_near` is an optional (origins, radii) pair that masks out
    candidates lying within `radius` of `origin` -- see `_contact_faces`
    for the one caller that needs it."""
    count = len(points)
    best_q = np.empty((count, 2), dtype=float)
    best_d = np.empty(count, dtype=float)
    best_e = np.empty(count, dtype=int)
    for start in range(0, count, _NEAREST_CHUNK):
        stop = start + _NEAREST_CHUNK
        block = points[start:stop]
        # Parameter of the closest point on each (infinite) edge line,
        # clamped to the segment.
        w = block[:, None, :] - edges.a[None, :, :]
        t = (w * edges.d[None, :, :]).sum(axis=-1) / edges.dd[None, :]
        np.clip(t, 0.0, 1.0, out=t)
        proj = edges.a[None, :, :] + t[..., None] * edges.d[None, :, :]
        diff = block[:, None, :] - proj
        d2 = (diff * diff).sum(axis=-1)
        if exclude_near is not None:
            origins, radii = exclude_near
            back = proj - origins[start:stop, None, :]
            near = (back * back).sum(axis=-1) < (radii[start:stop, None] ** 2)
            d2 = np.where(near, np.inf, d2)
        pick = np.argmin(d2, axis=1)
        rows = np.arange(len(block))
        best_q[start:stop] = proj[rows, pick]
        best_d[start:stop] = np.sqrt(d2[rows, pick])
        best_e[start:stop] = pick
    return best_q, best_d, best_e


def _contact_faces(
    points: np.ndarray, normals: np.ndarray, radii: np.ndarray, edges: _Edges
) -> tuple[np.ndarray, np.ndarray]:
    """Where each finished circle touches the boundary on the far side,
    and the outward normal of the face it touches there.

    Done as its own pass rather than read off the shrinking ball's last
    step, because that step is not reliably the tangency. On a fillet
    approximated by short chords, the chord next to the one a sample sits
    on cuts inside the tangent circle by O(turn^2 * chord) -- a few parts
    in 10^8, far too little to matter to the radius, but enough to be the
    last thing the iteration touched, which would then be recorded as the
    "opposite face" when it is really the same face one chord along.

    The fix is to ask the finished circle directly, ignoring anything
    within its own radius of the starting point. A true tangency at
    opposition angle phi sits at 2*r*cos(phi/2) from the start, so the
    exclusion discards only contacts beyond 120 degrees.

    Whether it discarded one is then read off the answer, and this part
    matters: every point of the boundary is at least r from the centre of
    a circle that fits, and the tangency is at exactly r. So if the
    nearest surviving point is FURTHER than r, the real tangency was one
    of the ones excluded -- the circle is pinched between neighbours, on a
    spike or in the sub-millimetre zigzag a scanned or vector-extracted
    drawing leaves at a corner. Those are reported as 180 degrees, square
    on, definitively not a wall. Without that test they instead report
    whatever distant face happened to survive the mask, which on a real
    imported profile is often near enough parallel to pass for a wall and
    invent a fraction-of-a-millimetre one at every digitizing artifact."""
    centres = points + radii[:, None] * normals
    q, d, e = _nearest_on_boundary(centres, edges, exclude_near=(points, radii))
    outward = edges.outward[e]
    pinched = ~(d <= radii * (1.0 + _TANGENCY_REL))  # catches non-finite d too
    if pinched.any():
        q[pinched] = centres[pinched]
        outward[pinched] = -normals[pinched]
    return q, outward


def _shrinking_ball(
    points: np.ndarray,
    normals: np.ndarray,
    edges: _Edges,
    r_init: float,
    tol: float,
) -> np.ndarray:
    """Radius of the largest inscribed circle touching each sample point.
    See the module docstring for the derivation; this is that iteration
    run on every sample at once.

    A circle is accepted once nothing on the boundary lies nearer to its
    centre than its own radius (the tangency at `point` itself always sits
    exactly at the radius, so it never triggers a shrink)."""
    radii = np.full(len(points), float(r_init))
    active = np.ones(len(points), dtype=bool)

    for _ in range(_SHRINK_ITERATIONS):
        idx = np.nonzero(active)[0]
        if idx.size == 0:
            break
        centres = points[idx] + radii[idx, None] * normals[idx]
        q, d, _ = _nearest_on_boundary(centres, edges)

        pq = q - points[idx]
        # Radius of the circle tangent at p that passes through q. Only
        # meaningful when q lies ahead of p along the inward normal, which
        # is guaranteed whenever q actually intrudes into the circle (the
        # circle lies wholly in that half-plane), so a non-positive
        # denominator means numerical noise, not a real constraint.
        denom = 2.0 * (pq * normals[idx]).sum(axis=1)
        numer = (pq * pq).sum(axis=1)
        safe = denom > tol
        candidate = np.where(safe, numer / np.where(safe, denom, 1.0), radii[idx])

        ceiling = radii[idx] * (1.0 - _CONVERGENCE_REL)
        intrudes = d < ceiling
        shrink = intrudes & safe & (candidate > 0.0) & (candidate < ceiling)
        radii[idx[shrink]] = candidate[shrink]
        active[idx[~shrink]] = False

    return radii


# --- The sweep ---------------------------------------------------------------


def sample_thickness(
    poly: Polygon, rings: list[tuple[str, list[Vertex]]] | None = None
) -> list[ThicknessSample]:
    """Local wall thickness sampled along the whole boundary of `poly`.

    `rings` may be passed to reuse an already-normalized ring list (see
    `normalized_rings`); it must describe the same geometry as `poly`.
    """
    if rings is None:
        rings = normalized_rings(list(poly.exterior.coords)[:-1], [list(r.coords)[:-1] for r in poly.interiors])

    minx, miny, maxx, maxy = poly.bounds
    span = math.hypot(maxx - minx, maxy - miny)
    if span <= 0:
        return []
    step = sample_step(poly)
    eps = span * 1e-9
    inside = prep(poly)

    edges = _Edges(rings, eps)
    if len(edges) == 0:
        return []

    # Walk the boundary once, laying out sample points, inward normals and
    # the bookkeeping (ring, edge index, arc length) each one carries.
    meta: list[tuple[str, int, float, float, float]] = []  # ring, index, run, s, ring_length
    pts: list[tuple[float, float]] = []
    nrm: list[tuple[float, float]] = []

    for ring_label, coords in rings:
        n = len(coords)
        edge_lengths = []
        for i in range(n):
            x0, y0 = coords[i]
            x1, y1 = coords[(i + 1) % n]
            edge_lengths.append(math.hypot(x1 - x0, y1 - y0))
        ring_length = sum(edge_lengths)
        travelled = 0.0
        for i in range(n):
            length = edge_lengths[i]
            if length < eps:
                continue
            x0, y0 = coords[i]
            x1, y1 = coords[(i + 1) % n]
            dx, dy = x1 - x0, y1 - y0
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux  # left normal: into the material, on every ring
            count = max(1, min(MAX_SAMPLES_PER_EDGE, int(round(length / step))))
            run = length / count
            for j in range(count):
                f = (j + 0.5) / count
                px, py = x0 + dx * f, y0 + dy * f
                if not inside.contains(Point(px + nx * eps, py + ny * eps)):
                    continue  # sliver or unexpected winding -- don't guess
                meta.append((ring_label, i, run, travelled + length * f, ring_length))
                pts.append((px, py))
                nrm.append((nx, ny))
            travelled += length

    if not pts:
        return []

    points = np.asarray(pts, dtype=float)
    normals = np.asarray(nrm, dtype=float)
    radii = _shrinking_ball(points, normals, edges, r_init=span / 2.0, tol=span * 1e-12)
    contacts, contact_normals = _contact_faces(points, normals, radii, edges)

    centres = points + radii[:, None] * normals
    # Angle between the two faces the circle touches: 0 when they are
    # parallel (a wall), 90 when the far one is square to this one (a
    # corner). See ThicknessSample.
    cos_opposition = np.clip((normals * contact_normals).sum(axis=1), -1.0, 1.0)
    opposition = np.degrees(np.arccos(cos_opposition))

    samples: list[ThicknessSample] = []
    for k, (ring_label, index, run, s, ring_length) in enumerate(meta):
        samples.append(
            ThicknessSample(
                ring=ring_label,
                index=index,
                point=(float(points[k, 0]), float(points[k, 1])),
                normal=(float(normals[k, 0]), float(normals[k, 1])),
                run=run,
                s=s,
                ring_length=ring_length,
                thickness=float(2.0 * radii[k]),
                centre=(float(centres[k, 0]), float(centres[k, 1])),
                contact=(float(contacts[k, 0]), float(contacts[k, 1])),
                contact_normal=(float(contact_normals[k, 0]), float(contact_normals[k, 1])),
                opposition_deg=float(opposition[k]),
            )
        )
    return samples


def thickness_at(poly: Polygon, point: Vertex, normal: Vertex) -> float:
    """The same measure at one arbitrary boundary point, for spot checks
    and for verification against hand-calculated values.

    `normal` must point into the material and need not be normalized. It
    must also be perpendicular to the boundary *at* `point` -- i.e. `point`
    lies in the interior of a straight edge, not on a vertex. At a convex
    vertex the two adjacent edges both cut inside any circle tangent to the
    bisector, so the shrinking ball collapses toward zero; the sweep in
    `sample_thickness` never asks that question because it only ever
    samples edge interiors with that edge's own normal."""
    rings = normalized_rings(
        list(poly.exterior.coords)[:-1], [list(r.coords)[:-1] for r in poly.interiors]
    )
    minx, miny, maxx, maxy = poly.bounds
    span = math.hypot(maxx - minx, maxy - miny)
    edges = _Edges(rings, span * 1e-9)
    nx, ny = normal
    mag = math.hypot(nx, ny)
    if mag == 0:
        raise ValueError("normal must be non-zero")
    radii = _shrinking_ball(
        np.asarray([point], dtype=float),
        np.asarray([(nx / mag, ny / mag)], dtype=float),
        edges,
        r_init=span / 2.0,
        tol=span * 1e-12,
    )
    return float(2.0 * radii[0])


def wall_like(samples: list[ThicknessSample]) -> list[ThicknessSample]:
    """Only the samples whose inscribed circle actually crosses a wall --
    i.e. lands on a face roughly parallel to the one it started from. A
    circle wedged into a corner measures a true width of material, but not
    a wall thickness; see `WALL_OPPOSITION_MAX_DEG`."""
    return [s for s in samples if s.opposition_deg <= WALL_OPPOSITION_MAX_DEG]


def ordered_by_ring(samples: list[ThicknessSample]) -> dict[str, list[ThicknessSample]]:
    """Samples grouped by ring and put in boundary order, so one can be
    compared against what actually lies either side of it."""
    by_ring: dict[str, list[ThicknessSample]] = {}
    for sample in samples:
        by_ring.setdefault(sample.ring, []).append(sample)
    for ordered in by_ring.values():
        ordered.sort(key=lambda s: s.s)
    return by_ring


def weighted_median(values: list[float], weights: list[float]) -> float:
    """Median weighted by boundary length -- the thickness of the typical
    millimetre of wall rather than of the typical face, so a profile isn't
    skewed by a handful of very short faces."""
    if not values:
        raise ValueError("weighted_median of an empty sample")
    order = sorted(range(len(values)), key=lambda i: values[i])
    total = sum(weights)
    running = 0.0
    for i in order:
        running += weights[i]
        if running >= total / 2.0:
            return values[i]
    return values[order[-1]]


def typical_thickness(samples: list[ThicknessSample]) -> float:
    """The profile's typical wall: the length-weighted median of the
    local-thickness sweep."""
    return weighted_median([s.thickness for s in samples], [s.run for s in samples])
