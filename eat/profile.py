"""EAT profile validation — what counts as a geometry this tool will analyze.

One definition, shared by every engine that takes a profile: `eat.section`
(FE section properties), `eat.suggestions` (DFM checks) and
`eat.local_buckling` (per-wall plate checks). Before this module existed
each had its own idea -- section analysis had none at all, and the other
two used a bare `polygon.is_valid` -- so the same DXF could produce
section properties but no design review, or crash one engine and not
another.

Kept free of `sectionproperties` (shapely and math only) so the two
geometry-only engines don't pull in the FE stack just to validate.

WHY THIS IS A HARD PREREQUISITE, NOT A NICETY
---------------------------------------------
`sectionproperties` meshes through a C library that does not validate its
input. Handed bad geometry it does not raise -- it variously hangs,
allocates until the machine swaps, or dies on a SIGSEGV/SIGBUS, which in
the server takes down the worker process rather than failing the request.
Measured before this guard existed:

    self-intersecting bowtie, figure-8, NaN vertex, hole == outer ring
                                      -> SIGSEGV, process killed
    hairline sliver 100 x 0.001 mm    -> hang, 7.6 GB before being killed
    three collinear points            -> HTTP 500, KeyError('triangles')
    hole larger than the profile      -> HTTP 500, ZeroDivisionError

Two more cases were worse than a crash because they were silent:

    hole outside the profile   -> quietly ignored; a 10x10 square with a
                                  stray hole reported the full 100 mm^2
    two overlapping holes      -> overlap subtracted twice; 400 - 36 - 36
                                  reported instead of 400 - 63

THE ONE DELIBERATE GAP
----------------------
A hole whose boundary TOUCHES the outer boundary is accepted, even though
Shapely calls the resulting polygon invalid (the contact splits the
interior). `eat.dxf_io` classifies holes with `covers` rather than
`contains` specifically so that a hole meeting the profile wall still
imports, so a bare `polygon.is_valid` test here would reject files that
import correctly today. Each condition is therefore tested separately --
rings, containment, overlap, area -- rather than deferring to Shapely's
combined verdict.
"""

from __future__ import annotations

import math

from shapely.geometry import LinearRing, Polygon

Vertex = tuple[float, float]

# Nothing in the profile may be thinner than this fraction of its own
# bounding-box diagonal. The mesher's failures here are NON-MONOTONIC --
# a 100 x 0.001 mm sliver hangs while 100 x 0.0003 mm meshes fine -- so
# there is nothing to catch and retry; it has to be refused up front.
# Measured: every sliver at or below this ratio is slow or fatal,
# everything above it meshes in under a second. Real profiles sit orders
# of magnitude clear -- the 20x40 KJN fixture is at 4.5e-2, and a 200x200
# tube with 0.8mm walls, about as thin-walled as an extrusion gets, is at
# 1.7e-3.
MIN_INSCRIBED_SPAN_RATIO = 1e-4


def validate_profile(
    vertices: list[Vertex],
    holes: list[list[Vertex]] | None = None,
) -> None:
    """Raise `ValueError` with a specific reason if this profile can't be
    analyzed. Returns None when it can. See the module docstring for what
    each test exists for."""
    if len(vertices) < 3:
        raise ValueError("A polygon needs at least 3 vertices")
    for hole in holes or []:
        if len(hole) < 3:
            raise ValueError("A hole needs at least 3 vertices")

    rings = [("profile", vertices)] + [(f"hole {i + 1}", h) for i, h in enumerate(holes or [])]
    for label, ring in rings:
        for x, y in ring:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(
                    f"The {label} has a vertex that isn't a finite number ({x}, {y}). "
                    "Check the source drawing for a malformed coordinate."
                )
        # A ring that crosses itself (a bowtie, a figure-8, a doubled-back
        # outline) has no well-defined interior to mesh.
        if not LinearRing(ring).is_simple:
            raise ValueError(
                f"The {label} outline crosses itself. A profile has to be a single "
                "simple closed loop — check for a stray vertex or a doubled-back edge."
            )

    shell = Polygon(vertices)
    for i, hole in enumerate(holes or []):
        hole_poly = Polygon(hole)
        if not shell.covers(hole_poly):
            raise ValueError(
                f"Hole {i + 1} is not fully inside the profile, so it can't be "
                "subtracted from it. Interior loops must be fully enclosed."
            )
        for j, other in enumerate((holes or [])[:i]):
            if hole_poly.intersection(Polygon(other)).area > 0:
                raise ValueError(
                    f"Holes {j + 1} and {i + 1} overlap each other. Merge them into a "
                    "single loop — overlapping holes would be subtracted twice."
                )

    polygon = Polygon(vertices, holes or None)
    if polygon.area <= 0:
        raise ValueError(
            "The profile encloses no area — its outline is degenerate, or its holes "
            "consume all of it."
        )
    minx, miny, maxx, maxy = polygon.bounds
    span = math.hypot(maxx - minx, maxy - miny)
    if span <= 0 or polygon.buffer(-span * MIN_INSCRIBED_SPAN_RATIO).is_empty:
        raise ValueError(
            f"The profile is a hairline sliver: no part of it is more than "
            f"{2 * span * MIN_INSCRIBED_SPAN_RATIO:.3g} mm thick across a "
            f"{span:.4g} mm span. Check the units and for near-duplicate vertices."
        )
