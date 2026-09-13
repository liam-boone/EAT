"""
EAT DXF import/export — thin wrapper around `ezdxf`.

Import extracts closed-loop polygon geometry (lists of (x, y) vertices, mm
— see UNITS.md) from a DXF file. Export writes a flat vertex list back out
as a closed LWPOLYLINE DXF.

Real-world catalog DXFs (e.g. extrusion vendor exports) are messier than a
single hand-drawn closed polyline, so import is built in two layers:

1. **Recovery, not strict parsing.** Files load via `ezdxf.recover`
   instead of the strict reader. Vendor exports often carry corrupted or
   unsupported non-geometric records (this project's own test case has
   49 `ACAD_PROXY_OBJECT` entries wrongly nested in a scale-list
   dictionary, with embedded binary group-310 data that trips the strict
   reader's "Invalid binary data" check) alongside perfectly good
   geometry. `recover` repairs/discards what it can and reports exactly
   what it changed via `DxfImportResult.warnings` — surfaced to the
   caller, never silently swallowed, so a salvaged file is visibly
   flagged as salvaged rather than looking indistinguishable from a
   clean one.

2. **Loop assembly from any mix of edge geometry.** A profile doesn't
   have to arrive as one closed LWPOLYLINE. Import reads LINE, ARC,
   LWPOLYLINE, POLYLINE (both straight and arc/bulge segments), and
   CIRCLE entities as raw edges, discretizes arcs into straight chords
   (see `DEFAULT_CHORD_TOLERANCE` below), and chains them into closed
   loops by matching shared endpoints — regardless of whether any single
   source entity was itself flagged "closed". Every closed loop found is
   reported (`DxfImportResult.loops`); the one with the largest enclosed
   area is treated as the outer profile boundary and returned as
   `vertices`. Every other loop is classified against that boundary: a
   loop fully contained within it is an interior hole (e.g. an
   extrusion's screw-boss bores or T-slot channel), collected into
   `DxfImportResult.holes` so `eat.section.analyze_section` can subtract
   it. A loop that isn't fully contained (disjoint from the outer
   boundary, or overlapping it without full containment) is out of v1's
   scope and fails import with a specific description naming which loop
   and why, rather than guessing how to interpret it. If the edges don't
   reduce to clean closed loops at all (a dangling end, a branching
   junction), import fails the same way.

Anything still out of scope (SPLINE/ELLIPSE geometry, non-millimeter
units, a file with no closed-loop geometry at all) raises
`DxfImportError` describing exactly what was found.

Usable as a library:

    from eat.dxf_io import import_polygon, export_polygon
    result = import_polygon("profile.dxf")
    print(result.vertices, result.warnings, result.loops)
    export_polygon(result.vertices, "profile_out.dxf")

Or from the command line:

    python -m eat.dxf_io import profile.dxf
    python -m eat.dxf_io export path/to/vertices.json profile_out.dxf
"""

from __future__ import annotations

import argparse
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf import recover
from ezdxf import units as ezdxf_units
from ezdxf.math import bulge_from_arc_angle, bulge_to_arc
from shapely.geometry import Polygon as _ShapelyPolygon

# Chord tolerance for discretizing arcs/bulges into straight segments, mm.
# 0.02mm (20 microns) is roughly an order of magnitude finer than typical
# extrusion dimensional tolerances (~0.1-0.13mm is common for catalog
# aluminum profiles), so the polygon-approximation error this introduces
# is negligible next to real manufacturing variation, while still keeping
# vertex counts reasonable for ordinary fillet radii (a handful of
# segments per fillet rather than hundreds).
DEFAULT_CHORD_TOLERANCE = 0.02

# Decimal places used to snap edge endpoints together when chaining loops
# (~1e-6 mm) -- fine enough that real shared vertices always match, coarse
# enough to absorb DXF text round-trip noise.
_ENDPOINT_SNAP_DECIMALS = 6

