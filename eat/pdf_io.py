"""
EAT PDF engineering-drawing import — profile geometry and extrusion length
from a 2D drawing sheet, without needing a DXF.

This reads *vector* PDF drawings (CAD-plotted, where the linework is real
path geometry and the callouts are real text). It is not an OCR/vision
importer: a scanned or rasterised sheet fails immediately with a message
saying so, rather than guessing at geometry it cannot actually measure.

Why that is the right line to draw: everything below depends on measuring
the drawing to sub-0.1mm and on telling one *class* of line from another.
Both are exact operations on vector input and estimates on a raster one,
and a silently-wrong profile is worse here than a refusal — it would feed
a plausible-looking but wrong section straight into a beam analysis.


What a drawing sheet actually contains
--------------------------------------
A profile drawing is mostly *not* the profile. The reference sheets this
was built against (Dexory B18 tower extrusions, A3 landscape) carry, on
one page: the cross-section view, a separate much-smaller-scale side
elevation showing the cut length, a revision table, a notes block, a
title block, GD&T frames, datum symbols, surface-finish symbols, leader
lines, centrelines, and 20-40 dimension callouts. The linework of the
annotation vastly outnumbers the linework of the part.

Three independent signals separate them, and all three are used:

1. **Line weight (ISO 128).** Drawings draw visible outlines with a
   *thick* continuous line and dimension/extension/leader lines with a
   *thin* one. The reference sheets use the standard 0.25mm/0.18mm pair
   exactly (0.70866pt/0.51024pt in the PDF). This is a drafting
   convention with a published basis, not a quirk of one exporter — but
   it is still read from the file rather than hardcoded: see
   `_outline_width_classes`.

2. **Line style.** Centrelines and hidden detail are dashed or
   dash-dotted; profile outlines are continuous. Dashed strokes are
   dropped.

3. **Closure.** Profile geometry closes into loops; dimension and leader
   lines do not. This is the final arbiter — anything surviving (1) and
   (2) that still doesn't chain into a closed loop is discarded, and the
   count of what was discarded is reported.

CAD exporters do not emit a contour as one path. They emit it as a few
hundred fragments — typically one per straight edge and one per fillet,
each re-issued with its own `moveto` — chained end to end. So loops are
recovered by endpoint stitching (`_stitch_loops`), the same approach
`eat.dxf_io` uses for loose LINE/ARC entities.


Scale, and why it is verified rather than trusted
-------------------------------------------------
Getting the scale wrong is the single worst failure available here,
because area scales with its square: reading a 2:1 sheet as 1:1 gives a
profile that looks completely correct and has 4x the true area. So the
stated scale is treated as a claim to be checked, never as an answer.

The check is self-contained in the drawing. Dimension arrowheads are
filled triangles; a linear dimension is a pair of them, anti-parallel and
collinear, whose tips sit exactly on the two extension lines it spans. So
the paper distance between the tips, divided by the scale, must equal the
number printed beside them. `_verify_scale` measures every such pair on
the profile view, matches each to its callout text, and requires a clear
majority to agree with the stated scale before the import is allowed to
succeed. A drawing that cannot corroborate its own scale this way fails.

On the reference sheets this reproduces the printed callouts to about
0.05% (e.g. 14.005 against a stated 14.0, 6.203 against 6.2, 8.149
against 8.15), which is a far tighter check than the geometry needs.


Holes, and the one case that looks like an island
-------------------------------------------------
Loops are classified by nesting depth: depth 0 is the outer boundary,
depth 1 are holes. Depth 2 would be a solid island floating inside a
hole — which **cannot be extruded**, since it would come out as a
separate piece. So any depth-2 loop is by definition not profile
geometry, and is either explained or treated as an error.

In practice there is exactly one thing that produces them, and it is
worth handling rather than rejecting: a fastener hole drawn with its
*end* features. A tapped or counterbored hole appears on the section view
as two concentric circles — the countersink or thread major diameter
outside, the through-drill inside (e.g. "Ø 10.2 X 90°, NEAR SIDE" around
"Ø 8.5 THRU ALL"). Only the inner one runs the full length of the bar;
the outer exists for the first few millimetres at each end. So for a
concentric near-circular pair, the inner circle is the hole and the outer
is discarded, and the discard is reported in `warnings`. Any other
depth-2 loop fails the import rather than being guessed at.


Known limitations (v1, accepted)
--------------------------------
* **Section hatching is not handled.** These reference profiles draw the
  cross-section as an unhatched outline. A sheet that hatches the cut
  material would add a large number of thin parallel lines; they are thin
  and open, so they would be filtered out by line weight and by closure
  rather than corrupting the profile — but hatching that is drawn *thick*
  would not be, and no reference drawing was available to test that
  against. Flagged rather than speculatively coded for.

* **Only straight-line path items are read.** The reference exporter
  polygonises every arc and fillet into short line segments, so curve
  operators never appear. `_page_segments` raises if it meets a real
  Bézier item rather than silently ignoring that part of a contour.

* **The title-block weight is an advisory cross-check only, not a
  gate.** Extracted area x length x density should reproduce the stated
  WEIGHT, and it does to within a few percent — but only a few percent:
  across the reference sheets the implied density lands between 2.68 and
  2.80 g/cm3 against 2.70 for 6063, because the stated figure is a CAD
  number for the *machined* part (end threads, countersinks) and is
  rounded. It is reported in `mass_check` for the caller to show, and
  deliberately does not fail the import. The dimensional check above is
  the one that actually gates correctness, because it is exact.

Usable as a library:

    from eat.pdf_io import import_profile
    r = import_profile("drawing.pdf")
    print(r.vertices, r.holes, r.length_mm, r.scale, r.warnings)

Or from the command line:

    python -m eat.pdf_io drawing.pdf
    python -m eat.pdf_io drawing.pdf --page 2
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from shapely.geometry import Polygon as _ShapelyPolygon

# --- Units -------------------------------------------------------------------

PT_PER_MM = 72.0 / 25.4
MM_PER_PT = 25.4 / 72.0

# --- Linework classification -------------------------------------------------

# A stroke counts as "dark" (part geometry or annotation, not a greyed-out
# sheet frame / dimension line) when every RGB channel is at or below this.
# The reference sheets draw outlines at pure black (0,0,0), dimension lines
# at 50% grey, and the sheet border at 75% grey.
MAX_DARK_CHANNEL = 0.25

# A width class counts as "outline" when it exceeds the thinnest dark solid
# width on the page by more than this factor. The ISO 128 thin/thick pairs
# step by about 1.4x (0.18/0.25, 0.25/0.35, 0.35/0.5), so 1.15 separates the
# classes comfortably while absorbing float noise in the PDF's width values.
OUTLINE_WIDTH_RATIO = 1.15

# Minimum number of outline-class segments before a page is considered to
# have any part geometry at all.
MIN_OUTLINE_SEGMENTS = 20

# What share of a page's outline linework must resolve into closed loops
# before its geometry is trusted. A drawing whose outline lines mostly do
# not close is not a clean sectional view of a part -- it is carrying
# machining detail, hidden edges, section breaks or pictorial views at
# outline weight, and any contour traced out of it would be part guesswork.
#
# Measured: the seven single-profile reference sheets close 95.2% to 99.6%
# of their outline segments. The seven pages of the multi-view machined
# assembly drawing close 37.4% to 68.2%. 0.85 sits in the empty gap.
MIN_CLOSED_LINEWORK_FRACTION = 0.85

# Endpoint snap tolerance when chaining fragments into loops, in points.
# The reference exporter rounds coordinates to 2 decimal places (0.01pt), so
# this must exceed that; it is still ~1% of the smallest real feature on
# these drawings (a 1.5mm fillet is 4.25pt at 2:1), so it cannot merge
# genuinely distinct vertices.
ENDPOINT_SNAP_PT = 0.05

# Where a contour branches, the continuation that turns least is followed
# (see `_stitch_loops`). That choice only means something if it is a clear
# winner, so it must beat the runner-up by at least this many degrees;
# anything tighter is a coin flip and the import is refused instead.
#
# Measured on the reference sheets: every branch the stitcher actually takes
# inside a profile view wins by 75 degrees or more (the tightest is 75.4, on
# Section 1 ScanF A; the others run 76.2 to 80.3). A genuine tie -- the
# T-junctions in the side-elevation views, where both alternatives turn
# exactly 90 degrees -- scores 0.0. The two populations are that far apart,
# so 20 sits in an empty gap rather than being tuned against either.
JUNCTION_MIN_MARGIN_DEG = 20.0

# --- View separation ---------------------------------------------------------

# Loops whose bounding boxes come within this many points of each other are
# taken to belong to the same view. Views on a drawing sheet are separated by
# far more than 4mm; features within one view are not.
VIEW_GAP_PT = 12.0

# A view whose outer loop is longer than this ratio is an elevation/side view
# (a long thin bar), not a cross-section. The reference length views run
# 60:1 to 400:1; the widest real profile among the reference sections is
# 4.9:1 (Section 1 Spine A, 170.5 x 34.5mm).
MAX_PROFILE_ASPECT = 10.0

# A second candidate profile view this close in area to the best one makes
# the sheet ambiguous -- we refuse rather than pick.
AMBIGUOUS_AREA_FRACTION = 0.5

# Candidate views smaller than this fraction of the best one are ignored as
# detail/symbol clutter rather than competing profile views.
MIN_CANDIDATE_AREA_FRACTION = 0.02

# --- Dimension reading -------------------------------------------------------

# How far from a dimension line its callout text may sit, in points, to be
# considered as a possible label for it. This is deliberately generous:
# callouts are routinely placed well off the line they label, on a leader or
# stacked in a column. Picking the *nearest* text is unreliable for exactly
# that reason, so association is resolved by consensus instead (see
# `_scale_consensus`) and this only bounds the candidate set.
DIM_TEXT_MAX_DIST_PT = 150.0

# Two implied scales count as the same when they agree within this. Real
# alternative scales are far apart (1:1 vs 2:1 differ by 100%), so this can
# comfortably absorb rounded callouts -- a "1.5" printed for a true 1.482
# is 1.2% out.
SCALE_BUCKET_TOL = 0.02

# The scale must be corroborated by at least this many independent dimension
# callouts before a profile is read at it.
MIN_VERIFIED_DIMENSIONS = 3

# A dimension is reported as agreeing with its printed value within this
# relative tolerance, or this many mm, whichever is looser. The absolute
# floor matters because callouts are rounded: a 1.5 printed for a measured
# 1.482 is 1.2% out but only 18 microns.
DIM_AGREEMENT_TOL = 0.01
DIM_AGREEMENT_FLOOR_MM = 0.03

# --- Concentric end-feature pairs --------------------------------------------

# A loop counts as near-circular when its area reaches this fraction of the
# circle through its outermost vertex. An inscribed regular 24-gon (how the
# reference exporter polygonises a bolt hole) reaches 0.989.
CIRCULARITY_MIN = 0.95

# Two circles count as concentric when their centroids sit within this
# fraction of the outer one's diameter.
CONCENTRIC_MAX_OFFSET = 0.05


class PdfImportError(Exception):
    """Raised when a PDF drawing cannot be confidently interpreted."""


@dataclass
class DimensionCheck:
    """One dimension callout measured off the drawing and compared to its
    printed value."""

    stated: float
    measured: float
    text: str
    agrees: bool
    position: tuple[float, float]  # page coords, points

    @property
    def error_pct(self) -> float:
        return (self.measured - self.stated) / self.stated * 100.0 if self.stated else 0.0


@dataclass
class ViewInfo:
    """One cluster of outline geometry on the sheet -- a "view"."""

    bbox_pt: tuple[float, float, float, float]
    loop_count: int
    outer_area_pt2: float
    aspect: float
    scale: float | None
    scale_source: str
    role: str  # "profile", "length", or "rejected: <reason>"


@dataclass
class MassCheck:
    """Advisory comparison of extracted area against the title block's
    stated part weight. Never gates the import -- see the module docstring."""

    stated_weight_g: float
    length_mm: float
    area_mm2: float
    implied_density_g_cm3: float
    note: str


@dataclass
class PdfImportResult:
    vertices: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]]
    page_index: int
    scale: float
    scale_source: str
    length_mm: float | None
    length_source: str
    dimension_checks: list[DimensionCheck]
    views: list[ViewInfo]
    warnings: list[str]
    title_block: dict[str, str] = field(default_factory=dict)
    mass_check: MassCheck | None = None
    # Where the extracted millimetre coordinates came from on the page, so an
    # overlay can be drawn back onto the original drawing for review:
    #   x_pt = view_origin_pt[0] + x_mm / mm_per_pt
    #   y_pt = view_origin_pt[1] - y_mm / mm_per_pt
    view_origin_pt: tuple[float, float] = (0.0, 0.0)
    mm_per_pt: float = MM_PER_PT

    def to_page_points(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        ox, oy = self.view_origin_pt
        return [(ox + x / self.mm_per_pt, oy - y / self.mm_per_pt) for x, y in points]

    @property
    def area_mm2(self) -> float:
        return _polygon_area(self.vertices) - sum(_polygon_area(h) for h in self.holes)


# --- Geometry helpers --------------------------------------------------------


def _polygon_area(points: list[tuple[float, float]]) -> float:
    a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    a = 0.0
    cx = 0.0
    cy = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-12:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (sum(xs) / n, sum(ys) / n)
    return (cx / (3.0 * a), cy / (3.0 * a))


def _circularity(points: list[tuple[float, float]]) -> float:
    """Area as a fraction of the circle through the outermost vertex. 1.0 for
    a true circle, 0.989 for an inscribed 24-gon, well below 0.95 for
    anything that isn't round."""
    cx, cy = _centroid(points)
    r_max = max(math.dist((cx, cy), p) for p in points)
    if r_max <= 1e-9:
        return 0.0
    return _polygon_area(points) / (math.pi * r_max * r_max)


