"""
EAT DXF import/export — thin wrapper around `ezdxf`.

Import extracts a single closed, straight-edged polygon profile (a list of
(x, y) vertices, mm — see UNITS.md) from a DXF file, in the exact format
`eat.section.analyze_section` expects. Export writes that same vertex
format back out as a closed LWPOLYLINE DXF.

v1 scope, per the spec: a single closed/open polygon profile, no
multi-cell/composite sections. Concretely, import requires the DXF's
modelspace to contain exactly one entity: a closed LWPOLYLINE or
old-style POLYLINE with only straight segments (no arc bulges). Anything
else — multiple entities, open profiles, arcs, unsupported entity types,
non-millimeter units — raises `DxfImportError` describing exactly what
was found, rather than guessing at how to interpret it.

Usable as a library:

    from eat.dxf_io import import_polygon, export_polygon
    vertices = import_polygon("profile.dxf")
    export_polygon(vertices, "profile_out.dxf")

Or from the command line:

    python -m eat.dxf_io import profile.dxf
    python -m eat.dxf_io export path/to/vertices.json profile_out.dxf
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import ezdxf
from ezdxf import units as ezdxf_units

_BULGE_TOL = 1e-9


class DxfImportError(Exception):
    """Raised when a DXF file's profile is outside v1 import scope."""


def _describe_entity(entity) -> str:
    dxftype = entity.dxftype()
    if dxftype == "LWPOLYLINE":
        return f"LWPOLYLINE ({'closed' if entity.closed else 'open'}, {len(entity)} vertices)"
    if dxftype == "POLYLINE":
        return f"POLYLINE ({'closed' if entity.is_closed else 'open'})"
    return dxftype


def _check_units(doc) -> None:
    insunits = doc.header.get("$INSUNITS", 0)
    # 0 = unitless (no explicit unit set); treated as "trust the caller,
    # values are mm" since many hand-authored/simple DXFs leave this unset.
    if insunits not in (0, ezdxf_units.MM):
        unit_name = ezdxf_units.unit_name(insunits)
        raise DxfImportError(
            f"DXF file units are '{unit_name}' (code {insunits}), not millimeters. "
            "EAT works in mm throughout (see UNITS.md) — re-export the file in mm."
        )


def _vertices_from_lwpolyline(entity) -> list[tuple[float, float]]:
    if not entity.closed:
        raise DxfImportError(
            "Found a single LWPOLYLINE, but it is not closed. "
            "Open profiles are unsupported in v1 — close the polyline before importing."
        )
    points = list(entity.get_points())
    bulges = [p[4] for p in points]
    if any(abs(b) > _BULGE_TOL for b in bulges):
        raise DxfImportError(
            "The LWPOLYLINE contains arc segments (non-zero bulge). "
            "Only straight-edged polygons are supported in v1 — replace arcs with "
            "line segments before importing."
        )
    return [(float(p[0]), float(p[1])) for p in points]


def _vertices_from_polyline(entity) -> list[tuple[float, float]]:
    if not entity.is_closed:
        raise DxfImportError(
            "Found a single POLYLINE, but it is not closed. "
            "Open profiles are unsupported in v1 — close the polyline before importing."
        )
    vertices = []
    for v in entity.vertices:
        bulge = getattr(v.dxf, "bulge", 0.0)
        if abs(bulge) > _BULGE_TOL:
            raise DxfImportError(
                "The POLYLINE contains arc segments (non-zero bulge). "
                "Only straight-edged polygons are supported in v1 — replace arcs with "
                "line segments before importing."
            )
        loc = v.dxf.location
        vertices.append((float(loc.x), float(loc.y)))
    return vertices


def _import_polygon_from_doc(doc, source_label: str) -> list[tuple[float, float]]:
    _check_units(doc)

    msp = doc.modelspace()
    entities = list(msp)

    if len(entities) != 1:
        if len(entities) == 0:
            raise DxfImportError(
                f"'{source_label}' has no entities in modelspace — nothing to import."
            )
        descriptions = ", ".join(_describe_entity(e) for e in entities)
        raise DxfImportError(
            f"Expected exactly one closed polygon entity, found {len(entities)} entities in "
            f"'{source_label}': {descriptions}. v1 supports a single closed/open profile only — "
            "remove the extra geometry or split it into separate files."
        )

    entity = entities[0]
    dxftype = entity.dxftype()
    if dxftype == "LWPOLYLINE":
        vertices = _vertices_from_lwpolyline(entity)
    elif dxftype == "POLYLINE":
        vertices = _vertices_from_polyline(entity)
    else:
        raise DxfImportError(
            f"Expected a closed LWPOLYLINE or POLYLINE, found a {dxftype} entity in "
            f"'{source_label}'. Only simple closed polylines are supported in v1."
        )

    if len(vertices) < 3:
        raise DxfImportError(
            f"The closed polyline in '{source_label}' has only {len(vertices)} vertices; a "
            "polygon needs at least 3."
        )
    return vertices


def import_polygon(path: str | Path) -> list[tuple[float, float]]:
    """Extract a single closed polygon's vertices (mm) from a DXF file.

    Raises `DxfImportError` with a specific reason if the file doesn't
    parse as DXF, or doesn't contain exactly one closed, straight-edged
    LWPOLYLINE/POLYLINE.
    """
    try:
        doc = ezdxf.readfile(str(path))
    except ezdxf.DXFError as exc:
        raise DxfImportError(f"'{path}' is not a valid DXF file: {exc}") from exc
    return _import_polygon_from_doc(doc, source_label=str(path))


def import_polygon_from_text(
    dxf_text: str, source_label: str = "<uploaded file>"
) -> list[tuple[float, float]]:
    """Same as `import_polygon`, but from DXF file content already in memory
    (e.g. an uploaded file's bytes, decoded), avoiding a temp-file round trip."""
    try:
        doc = ezdxf.read(io.StringIO(dxf_text))
    except ezdxf.DXFError as exc:
        raise DxfImportError(f"'{source_label}' is not a valid DXF file: {exc}") from exc
    return _import_polygon_from_doc(doc, source_label=source_label)


def export_polygon(
    vertices: list[tuple[float, float]], path: str | Path, layer: str = "EAT_PROFILE"
) -> None:
    """Write a closed polygon (mm) to a DXF file as a closed LWPOLYLINE."""
    if len(vertices) < 3:
        raise ValueError("A polygon needs at least 3 vertices")

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf_units.MM
    doc.layers.add(layer)

    msp = doc.modelspace()
    polyline = msp.add_lwpolyline(vertices, dxfattribs={"layer": layer})
    polyline.closed = True

    doc.saveas(str(path))


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eat.dxf_io", description="Import/export a polygon profile as DXF."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="Import a DXF and print its vertices as JSON.")
    p_import.add_argument("dxf_path")

    p_export = sub.add_parser("export", help="Export vertices (from a JSON list) to a DXF file.")
    p_export.add_argument("vertices_json", help="Path to a JSON file containing [[x, y], ...]")
    p_export.add_argument("dxf_path")

    args = parser.parse_args(argv)

    if args.command == "import":
        vertices = import_polygon(args.dxf_path)
        print(json.dumps(vertices, indent=2))
    elif args.command == "export":
        vertices = [tuple(p) for p in json.loads(Path(args.vertices_json).read_text())]
        export_polygon(vertices, args.dxf_path)
        print(f"Wrote {len(vertices)} vertices to '{args.dxf_path}'.")

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