# Entity types that never contribute profile geometry -- skipped, not
# treated as an error, when found in modelspace.
_ANNOTATION_TYPES = {
    "TEXT", "MTEXT", "DIMENSION", "INSERT", "ATTDEF", "ATTRIB",
    "HATCH", "POINT", "VIEWPORT", "LEADER", "MLEADER", "SOLID",
}


class DxfImportError(Exception):
    """Raised when a DXF file's profile is outside v1 import scope."""


@dataclass
class _Edge:
    p0: tuple[float, float]
    p1: tuple[float, float]
    bulge: float = 0.0  # DXF bulge convention: arc from p0 to p1, 0 = straight


@dataclass
class LoopInfo:
    """One closed loop found in the file (not necessarily the one used)."""

    vertex_count: int
    area: float  # mm^2, after arc discretization
    bbox: tuple[float, float, float, float]  # (minx, miny, maxx, maxy), mm


@dataclass
class DxfImportResult:
    vertices: list[tuple[float, float]]  # the selected (largest-area) loop
    holes: list[list[tuple[float, float]]]  # interior loops fully contained in `vertices`
    warnings: list[str]  # ezdxf.recover audit messages, if the file needed repair
    loops: list[LoopInfo]  # every closed loop found, outer and interior alike
    outer_loop_index: int  # index into `loops` that became `vertices`
    hole_loop_indices: list[int]  # indices into `loops` that became `holes`, same order


def _check_units(doc) -> None:
    insunits = doc.header.get("$INSUNITS", 0)
    # 0 = unitless (no explicit unit set); treated as "trust the caller,
    # values are mm" since many hand-authored/vendor DXFs leave this unset.
    if insunits not in (0, ezdxf_units.MM):
        unit_name = ezdxf_units.unit_name(insunits)
        raise DxfImportError(
            f"DXF file units are '{unit_name}' (code {insunits}), not millimeters. "
            "EAT works in mm throughout (see UNITS.md) — re-export the file in mm."
        )


# --- Edge extraction: every supported entity type becomes 1+ _Edge -----------


def _edges_from_line(entity) -> list[_Edge]:
    p0 = (entity.dxf.start.x, entity.dxf.start.y)
    p1 = (entity.dxf.end.x, entity.dxf.end.y)
    return [_Edge(p0, p1, 0.0)]


def _edges_from_arc(entity) -> list[_Edge]:
    center = entity.dxf.center
    radius = entity.dxf.radius
    start_angle = math.radians(entity.dxf.start_angle)
    end_angle = math.radians(entity.dxf.end_angle)
    # DXF ARC entities always sweep counter-clockwise from start to end.
    sweep = (end_angle - start_angle) % (2 * math.pi)
    if sweep <= 1e-12:
        sweep = 2 * math.pi
    bulge = bulge_from_arc_angle(sweep)
    p0 = (center.x + radius * math.cos(start_angle), center.y + radius * math.sin(start_angle))
    p1 = (center.x + radius * math.cos(end_angle), center.y + radius * math.sin(end_angle))
    return [_Edge(p0, p1, bulge)]


def _edges_from_lwpolyline(entity) -> list[_Edge]:
    points = list(entity.get_points())  # (x, y, start_width, end_width, bulge)
    n = len(points)
    count = n if entity.closed else n - 1
    edges = []
    for i in range(count):
        j = (i + 1) % n
        p0 = (points[i][0], points[i][1])
        p1 = (points[j][0], points[j][1])
        edges.append(_Edge(p0, p1, points[i][4]))
    return edges


def _edges_from_polyline(entity) -> list[_Edge]:
    verts = list(entity.vertices)
    n = len(verts)
    count = n if entity.is_closed else n - 1
    edges = []
    for i in range(count):
        j = (i + 1) % n
        loc0 = verts[i].dxf.location
        loc1 = verts[j].dxf.location
        bulge = getattr(verts[i].dxf, "bulge", 0.0)
        edges.append(_Edge((loc0.x, loc0.y), (loc1.x, loc1.y), bulge))
    return edges