def _bbox_gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Shortest distance between two axis-aligned boxes; 0 if they overlap."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


# --- Stage 1: harvest outline linework ---------------------------------------


def _page_segments(page) -> tuple[list[tuple[tuple[float, float], tuple[float, float]]], list[str]]:
    """Every dark, solid, outline-weight line segment on the page.

    Returns the segments plus any warnings about content that was skipped.
    """
    warnings: list[str] = []
    strokes = []
    for d in page.get_drawings():
        if d.get("type") != "s":
            continue  # fills are arrowheads/symbols, handled separately
        width = d.get("width")
        colour = d.get("color")
        if width is None or colour is None:
            continue
        if max(colour) > MAX_DARK_CHANNEL:
            continue
        dashes = (d.get("dashes") or "[] 0").strip()
        if dashes not in ("[] 0", "[]0", ""):
            continue  # dashed => centreline / hidden detail
        strokes.append((width, d["items"]))

    if not strokes:
        return [], warnings

    thin = min(w for w, _ in strokes)
    outline_widths = {w for w, _ in strokes if w > thin * OUTLINE_WIDTH_RATIO}
    if not outline_widths:
        return [], warnings

    segments = []
    curves = 0
    for width, items in strokes:
        if width not in outline_widths:
            continue
        for it in items:
            if it[0] == "l":
                p1 = (it[1].x, it[1].y)
                p2 = (it[2].x, it[2].y)
                if math.dist(p1, p2) >= 1e-9:
                    segments.append((p1, p2))
            elif it[0] in ("c", "qu", "re"):
                curves += 1
    if curves:
        raise PdfImportError(
            f"This drawing's outline geometry contains {curves} curve/rectangle path "
            "operator(s). v1 reads straight-line path items only (CAD plotters normally "
            "polygonise arcs into line segments); this file would need curve flattening "
            "support before its profile could be read accurately."
        )
    return segments, warnings