def _loop_points_from_circle(entity, chord_tolerance: float) -> list[tuple[float, float]]:
    # A single bulge value can only describe up to a 180-degree arc, so a
    # full circle needs two bulge=1 (semicircle) edges.
    center = entity.dxf.center
    radius = entity.dxf.radius
    p_right = (center.x + radius, center.y)
    p_left = (center.x - radius, center.y)
    points = [p_right]
    points += _discretize_bulge(p_right, p_left, 1.0, chord_tolerance)
    points += _discretize_bulge(p_left, p_right, 1.0, chord_tolerance)
    return points[:-1]  # drop the duplicate closing point


def _discretize_bulge(
    p0: tuple[float, float], p1: tuple[float, float], bulge: float, chord_tolerance: float
) -> list[tuple[float, float]]:
    """Points along the arc from p0 to p1 (excluding p0, including p1),
    approximating it with straight chords no farther than `chord_tolerance`
    from the true arc."""
    if bulge == 0:
        return [p1]

    center, _, _, radius = bulge_to_arc(p0, p1, bulge)
    theta = 4.0 * math.atan(abs(bulge))  # total included angle, radians
    if radius <= 1e-9 or theta <= 1e-12:
        return [p1]

    angle0 = math.atan2(p0[1] - center.y, p0[0] - center.x)
    direction = 1.0 if bulge > 0 else -1.0

    if chord_tolerance >= radius:
        n = 1
    else:
        ratio = min(1.0, max(-1.0, 1.0 - chord_tolerance / radius))
        max_step = 2.0 * math.acos(ratio)
        n = max(1, math.ceil(theta / max_step))

    points = []
    for k in range(1, n + 1):
        a = angle0 + direction * theta * k / n
        points.append((center.x + radius * math.cos(a), center.y + radius * math.sin(a)))
    return points


def _expand_loop(loop: list[_Edge], chord_tolerance: float) -> list[tuple[float, float]]:
    points = [loop[0].p0]
    for edge in loop:
        points.extend(_discretize_bulge(edge.p0, edge.p1, edge.bulge, chord_tolerance))
    if len(points) > 1 and math.dist(points[0], points[-1]) < 1e-6:
        points.pop()  # closing point duplicates the start
    return points


def _shoelace_area_and_bbox(points: list[tuple[float, float]]):
    n = len(points)
    s = 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0, (min(xs), min(ys), max(xs), max(ys))


def _snap(pt: tuple[float, float]) -> tuple[float, float]:
    return (round(pt[0], _ENDPOINT_SNAP_DECIMALS), round(pt[1], _ENDPOINT_SNAP_DECIMALS))


def _chain_edges_into_loops(edges: list[_Edge], source_label: str) -> list[list[_Edge]]:
    """Group edges into closed loops by matching shared endpoints.

    Requires every endpoint to be shared by exactly two edge-ends (a
    simple set of closed loops, no dangling ends or branching
    junctions) -- if it isn't, raises DxfImportError describing the
    specific points that don't pair up, rather than guessing.
    """
    adjacency: dict[tuple[float, float], list[tuple[int, str]]] = {}
    for i, e in enumerate(edges):
        adjacency.setdefault(_snap(e.p0), []).append((i, "p0"))
        adjacency.setdefault(_snap(e.p1), []).append((i, "p1"))

    bad_points = [(pt, len(v)) for pt, v in adjacency.items() if len(v) != 2]
    if bad_points:
        bad_points.sort(key=lambda item: -item[1])
        examples = ", ".join(
            f"({x:.4f}, {y:.4f}) has {n} edge-ends" for (x, y), n in bad_points[:5]
        )
        raise DxfImportError(
            f"'{source_label}' geometry doesn't reduce to simple closed loops: "
            f"{len(bad_points)} point(s) where edges don't pair up cleanly (expected "
            f"exactly 2 edge-ends per point) — e.g. {examples}. This usually means an "
            "open/dangling edge or a branching junction, which v1 can't resolve into a "
            "polygon automatically."
        )

    visited = [False] * len(edges)
    loops: list[list[_Edge]] = []
    for i in range(len(edges)):
        if visited[i]:
            continue
        loop = [edges[i]]
        visited[i] = True
        start_point = _snap(edges[i].p0)
        cur_point = _snap(edges[i].p1)
        while cur_point != start_point:
            idx, end = next(
                (idx, end) for idx, end in adjacency[cur_point] if not visited[idx]
            )
            e = edges[idx]
            oriented = e if end == "p0" else _Edge(e.p1, e.p0, -e.bulge)
            loop.append(oriented)
            visited[idx] = True
            cur_point = _snap(oriented.p1)
        loops.append(loop)
    return loops