def _outline_width_classes(page) -> list[float]:
    """Diagnostic: the dark solid stroke widths present, thinnest first."""
    widths = set()
    for d in page.get_drawings():
        if d.get("type") != "s":
            continue
        width = d.get("width")
        colour = d.get("color")
        if width is None or colour is None or max(colour) > MAX_DARK_CHANNEL:
            continue
        dashes = (d.get("dashes") or "[] 0").strip()
        if dashes not in ("[] 0", "[]0", ""):
            continue
        widths.add(round(width, 5))
    return sorted(widths)


# --- Stage 2: stitch fragments into closed loops -----------------------------


def _snap(p: tuple[float, float]) -> tuple[int, int]:
    return (round(p[0] / ENDPOINT_SNAP_PT), round(p[1] / ENDPOINT_SNAP_PT))


def _stitch_loops(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> tuple[list[list[tuple[float, float]]], int, list[tuple[tuple[float, float], float]]]:
    """Chain segments into closed loops by shared endpoints.

    Where more than two segment-ends meet, the contour branches: something
    is drawn on top of it. The continuation that turns least is taken,
    because a drawn contour is tangent-continuous through its fillets while
    a superimposed feature meets it at an angle. On the reference sheets
    this is what separates a bore from the countersink circle drawn over it
    — arriving along the bore wall, continuing along the bore deviates by
    under 4 degrees where turning onto the countersink arc would deviate by
    79 or more.

    That is a judgement the drawing itself does not settle, so every branch
    taken is returned with the angular margin by which it was preferred, and
    the caller refuses the import if any margin is too small to be
    meaningful (see JUNCTION_MIN_MARGIN_DEG).

    Returns (closed loops, number of leftover open chains, branch decisions
    as (position, margin in degrees)).
    """
    # Drop exact duplicates; CAD exports occasionally emit an edge twice.
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for p1, p2 in segments:
        k1, k2 = _snap(p1), _snap(p2)
        key = (k1, k2) if k1 <= k2 else (k2, k1)
        if key in seen:
            continue
        seen.add(key)
        segs.append((p1, p2))

    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, (p1, p2) in enumerate(segs):
        adjacency.setdefault(_snap(p1), []).append((i, 0))
        adjacency.setdefault(_snap(p2), []).append((i, 1))

    used = [False] * len(segs)
    loops: list[list[tuple[float, float]]] = []
    open_chains = 0
    branches: list[tuple[tuple[float, float], float]] = []

    for start_idx in range(len(segs)):
        if used[start_idx]:
            continue
        used[start_idx] = True
        p_start, p_cur = segs[start_idx]
        points = [p_start, p_cur]
        start_key = _snap(p_start)
        cur_key = _snap(p_cur)
        prev_point = p_start

        while cur_key != start_key:
            options = [(i, e) for i, e in adjacency.get(cur_key, []) if not used[i]]
            if not options:
                break
            if len(options) == 1:
                idx, end = options[0]
            else:
                # straightest continuation
                inbound = (p_cur[0] - prev_point[0], p_cur[1] - prev_point[1])
                norm = math.hypot(*inbound) or 1.0
                inbound = (inbound[0] / norm, inbound[1] / norm)
                scored = []
                for idx_, end_ in options:
                    nxt = segs[idx_][1] if end_ == 0 else segs[idx_][0]
                    out = (nxt[0] - p_cur[0], nxt[1] - p_cur[1])
                    n2 = math.hypot(*out) or 1.0
                    dot = (out[0] / n2) * inbound[0] + (out[1] / n2) * inbound[1]
                    turn = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
                    scored.append((turn, idx_, end_))
                scored.sort()
                branches.append((p_cur, scored[1][0] - scored[0][0]))
                _, idx, end = scored[0]
            used[idx] = True
            prev_point = p_cur
            p_cur = segs[idx][1] if end == 0 else segs[idx][0]
            points.append(p_cur)
            cur_key = _snap(p_cur)

        if cur_key == start_key:
            if len(points) > 3:
                loops.append(points[:-1])  # closing point duplicates the start
        else:
            open_chains += 1

    return loops, open_chains, branches


# --- Stage 3: group loops into views -----------------------------------------


def _cluster_views(loops: list[list[tuple[float, float]]]) -> list[list[int]]:
    """Union-find loops whose bounding boxes are within VIEW_GAP_PT."""
    boxes = [_bbox(l) for l in loops]
    parent = list(range(len(loops)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(loops)):
        for j in range(i + 1, len(loops)):
            if _bbox_gap(boxes[i], boxes[j]) <= VIEW_GAP_PT:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(len(loops)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


# --- Text and title block ----------------------------------------------------


def _text_lines(page) -> list[tuple[str, tuple[float, float, float, float]]]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                out.append((text, tuple(line["bbox"])))
    return out


def _field_right_of(
    lines: list[tuple[str, tuple[float, float, float, float]]], label: str
) -> str | None:
    """Title-block fields print as a label and a value in separate text runs,
    on the same row. Returns the nearest run to the right of `label`."""
    for text, bb in lines:
        if not text.upper().startswith(label):
            continue
        cy = (bb[1] + bb[3]) / 2.0
        row_tol = (bb[3] - bb[1]) * 0.6
        best = None
        for text2, bb2 in lines:
            if bb2[0] < bb[2] - 1.0:
                continue
            cy2 = (bb2[1] + bb2[3]) / 2.0
            if abs(cy2 - cy) > row_tol:
                continue
            if best is None or bb2[0] < best[1][0]:
                best = (text2, bb2)
        if best:
            return best[0]
    return None


_SCALE_RE = re.compile(r"SCALE\s*:?\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", re.I)


def _scale_labels(
    lines: list[tuple[str, tuple[float, float, float, float]]],
) -> list[tuple[float, tuple[float, float, float, float]]]:
    out = []
    for text, bb in lines:
        m = _SCALE_RE.search(text)
        if m:
            num, den = float(m.group(1)), float(m.group(2))
            if den:
                out.append((num / den, bb))
    return out


# --- Dimension callouts ------------------------------------------------------


@dataclass
class _Arrowhead:
    apex: tuple[float, float]
    direction: tuple[float, float]
    colour: tuple


def _arrowheads(page) -> list[_Arrowhead]:
    """Dimension/leader arrowheads: filled triangles. The apex is the vertex
    opposite the shortest edge."""
    heads = []
    for d in page.get_drawings():
        if d.get("type") != "f":
            continue
        colour = d.get("fill")
        cur: list[tuple[float, float]] = []
        subs: list[list[tuple[float, float]]] = []
        for it in d["items"]:
            if it[0] != "l":
                continue
            p1 = (it[1].x, it[1].y)
            p2 = (it[2].x, it[2].y)
            if cur and math.dist(cur[-1], p1) < 1e-6:
                cur.append(p2)
            else:
                if len(cur) > 1:
                    subs.append(cur)
                cur = [p1, p2]
        if len(cur) > 1:
            subs.append(cur)
        for s in subs:
            pts = s[:-1] if len(s) > 1 and math.dist(s[0], s[-1]) < 1e-6 else list(s)
            if len(pts) != 3:
                continue
            edges = [
                math.dist(pts[1], pts[2]),
                math.dist(pts[0], pts[2]),
                math.dist(pts[0], pts[1]),
            ]
            k = min(range(3), key=lambda i: edges[i])
            apex = pts[k]
            base = [pts[j] for j in range(3) if j != k]
            mid = ((base[0][0] + base[1][0]) / 2.0, (base[0][1] + base[1][1]) / 2.0)
            length = math.dist(apex, mid)
            if length < 1e-6:
                continue
            heads.append(
                _Arrowhead(
                    apex=apex,
                    direction=((apex[0] - mid[0]) / length, (apex[1] - mid[1]) / length),
                    colour=colour,
                )
            )
    return heads


_NUM_RE = re.compile(r"^\(?(\d+(?:\.\d+)?)\)?$")


def _callout_value(text: str) -> float | None:
    """The nominal value from a dimension callout, or None if it isn't one.

    Handles the "2X 38.0 ±0.3" / "(136)" / "24.0 ±0.15" forms. The count
    prefix is dropped: "2X 38.0" labels one 38.0 span, not 76.
    """
    t = text.strip()
    t = re.sub(r"^\d+\s*X\s*", "", t, flags=re.I)  # "2X ", "4 X "
    t = re.split(r"[±]", t)[0].strip()
    t = t.replace("⌀", "").replace("Ø", "").strip()
    m = _NUM_RE.match(t)
    if not m:
        return None
    value = float(m.group(1))
    return value if value > 0 else None


def _dimension_pairs(heads: list[_Arrowhead]) -> list[tuple[float, tuple[float, float]]]:
    """Linear dimensions, as (span in points, midpoint).

    A linear dimension is two arrowheads that are anti-parallel, collinear,
    and *adjacent* -- no third arrowhead lying between them. The adjacency
    test is what stops a chained pair of 14.0 dimensions being read as one
    28.0 span across the outside of both.
    """
    pairs = []
    for i in range(len(heads)):
        for j in range(i + 1, len(heads)):
            a, b = heads[i], heads[j]
            if a.colour != b.colour:
                continue
            if a.direction[0] * b.direction[0] + a.direction[1] * b.direction[1] > -0.999:
                continue
            vx, vy = b.apex[0] - a.apex[0], b.apex[1] - a.apex[1]
            span = math.hypot(vx, vy)
            if span < 1e-6:
                continue
            ux, uy = vx / span, vy / span
            if abs(ux * a.direction[0] + uy * a.direction[1]) < 0.999:
                continue
            blocked = False
            for k, c in enumerate(heads):
                if k in (i, j) or c.colour != a.colour:
                    continue
                wx, wy = c.apex[0] - a.apex[0], c.apex[1] - a.apex[1]
                t = wx * ux + wy * uy
                perp = abs(wx * -uy + wy * ux)
                if 1e-6 < t < span - 1e-6 and perp < 1.0:
                    blocked = True
                    break
            if blocked:
                continue
            pairs.append((span, ((a.apex[0] + b.apex[0]) / 2.0, (a.apex[1] + b.apex[1]) / 2.0)))
    return pairs


def _point_to_segment(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _heads_in(
    heads: list[_Arrowhead], bbox: tuple[float, float, float, float] | None
) -> list[_Arrowhead]:
    if bbox is None:
        return heads
    pad = VIEW_GAP_PT * 6
    region = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    return [
        h
        for h in heads
        if region[0] <= h.apex[0] <= region[2] and region[1] <= h.apex[1] <= region[3]
    ]


def _scale_consensus(
    heads: list[_Arrowhead],
    lines: list[tuple[str, tuple[float, float, float, float]]],
) -> tuple[float | None, list[DimensionCheck], int]:
    """Work out the scale a view is actually drawn at, by consensus among its
    dimension callouts.

    Matching each dimension line to *its* callout by proximity alone is not
    reliable — callouts sit on leaders, or stack in a column where a
    neighbour's text is nearer than the right one. So instead every plausible
    (dimension line, nearby callout) combination casts a vote for the scale it
    would imply, each dimension line voting at most once per candidate scale.
    A correct pairing implies the true scale; the mismatched ones scatter, so
    the true scale wins by a wide margin.

    Returns (consensus scale or None, the dimensions it explains, number of
    dimension lines found).
    """
    pairs = _dimension_pairs(heads)
    votes: list[tuple[int, float, tuple[float, float], str, float, float, float]] = []
    for pi, (span, mid) in enumerate(pairs):
        paper_mm = span * MM_PER_PT
        for text, bb in lines:
            value = _callout_value(text)
            if value is None:
                continue
            centre = ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)
            dist = math.dist(centre, mid)
            if dist > DIM_TEXT_MAX_DIST_PT:
                continue
            votes.append((pi, span, mid, text, value, paper_mm / value, dist))
    if not votes:
        return None, [], len(pairs)

    best: tuple[int, float, dict] | None = None
    for candidate in sorted({v[5] for v in votes}):
        chosen: dict[int, tuple[float, tuple]] = {}
        for v in votes:
            if abs(v[5] - candidate) / candidate > SCALE_BUCKET_TOL:
                continue
            if v[0] not in chosen or v[6] < chosen[v[0]][0]:
                chosen[v[0]] = (v[6], v)
        if not chosen:
            continue
        total_dist = sum(d for d, _ in chosen.values())
        key = (len(chosen), -total_dist)
        if best is None or key > (best[0], -best[1]):
            best = (len(chosen), total_dist, chosen)

    if best is None:
        return None, [], len(pairs)

    chosen = best[2]
    # Refine: average the implied scales the winning bucket actually holds.
    scale = sum(v[5] for _, v in chosen.values()) / len(chosen)

    checks = []
    for _pair_index, (_dist, v) in sorted(chosen.items()):
        _, span, mid, text, value, _implied, _d = v
        measured = span * MM_PER_PT / scale
        tol = max(DIM_AGREEMENT_TOL * value, DIM_AGREEMENT_FLOOR_MM)
        checks.append(
            DimensionCheck(
                stated=value,
                measured=measured,
                text=text,
                agrees=abs(measured - value) <= tol,
                position=mid,
            )
        )
    checks.sort(key=lambda c: -c.stated)
    return scale, checks, len(pairs)


# --- Stage: nesting, holes, end-feature pairs --------------------------------


def _classify_loops(
    loops: list[list[tuple[float, float]]],
) -> tuple[int, list[int], list[str]]:
    """Return (outer index, hole indices, warnings), resolving concentric
    end-feature pairs and rejecting anything else nested two deep."""
    polys = []
    for pts in loops:
        poly = _ShapelyPolygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        polys.append(poly)
    areas = [_polygon_area(l) for l in loops]
    order = sorted(range(len(loops)), key=lambda i: -areas[i])
    outer = order[0]

    depth = {}
    parent = {}
    for i in range(len(loops)):
        rep = polys[i].representative_point()
        containing = [
            j for j in range(len(loops)) if j != i and areas[j] > areas[i] and polys[j].contains(rep)
        ]
        depth[i] = len(containing)
        if containing:
            parent[i] = min(containing, key=lambda j: areas[j])

    warnings: list[str] = []
    holes = [i for i in range(len(loops)) if depth[i] == 1]
    deeper = [i for i in range(len(loops)) if depth[i] >= 2]

    for i in deeper:
        if depth[i] != 2:
            raise PdfImportError(
                f"A loop in the profile view is nested {depth[i]} levels deep. An extruded "
                "profile can only have an outer boundary and holes in it -- anything deeper "
                "cannot be produced as a single extrusion, so this drawing needs manual review."
            )
        p = parent.get(i)
        if p is None or p not in holes:
            raise PdfImportError(
                "The profile view contains a loop nested inside a hole whose parent could "
                "not be identified. A solid island inside a hole cannot be extruded as one "
                "piece, so this drawing needs manual review."
            )
        inner_circ = _circularity(loops[i])
        outer_circ = _circularity(loops[p])
        ci = _centroid(loops[i])
        cp = _centroid(loops[p])
        d_outer = 2.0 * math.sqrt(areas[p] / math.pi)
        concentric = math.dist(ci, cp) <= CONCENTRIC_MAX_OFFSET * d_outer
        if inner_circ >= CIRCULARITY_MIN and outer_circ >= CIRCULARITY_MIN and concentric:
            # Coaxial end feature: countersink / thread major diameter drawn
            # around the through-drill. Only the inner circle runs the full
            # length of the bar, so it is the hole; the outer is annotation.
            holes.remove(p)
            holes.append(i)
            warnings.append(
                f"Hole at ({cp[0]:.1f}, {cp[1]:.1f}) is drawn as two concentric circles "
                f"(outer d={d_outer * MM_PER_PT:.2f}pt-scale, inner "
                f"d={2 * math.sqrt(areas[i] / math.pi) * MM_PER_PT:.2f}pt-scale): read as a "
                "countersink/thread end feature around a through-hole. The inner (through) "
                "circle was used; the outer was ignored, since it only exists at the bar ends."
            )
        else:
            raise PdfImportError(
                "The profile view contains a loop inside a hole that is not a concentric "
                "round end feature (circularity "
                f"{inner_circ:.2f}/{outer_circ:.2f}, concentric={concentric}). A solid island "
                "inside a hole cannot be extruded as one piece, so this drawing needs "
                "manual review."
            )

    return outer, holes, warnings


# --- Page-level extraction ---------------------------------------------------


def _extract_page(page, page_index: int, source_label: str) -> PdfImportResult:
    lines = _text_lines(page)
    segments, warnings = _page_segments(page)
    if len(segments) < MIN_OUTLINE_SEGMENTS:
        widths = _outline_width_classes(page)
        raster = len(page.get_images(full=True))
        if not widths and raster:
            raise PdfImportError(
                f"Page {page_index + 1} of '{source_label}' has no vector linework, only "
                f"{raster} raster image(s). This looks like a scanned or exported-as-image "
                "drawing; EAT reads vector PDFs, where the geometry is real paths it can "
                "measure exactly. Re-export the drawing as a vector PDF (or import the DXF)."
            )
        raise PdfImportError(
            f"Page {page_index + 1} of '{source_label}' has no outline-weight linework to "
            f"read a profile from (found {len(segments)} candidate segments; dark solid "
            f"stroke widths on the page: {widths or 'none'}). Engineering drawings draw "
            "visible outlines with a thicker line than dimension and leader lines; without "
            "that distinction the profile cannot be told apart from the annotation."
        )

    loops, open_chains, branches = _stitch_loops(segments)
    if not loops:
        raise PdfImportError(
            f"Page {page_index + 1} of '{source_label}': none of the {len(segments)} "
            "outline segments chain into a closed loop, so there is no profile boundary "
            "to read."
        )

    closed_fraction = sum(len(l) for l in loops) / len(segments)
    if closed_fraction < MIN_CLOSED_LINEWORK_FRACTION:
        raise PdfImportError(
            f"Page {page_index + 1} of '{source_label}': only {closed_fraction * 100:.0f}% of "
            f"the outline linework closes into loops ({open_chains} run(s) left open). A "
            "clean sectional view of an extrusion closes almost all of it; a page that does "
            "not is carrying machining detail, hidden edges, section breaks or pictorial "
            "views at outline weight, and any profile traced out of it would be partly "
            "guesswork. If this sheet does contain a plain cross-section on another page, "
            "import that page instead; otherwise use the profile's DXF."
        )

    clusters = _cluster_views(loops)
    scale_labels = _scale_labels(lines)
    title_scale = None
    title_scale_bb = None
    view_label_scales: list[tuple[float, tuple[float, float, float, float]]] = []

    cluster_boxes = []
    for members in clusters:
        pts = [p for i in members for p in loops[i]]
        cluster_boxes.append(_bbox(pts))

    for value, bb in scale_labels:
        centre = ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)
        near = min(
            (_bbox_gap((centre[0], centre[1], centre[0], centre[1]), cb) for cb in cluster_boxes),
            default=1e9,
        )
        if near <= VIEW_GAP_PT * 4:
            view_label_scales.append((value, bb))
        elif title_scale is None:
            title_scale = value
            title_scale_bb = bb

    if title_scale is None and view_label_scales:
        # Every scale statement sat next to a view; the title block one is the
        # bottom-right-most.
        value, bb = max(view_label_scales, key=lambda vb: (vb[1][1], vb[1][0]))
        title_scale = value
        view_label_scales.remove((value, bb))

    # Build candidate views
    views: list[ViewInfo] = []
    candidates = []
    for ci, members in enumerate(clusters):
        box = cluster_boxes[ci]
        outer_local = max(members, key=lambda i: _polygon_area(loops[i]))
        area = _polygon_area(loops[outer_local])
        w = box[2] - box[0]
        h = box[3] - box[1]
        aspect = max(w, h) / max(min(w, h), 1e-9)
        # view-local scale label, if any
        local_scale = None
        best_d = None
        for value, bb in view_label_scales:
            centre = ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)
            d = _bbox_gap((centre[0], centre[1], centre[0], centre[1]), box)
            if d <= VIEW_GAP_PT * 4 and (best_d is None or d < best_d):
                best_d = d
                local_scale = value
        candidates.append(
            dict(
                ci=ci,
                members=members,
                box=box,
                outer=outer_local,
                area=area,
                aspect=aspect,
                scale=local_scale if local_scale is not None else title_scale,
                scale_source="view scale label" if local_scale is not None else "title block",
            )
        )

    max_area = max(c["area"] for c in candidates)
    profile_candidates = [
        c
        for c in candidates
        if c["aspect"] <= MAX_PROFILE_ASPECT
        and c["area"] >= MIN_CANDIDATE_AREA_FRACTION * max_area
        and len(c["members"]) >= 1
    ]
    profile_candidates.sort(key=lambda c: -c["area"])

    if not profile_candidates:
        raise PdfImportError(
            f"Page {page_index + 1} of '{source_label}' has no view that looks like a "
            "cross-section: every closed outline on the sheet is either a long thin "
            f"elevation (aspect ratio over {MAX_PROFILE_ASPECT:g}:1) or too small to be the "
            "profile. If the profile is on another page, pass that page explicitly."
        )

    chosen = profile_candidates[0]
    if len(profile_candidates) > 1:
        runner_up = profile_candidates[1]
        if runner_up["area"] >= AMBIGUOUS_AREA_FRACTION * chosen["area"]:
            def describe(c):
                b = c["box"]
                sc = c["scale"] or 1.0
                return (
                    f"{(b[2] - b[0]) * MM_PER_PT / sc:.1f} x {(b[3] - b[1]) * MM_PER_PT / sc:.1f}mm "
                    f"at ({b[0]:.0f}, {b[1]:.0f})pt, {len(c['members'])} loop(s)"
                )

            raise PdfImportError(
                f"Page {page_index + 1} of '{source_label}' has more than one view that "
                "could be the profile cross-section, and they are too similar in size to "
                f"choose between: {describe(chosen)}; and {describe(runner_up)}"
                + (
                    f"; and {describe(profile_candidates[2])}"
                    if len(profile_candidates) > 2
                    else ""
                )
                + ". Refusing to guess which one is the extrusion profile — this sheet "
                "needs the view identified manually, or the profile imported from its DXF."
            )

    # --- establish the scale, and corroborate it against the drawing --------
    stated_scale = chosen["scale"]
    heads = _arrowheads(page)
    measured_scale, checks, n_dims = _scale_consensus(_heads_in(heads, chosen["box"]), lines)
    agreeing = [c for c in checks if c.agrees]

    if measured_scale is None or len(agreeing) < MIN_VERIFIED_DIMENSIONS:
        raise PdfImportError(
            f"Page {page_index + 1} of '{source_label}': the profile view's scale could not "
            f"be corroborated from the drawing itself. Found {n_dims} dimension line(s) and "
            f"could reconcile {len(agreeing)} of them with a printed callout (need at least "
            f"{MIN_VERIFIED_DIMENSIONS})."
            + (
                f" The title block states {_fmt_scale(stated_scale)}, but reading the profile "
                "at an unverified scale risks a section that looks right and is the wrong "
                "size, so this import is refused."
                if stated_scale
                else " The sheet also states no scale."
            )
        )

    if stated_scale is None:
        scale = measured_scale
        scale_source = (
            f"measured from {len(agreeing)} dimension callout(s) — the sheet states no scale"
        )
        warnings.append(
            f"No 'SCALE n:m' text was found on this sheet. The scale was measured from the "
            f"drawing's own dimension callouts instead ({_fmt_scale(measured_scale)}, "
            f"corroborated by {len(agreeing)} of {n_dims} dimension line(s))."
        )
    elif abs(measured_scale - stated_scale) / stated_scale > SCALE_BUCKET_TOL:
        raise PdfImportError(
            f"Page {page_index + 1} of '{source_label}' states a scale of "
            f"{_fmt_scale(stated_scale)}, but its own dimension callouts measure "
            f"consistently at {_fmt_scale(measured_scale)} "
            f"({len(agreeing)} of {n_dims} dimension line(s) agree with that, e.g. "
            + ", ".join(f"{c.text!r} measures {c.measured:.3f}mm" for c in agreeing[:3])
            + "). The stated and drawn scales disagree, so the profile's true size is "
            "unclear; refusing to guess which is right."
        )
    else:
        scale = stated_scale
        scale_source = (
            f"{chosen['scale_source']}, confirmed by {len(agreeing)} of {n_dims} "
            "dimension callout(s)"
        )

    # Re-measure the checks at the scale actually adopted.
    for c in checks:
        c.measured = c.measured * measured_scale / scale
        c.agrees = abs(c.measured - c.stated) <= max(
            DIM_AGREEMENT_TOL * c.stated, DIM_AGREEMENT_FLOOR_MM
        )

    # --- branch decisions taken inside the view we are about to read --------
    box = chosen["box"]
    in_view = [
        (pos, margin)
        for pos, margin in branches
        if box[0] - 2.0 <= pos[0] <= box[2] + 2.0 and box[1] - 2.0 <= pos[1] <= box[3] + 2.0
    ]
    unclear = [(pos, m) for pos, m in in_view if m < JUNCTION_MIN_MARGIN_DEG]
    if unclear:
        where = ", ".join(f"({p[0]:.1f}, {p[1]:.1f})pt margin {m:.1f}deg" for p, m in unclear[:4])
        raise PdfImportError(
            f"The profile outline on page {page_index + 1} of '{source_label}' branches at "
            f"{len(unclear)} point(s) where it is not clear which way the contour continues "
            f"(the two candidate directions differ by less than {JUNCTION_MIN_MARGIN_DEG:g} "
            f"degrees): {where}. Tracing on past that would be a guess at the profile's "
            "actual shape, so this import is refused."
        )
    if in_view:
        warnings.append(
            f"The profile outline branches at {len(in_view)} point(s) — geometry drawn on top "
            "of the section, typically a countersink or thread circle around a bore. The "
            "contour was followed through tangentially at each (narrowest margin "
            f"{min(m for _, m in in_view):.0f} degrees), and the superimposed feature left out "
            "of the section."
        )

    # --- assemble the polygon ----------------------------------------------
    member_loops = [loops[i] for i in chosen["members"]]
    outer_idx, hole_idxs, hole_warnings = _classify_loops(member_loops)
    warnings.extend(hole_warnings)

    ox, oy = chosen["box"][0], chosen["box"][3]
    factor = MM_PER_PT / scale

    def to_mm(pts):
        # PDF y grows downward; EAT/CAD y grows upward. Origin moves to the
        # view's bottom-left corner so coordinates are positive millimetres.
        return [((p[0] - ox) * factor, (oy - p[1]) * factor) for p in pts]

    vertices = to_mm(member_loops[outer_idx])
    holes = [to_mm(member_loops[i]) for i in hole_idxs]

    # --- length -------------------------------------------------------------
    length_mm, length_source = _find_length(
        page, heads, lines, candidates, chosen, title_scale, scale
    )

    # --- title block --------------------------------------------------------
    title_block = {}
    for label, key in (
        ("PART #", "part_number"),
        ("MATERIAL", "material"),
        ("TITLE", "title"),
        ("FINISH", "finish"),
        ("WEIGHT", "weight_g"),
    ):
        value = _field_right_of(lines, label)
        if value:
            title_block[key] = value

    result = PdfImportResult(
        vertices=vertices,
        holes=holes,
        page_index=page_index,
        scale=scale,
        scale_source=scale_source,
        length_mm=length_mm,
        length_source=length_source,
        dimension_checks=checks,
        views=[],
        warnings=warnings,
        title_block=title_block,
        view_origin_pt=(ox, oy),
        mm_per_pt=factor,
    )

    for c in candidates:
        if c is chosen:
            role = "profile"
        elif c["aspect"] > MAX_PROFILE_ASPECT:
            role = "rejected: long thin view (elevation / length)"
        elif c["area"] < MIN_CANDIDATE_AREA_FRACTION * max_area:
            role = "rejected: too small"
        else:
            role = "rejected: smaller than the chosen profile view"
        result.views.append(
            ViewInfo(
                bbox_pt=c["box"],
                loop_count=len(c["members"]),
                outer_area_pt2=c["area"],
                aspect=c["aspect"],
                scale=c["scale"],
                scale_source=c["scale_source"],
                role=role,
            )
        )

    if open_chains:
        warnings.append(
            f"{open_chains} run(s) of outline-weight linework did not close into a loop and "
            "were ignored (typically thread-representation arcs, view frames, or symbols "
            "drawn at outline weight)."
        )

    result.mass_check = _mass_check(title_block, length_mm, result.area_mm2)
    return result


def _fmt_scale(scale: float) -> str:
    if scale >= 1:
        return f"{scale:g}:1"
    return f"1:{1 / scale:g}"


def _find_length(
    page, heads, lines, candidates, chosen, title_scale, profile_scale
) -> tuple[float | None, str]:
    """The extrusion's cut length, from a dimension on a view other than the
    profile — normally the side elevation, drawn at its own much smaller
    scale (1:20 on the reference sheets, against 2:1 for the section).

    The value is read from the callout text, but only accepted once the
    drawn geometry independently measures the same thing at that view's own
    scale. That is what distinguishes a real length dimension from any other
    number printed nearby.
    """
    best: tuple[float, str] | None = None
    for c in candidates:
        if c is chosen:
            continue
        view_scale = c["scale"] or title_scale
        heads_here = _heads_in(heads, c["box"])
        measured_scale, checks, _n = _scale_consensus(heads_here, lines)
        if measured_scale is None:
            continue
        # Only trust this view's dimensions if what they measure agrees with
        # the scale the sheet says the view is drawn at.
        if view_scale and abs(measured_scale - view_scale) / view_scale > SCALE_BUCKET_TOL:
            continue
        shown_scale = view_scale or measured_scale
        for check in checks:
            if not check.agrees:
                continue
            if best is None or check.stated > best[0]:
                span_mm = (c["box"][2] - c["box"][0]) * MM_PER_PT / measured_scale
                best = (
                    check.stated,
                    (
                        f"dimension {check.text!r} on the side view at "
                        f"{_fmt_scale(shown_scale)} — that view is drawn {span_mm:.0f}mm "
                        f"long, which corroborates the callout to {check.error_pct:+.2f}%"
                    ),
                )
    if best:
        return best
    return (
        None,
        "no length dimension could be read: no view other than the profile carried a "
        "dimension callout that its own drawn geometry corroborated",
    )


_DENSITY_G_CM3 = {"6063": 2.70, "6061": 2.70, "6005": 2.70, "6082": 2.70, "7075": 2.81}


def _mass_check(title_block: dict, length_mm: float | None, area_mm2: float) -> MassCheck | None:
    raw = title_block.get("weight_g")
    if not raw or not length_mm or area_mm2 <= 0:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not m:
        return None
    weight = float(m.group(1))
    volume_cm3 = area_mm2 * length_mm / 1000.0
    if volume_cm3 <= 0:
        return None
    implied = weight / volume_cm3
    material = (title_block.get("material") or "").lower()
    nominal = None
    for alloy, rho in _DENSITY_G_CM3.items():
        if alloy in material:
            nominal = rho
            break
    if nominal:
        err = (implied - nominal) / nominal * 100.0
        note = (
            f"Stated weight {weight:g}g over {length_mm:g}mm implies a density of "
            f"{implied:.3f} g/cm3 against {nominal:.2f} for this alloy ({err:+.1f}%). "
            "Advisory only — the title-block weight is a CAD figure for the machined "
            "part and is rounded."
        )
    else:
        note = (
            f"Stated weight {weight:g}g over {length_mm:g}mm implies a density of "
            f"{implied:.3f} g/cm3 for the extracted area. Advisory only."
        )
    return MassCheck(
        stated_weight_g=weight,
        length_mm=length_mm,
        area_mm2=area_mm2,
        implied_density_g_cm3=implied,
        note=note,
    )


# --- Public API --------------------------------------------------------------


def _require_pdf(header: bytes, source_label: str) -> None:
    """PyMuPDF happily opens plain text, images and e-books as documents, so a
    mistyped file would otherwise fail much later with a confusing message
    about missing linework."""
    if not header.lstrip()[:5].startswith(b"%PDF-"):
        raise PdfImportError(
            f"'{source_label}' is not a PDF file (it does not start with the '%PDF-' "
            "header). This importer reads vector PDF engineering drawings."
        )


def _import_from_doc(doc, source_label: str, page: int | None) -> PdfImportResult:
    if len(doc) == 0:
        raise PdfImportError(f"'{source_label}' has no pages.")

    if page is not None:
        if not (0 <= page < len(doc)):
            raise PdfImportError(
                f"'{source_label}' has {len(doc)} page(s); page {page + 1} was requested."
            )
        return _extract_page(doc[page], page, source_label)

    successes: list[PdfImportResult] = []
    failures: list[str] = []
    for i in range(len(doc)):
        try:
            successes.append(_extract_page(doc[i], i, source_label))
        except PdfImportError as exc:
            failures.append(f"page {i + 1}: {exc}")

    if not successes:
        if len(doc) == 1:
            raise PdfImportError(failures[0].split(": ", 1)[1] if failures else "unknown error")
        raise PdfImportError(
            f"No page of '{source_label}' yielded a profile that could be read confidently.\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
    if len(successes) > 1:
        described = ", ".join(
            f"page {r.page_index + 1} ({r.area_mm2:.0f}mm^2, {len(r.holes)} hole(s))"
            for r in successes
        )
        raise PdfImportError(
            f"'{source_label}' has {len(successes)} pages carrying a readable profile view "
            f"({described}). Refusing to guess which one is the extrusion to analyse — "
            "re-import specifying the page."
        )
    return successes[0]


def import_profile(path: str | Path, page: int | None = None) -> PdfImportResult:
    """Extract profile geometry (mm) and cut length from a vector PDF drawing.

    `page` is a 0-based page index; when omitted, every page is tried and
    exactly one must yield a readable profile.
    """
    try:
        header = Path(path).open("rb").read(1024)
    except OSError as exc:
        raise PdfImportError(f"Could not read '{path}': {exc}") from exc
    _require_pdf(header, str(path))
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:  # pymupdf raises a variety of types
        raise PdfImportError(f"Could not open '{path}' as a PDF: {exc}") from exc
    try:
        return _import_from_doc(doc, str(path), page)
    finally:
        doc.close()


def import_profile_from_bytes(
    pdf_bytes: bytes, source_label: str = "<uploaded file>", page: int | None = None
) -> PdfImportResult:
    """Same as `import_profile`, from PDF content already in memory."""
    _require_pdf(pdf_bytes[:1024], source_label)
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfImportError(f"'{source_label}' could not be read as a PDF: {exc}") from exc
    try:
        return _import_from_doc(doc, source_label, page)
    finally:
        doc.close()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eat.pdf_io",
        description="Extract an extrusion profile and its length from a PDF drawing.",
    )
    parser.add_argument("pdf_path")
    parser.add_argument("--page", type=int, default=None, help="1-based page number")
    parser.add_argument("--vertices", action="store_true", help="include full vertex lists")
    args = parser.parse_args(argv)

    result = import_profile(args.pdf_path, page=(args.page - 1) if args.page else None)
    payload = {
        "page": result.page_index + 1,
        "scale": _fmt_scale(result.scale),
        "scale_source": result.scale_source,
        "length_mm": result.length_mm,
        "length_source": result.length_source,
        "area_mm2": round(result.area_mm2, 4),
        "outer_vertex_count": len(result.vertices),
        "holes": [len(h) for h in result.holes],
        "title_block": result.title_block,
        "dimension_checks": [
            {
                "text": c.text,
                "stated": c.stated,
                "measured": round(c.measured, 4),
                "error_pct": round(c.error_pct, 3),
                "agrees": c.agrees,
            }
            for c in result.dimension_checks
        ],
        "views": [
            {
                "role": v.role,
                "loops": v.loop_count,
                "aspect": round(v.aspect, 2),
                "scale": _fmt_scale(v.scale) if v.scale else None,
                "scale_source": v.scale_source,
            }
            for v in result.views
        ],
        "warnings": result.warnings,
        "mass_check": result.mass_check.note if result.mass_check else None,
    }
    if args.vertices:
        payload["vertices"] = result.vertices
        payload["hole_vertices"] = result.holes
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