def _import_from_doc(doc, auditor, source_label: str, chord_tolerance: float) -> DxfImportResult:
    _check_units(doc)
    warnings = [fix.message for fix in auditor.fixes] if auditor.fixes else []

    edges: list[_Edge] = []
    loops_points: list[list[tuple[float, float]]] = []
    unsupported: set[str] = set()

    for entity in doc.modelspace():
        dxftype = entity.dxftype()
        if dxftype == "LINE":
            edges.extend(_edges_from_line(entity))
        elif dxftype == "ARC":
            edges.extend(_edges_from_arc(entity))
        elif dxftype == "LWPOLYLINE":
            edges.extend(_edges_from_lwpolyline(entity))
        elif dxftype == "POLYLINE":
            edges.extend(_edges_from_polyline(entity))
        elif dxftype == "CIRCLE":
            loops_points.append(_loop_points_from_circle(entity, chord_tolerance))
        elif dxftype in _ANNOTATION_TYPES:
            continue
        else:
            unsupported.add(dxftype)

    if unsupported:
        raise DxfImportError(
            f"'{source_label}' contains unsupported entity type(s) for profile geometry: "
            f"{', '.join(sorted(unsupported))}. v1 reads profile geometry from LINE, ARC, "
            "LWPOLYLINE, POLYLINE, and CIRCLE entities only."
        )

    if edges:
        for loop in _chain_edges_into_loops(edges, source_label):
            loops_points.append(_expand_loop(loop, chord_tolerance))

    if not loops_points:
        raise DxfImportError(f"'{source_label}' has no closed-loop geometry to import.")

    loop_infos = []
    areas = []
    for pts in loops_points:
        area, bbox = _shoelace_area_and_bbox(pts)
        loop_infos.append(LoopInfo(vertex_count=len(pts), area=area, bbox=bbox))
        areas.append(area)

    outer_index = max(range(len(areas)), key=lambda i: areas[i])
    vertices = loops_points[outer_index]

    if len(vertices) < 3:
        raise DxfImportError(
            f"The largest closed loop in '{source_label}' has only {len(vertices)} "
            "vertices; a polygon needs at least 3."
        )

    holes: list[list[tuple[float, float]]] = []
    hole_indices: list[int] = []
    if len(loops_points) > 1:
        outer_poly = _ShapelyPolygon(vertices)
        for i, pts in enumerate(loops_points):
            if i == outer_index:
                continue
            candidate = _ShapelyPolygon(pts)
            # `covers` (not the stricter `contains`) so a hole boundary that
            # happens to touch the outer boundary still counts as enclosed --
            # only genuine escapes outside the outer profile are rejected.
            if outer_poly.covers(candidate):
                holes.append(pts)
                hole_indices.append(i)
            else:
                raise DxfImportError(
                    f"Loop {i} in '{source_label}' ({loop_infos[i].vertex_count} vertices, "
                    f"area={loop_infos[i].area:.4g} mm^2, bbox={loop_infos[i].bbox}) is not "
                    f"fully contained within the outer boundary (loop {outer_index}, "
                    f"area={loop_infos[outer_index].area:.4g} mm^2) -- it's either disjoint "
                    "from it or only partially overlapping. v1 only supports interior loops "
                    "that are fully-enclosed holes; this file needs manual review."
                )

    return DxfImportResult(
        vertices=vertices,
        holes=holes,
        warnings=warnings,
        loops=loop_infos,
        outer_loop_index=outer_index,
        hole_loop_indices=hole_indices,
    )


def import_polygon(
    path: str | Path, chord_tolerance: float = DEFAULT_CHORD_TOLERANCE
) -> DxfImportResult:
    """Extract profile geometry (mm) from a DXF file.

    Loads via `ezdxf.recover` (not the strict reader) so files with
    corrupted-but-irrelevant records still import, with any repairs
    reported in the result's `warnings`. Raises `DxfImportError` with a
    specific reason if the file can't be recovered at all, or its
    geometry doesn't reduce to at least one simple closed loop.
    """
    try:
        doc, auditor = recover.readfile(str(path))
    except OSError as exc:
        raise DxfImportError(f"Could not read '{path}': {exc}") from exc
    except ezdxf.DXFError as exc:
        raise DxfImportError(f"'{path}' could not be recovered as DXF: {exc}") from exc
    return _import_from_doc(doc, auditor, str(path), chord_tolerance)


def import_polygon_from_bytes(
    dxf_bytes: bytes,
    source_label: str = "<uploaded file>",
    chord_tolerance: float = DEFAULT_CHORD_TOLERANCE,
) -> DxfImportResult:
    """Same as `import_polygon`, but from DXF file content already in
    memory (e.g. an uploaded file's raw bytes), avoiding a temp-file
    round trip. `ezdxf.recover` detects text encoding itself."""
    try:
        doc, auditor = recover.read(io.BytesIO(dxf_bytes))
    except ezdxf.DXFError as exc:
        raise DxfImportError(f"'{source_label}' could not be recovered as DXF: {exc}") from exc
    return _import_from_doc(doc, auditor, source_label, chord_tolerance)


def export_polygon_to_text(vertices: list[tuple[float, float]], layer: str = "EAT_PROFILE") -> str:
    """Build DXF file content (as text) for a closed polygon, without
    touching disk -- used for the API's download endpoint."""
    if len(vertices) < 3:
        raise ValueError("A polygon needs at least 3 vertices")
    # ezdxf will happily write "nan"/"inf" into the coordinate group codes,
    # producing a file that no CAD tool (including this one's own importer)
    # can read back. Fail here instead, where the reason is still visible.
    for i, (x, y) in enumerate(vertices):
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(
                f"Vertex {i} is not a finite coordinate ({x}, {y}); it cannot be written to DXF."
            )

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf_units.MM
    doc.layers.add(layer)

    msp = doc.modelspace()
    polyline = msp.add_lwpolyline(vertices, dxfattribs={"layer": layer})
    polyline.closed = True

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue()


def export_polygon(
    vertices: list[tuple[float, float]], path: str | Path, layer: str = "EAT_PROFILE"
) -> None:
    """Write a closed polygon (mm) to a DXF file as a closed LWPOLYLINE."""
    text = export_polygon_to_text(vertices, layer=layer)
    Path(path).write_text(text)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eat.dxf_io", description="Import/export a polygon profile as DXF."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser(
        "import", help="Import a DXF and print its vertices, warnings, and loop diagnostics."
    )
    p_import.add_argument("dxf_path")

    p_export = sub.add_parser("export", help="Export vertices (from a JSON list) to a DXF file.")
    p_export.add_argument("vertices_json", help="Path to a JSON file containing [[x, y], ...]")
    p_export.add_argument("dxf_path")

    args = parser.parse_args(argv)

    if args.command == "import":
        result = import_polygon(args.dxf_path)
        print(
            json.dumps(
                {
                    "vertices": result.vertices,
                    "holes": result.holes,
                    "warnings": result.warnings,
                    "outer_loop_index": result.outer_loop_index,
                    "hole_loop_indices": result.hole_loop_indices,
                    "loops": [
                        {"vertex_count": l.vertex_count, "area": l.area, "bbox": l.bbox}
                        for l in result.loops
                    ],
                },
                indent=2,
            )
        )
    elif args.command == "export":
        vertices = [tuple(p) for p in json.loads(Path(args.vertices_json).read_text())]
        export_polygon(vertices, args.dxf_path)
        print(f"Wrote {len(vertices)} vertices to '{args.dxf_path}'.")

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
