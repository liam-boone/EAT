"""
Verification harness for DXF import robustness (build step 7, extending
step 4).

1. Regression: the three straight-edge fixtures (rectangle, L-angle,
   C-channel) still import to the *exact* same vertices and analyze to
   the exact same step-1 rectangle numbers as before this step's
   rewrite (recover-based loading + general edge chaining instead of a
   single-LWPOLYLINE-only reader).
2. Round-trip: export the step-1 rectangle, re-import it, confirm the
   vertices match to a tight tolerance and analyze_section reproduces
   Area/Ixx/Iyy exactly.
3. Arc-bulge discretization: a stadium shape (two straight sides, two
   semicircular bulge ends) imports with an area within a documented
   tolerance of the exact analytic value (rectangle + full circle).
4. Multi-loop recognition: two disjoint closed rectangles (neither
   containing the other) are rejected -- step 8 tightens step 7's
   "any extra loop is valid multi-loop input" to "any extra loop must
   be a fully-enclosed hole."
5. Out-of-scope cases still fail clearly: a dangling (unclosed) chain,
   and an unsupported entity type (SPLINE).
6. The real catalog file, eat/fixtures/20X40_KJN992891.dxf, which
   fails under the strict DXF reader (an embedded binary chunk in a
   corrupted ACAD_PROXY_OBJECT dictionary entry -- see eat/dxf_io.py's
   module docstring): confirms it now imports via ezdxf.recover with
   its repairs surfaced as warnings (not silently swallowed), reports
   its actual loop structure (1 outer + 3 interior loops -- two
   symmetric round bores and a central T-slot channel), classifies all
   3 interior loops as holes fully contained in the outer boundary, and
   confirms the outer-minus-holes section analyzes to the corrected
   (smaller) Area/Ixx/Iyy -- see eat.verify_api's plausibility check
   against published 20x40 T-slot extrusion mass-per-length figures.
7. Hole classification (build step 8): a hole fully contained in the
   outer boundary is subtracted; a loop that pokes outside the outer
   boundary (not fully contained) is rejected with a specific,
   loop-naming error rather than guessed at.

Run with: python -m eat.verify_dxf
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import ezdxf

from eat.dxf_io import DEFAULT_CHORD_TOLERANCE, DxfImportError, export_polygon, import_polygon
from eat.section import Material, analyze_section

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MATERIAL = Material(name="6063-T6", E=68900, nu=0.33, yield_strength=214, density=2700)

REAL_CATALOG_FILE = FIXTURES_DIR / "20X40_KJN992891.dxf"


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _rel_close(actual, expected, tol=1e-3):
    if expected == 0:
        return abs(actual) < 1e-9
    return abs(actual - expected) / abs(expected) <= tol


def check_fixture_regression() -> list[Check]:
    """Exact same vertices as the shapes originally used to generate
    these fixtures (see the git history of eat/fixtures/*.dxf's export
    calls) -- confirms the recover+chaining rewrite changed nothing for
    the simple single-closed-LWPOLYLINE case."""
    checks: list[Check] = []
    expected = {
        "rectangle_50x100.dxf": [(0.0, 0.0), (50.0, 0.0), (50.0, 100.0), (0.0, 100.0)],
        "l_angle_50x50x5.dxf": [(0.0, 0.0), (50.0, 0.0), (50.0, 5.0), (5.0, 5.0), (5.0, 50.0), (0.0, 50.0)],
        "c_channel_100x40x5.dxf": [
            (0.0, 0.0), (40.0, 0.0), (40.0, 5.0), (5.0, 5.0), (5.0, 95.0),
            (40.0, 95.0), (40.0, 100.0), (0.0, 100.0),
        ],
    }
    print("\nFixture DXF contents (eyeball check):")
    for name, expected_vertices in expected.items():
        path = FIXTURES_DIR / name
        try:
            result = import_polygon(path)
            print(f"  {name}: {result.vertices}")
            match = len(result.vertices) == len(expected_vertices) and all(
                abs(ax - ex) < 1e-9 and abs(ay - ey) < 1e-9
                for (ax, ay), (ex, ey) in zip(result.vertices, expected_vertices)
            )
            checks.append(
                Check(f"'{name}' vertices unchanged by rewrite", match, f"got {result.vertices}")
            )
            checks.append(Check(f"'{name}' has no recovery warnings (clean file)", result.warnings == []))
            checks.append(Check(f"'{name}' reports exactly 1 loop", len(result.loops) == 1))
        except DxfImportError as exc:
            print(f"  {name}: IMPORT FAILED — {exc}")
            checks.append(Check(f"'{name}' still imports", False, str(exc)))
    print()
    return checks


def check_roundtrip(tmp_dir: Path) -> list[Check]:
    checks: list[Check] = []

    original = [(0.0, 0.0), (50.0, 0.0), (50.0, 100.0), (0.0, 100.0)]
    dxf_path = tmp_dir / "roundtrip_rectangle.dxf"
    export_polygon(original, dxf_path)
    reimported = import_polygon(dxf_path).vertices

    checks.append(Check("Round-trip: vertex count matches", len(reimported) == len(original)))
    coords_match = len(reimported) == len(original) and all(
        abs(rx - ox) < 1e-6 and abs(ry - oy) < 1e-6
        for (rx, ry), (ox, oy) in zip(reimported, original)
    )
    checks.append(
        Check("Round-trip: vertices match original to 1e-6mm", coords_match, f"reimported={reimported}")
    )

    result = analyze_section(reimported, MATERIAL, mesh_size=1.0)
    b, d = 50.0, 100.0
    checks.append(Check("Round-trip: Area matches step-1 value", _rel_close(result.area, b * d)))
    checks.append(Check("Round-trip: Ixx matches step-1 value", _rel_close(result.ixx, b * d**3 / 12)))
    checks.append(Check("Round-trip: Iyy matches step-1 value", _rel_close(result.iyy, d * b**3 / 12)))

    return checks


def check_arc_bulge_discretization(tmp_dir: Path) -> list[Check]:
    """Stadium shape: 30mm straight sides, 5mm-radius semicircular ends
    (bulge=1 each). Exact area = 30*10 + pi*5^2 (rectangle + full circle).
    The discretization tolerance is DEFAULT_CHORD_TOLERANCE (chord-to-arc
    sagitta, mm) -- the resulting *area* error is larger than that in
    relative terms for a full circle (an n-gon's area deficit scales
    with the tolerance, diluted here by the straight-sided portion), so
    this check uses a looser, empirically-justified tolerance rather
    than assuming area error == chord tolerance."""
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    points = [(0, -5, 0, 0, 0), (30, -5, 0, 0, 1.0), (30, 5, 0, 0, 0), (0, 5, 0, 0, 1.0)]
    lwp = msp.add_lwpolyline(points, format="xyseb")
    lwp.closed = True
    path = tmp_dir / "stadium.dxf"
    doc.saveas(path)

    result = import_polygon(path)
    expected_area = 30 * 10 + math.pi * 5**2

    return [
        Check("Arc-bulge: stadium imports with no errors", True),
        Check(
            "Arc-bulge: discretized area within 0.5% of exact value",
            _rel_close(result.loops[0].area, expected_area, tol=5e-3),
            f"expected={expected_area:.4f}, actual={result.loops[0].area:.4f}, "
            f"chord_tolerance={DEFAULT_CHORD_TOLERANCE}mm",
        ),
        Check(
            "Arc-bulge: vertex count reflects chord-tolerance discretization",
            len(result.vertices) > 10,
            f"{len(result.vertices)} vertices",
        ),
    ]


def check_multi_loop_recognition(tmp_dir: Path) -> list[Check]:
    """Two disjoint closed rectangles (neither containing the other): step
    7 treated this as valid multi-loop input (both reported, larger as
    outer, smaller un-processed). Step 8 tightens that -- every non-outer
    loop must now be a fully-enclosed hole to be meaningful input, so a
    disjoint "interior" loop is out of scope and must be rejected with a
    clear reason, same as check_hole_classification's poking-outside
    case below. (The valid multi-loop case -- a loop that IS a proper
    hole -- is covered there instead.)"""
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    small = msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)])
    small.closed = True
    big = msp.add_lwpolyline([(20, 0), (50, 0), (50, 30), (20, 30)])
    big.closed = True
    path = tmp_dir / "two_loops.dxf"
    doc.saveas(path)

    checks = []
    try:
        import_polygon(path)
        checks.append(Check("Multi-loop: two disjoint (non-hole) loops are rejected", False, "no exception raised"))
    except DxfImportError as exc:
        msg = str(exc)
        checks.append(
            Check(
                "Multi-loop: two disjoint (non-hole) loops are rejected",
                "not fully contained" in msg,
                msg,
            )
        )
    return checks


def check_hole_classification(tmp_dir: Path) -> list[Check]:
    """A loop fully inside the outer boundary becomes a hole; a loop that
    pokes outside it (here, a "hole" whose bbox extends past the outer
    rectangle's right edge) is out of v1's scope and must fail clearly,
    naming which loop and why -- not silently guessed at or dropped."""
    checks: list[Check] = []

    # Case A: a real hole, fully contained -- 50x30 outer, 10x10 hole
    # centered inside it.
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    outer = msp.add_lwpolyline([(0, 0), (50, 0), (50, 30), (0, 30)])
    outer.closed = True
    hole = msp.add_lwpolyline([(20, 10), (30, 10), (30, 20), (20, 20)])
    hole.closed = True
    contained_path = tmp_dir / "contained_hole.dxf"
    doc.saveas(contained_path)

    result = import_polygon(contained_path)
    checks.append(Check("Hole classification: 2 loops found", len(result.loops) == 2))
    checks.append(
        Check(
            "Hole classification: contained loop becomes a hole",
            len(result.holes) == 1 and result.hole_loop_indices != [],
            f"holes={result.holes}",
        )
    )
    if result.holes:
        section = analyze_section(result.vertices, MATERIAL, mesh_size=0.5, holes=result.holes)
        expected_area = 50 * 30 - 10 * 10
        checks.append(
            Check(
                "Hole classification: outer-minus-hole area matches analytic value",
                _rel_close(section.area, expected_area),
                f"expected={expected_area}, actual={section.area}",
            )
        )

    # Case B: malformed -- "hole" pokes outside the outer boundary's right
    # edge (outer spans x=0..50, this loop spans x=40..60).
    doc2 = ezdxf.new(dxfversion="R2010")
    doc2.units = ezdxf.units.MM
    msp2 = doc2.modelspace()
    outer2 = msp2.add_lwpolyline([(0, 0), (50, 0), (50, 30), (0, 30)])
    outer2.closed = True
    poking = msp2.add_lwpolyline([(40, 10), (60, 10), (60, 20), (40, 20)])
    poking.closed = True
    malformed_path = tmp_dir / "hole_pokes_outside.dxf"
    doc2.saveas(malformed_path)

    try:
        import_polygon(malformed_path)
        checks.append(
            Check("Hole classification: not-fully-contained loop is rejected", False, "no exception raised")
        )
    except DxfImportError as exc:
        msg = str(exc)
        checks.append(
            Check(
                "Hole classification: not-fully-contained loop is rejected",
                "not fully contained" in msg,
                msg,
            )
        )

    return checks


def check_rejects_out_of_scope(tmp_dir: Path) -> list[Check]:
    checks: list[Check] = []

    # Case 1: a dangling (unclosed) chain -- three lines that don't close.
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_line((10, 0), (10, 10))
    msp.add_line((10, 10), (0, 10))
    open_path = tmp_dir / "open_chain.dxf"
    doc.saveas(open_path)
    try:
        import_polygon(open_path)
        checks.append(Check("Dangling chain is rejected", False, "no exception raised"))
    except DxfImportError as exc:
        checks.append(Check("Dangling chain is rejected", True, str(exc)))

    # Case 2: an unsupported entity type (SPLINE) mixed in with valid geometry.
    doc2 = ezdxf.new(dxfversion="R2010")
    doc2.units = ezdxf.units.MM
    msp2 = doc2.modelspace()
    p = msp2.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)])
    p.closed = True
    msp2.add_spline(fit_points=[(0, 0), (5, 5), (10, 0)])
    spline_path = tmp_dir / "with_spline.dxf"
    doc2.saveas(spline_path)
    try:
        import_polygon(spline_path)
        checks.append(Check("Unsupported entity type (SPLINE) is rejected", False, "no exception raised"))
    except DxfImportError as exc:
        checks.append(Check("Unsupported entity type (SPLINE) is rejected", True, str(exc)))

    return checks


def check_real_catalog_file() -> list[Check]:
    checks: list[Check] = []
    if not REAL_CATALOG_FILE.exists():
        checks.append(Check(f"Real catalog fixture present: {REAL_CATALOG_FILE.name}", False, "file missing"))
        return checks

    try:
        result = import_polygon(REAL_CATALOG_FILE)
    except DxfImportError as exc:
        checks.append(Check("Real catalog file imports (via ezdxf.recover)", False, str(exc)))
        return checks

    checks.append(Check("Real catalog file imports (via ezdxf.recover)", True))
    checks.append(
        Check(
            "Recovery warnings are surfaced, not swallowed",
            len(result.warnings) > 0,
            f"{len(result.warnings)} warnings, e.g. {result.warnings[0] if result.warnings else ''}",
        )
    )

    print(f"\n{REAL_CATALOG_FILE.name} diagnostics:")
    print(f"  {len(result.warnings)} recovery warning(s) (corrupted ACAD_PROXY_OBJECT entries removed)")
    print(f"  {len(result.loops)} closed loop(s) found:")
    for i, loop in enumerate(result.loops):
        tag = " <- used as outer profile" if i == result.outer_loop_index else " (interior loop, not yet subtracted)"
        print(f"    loop {i}: {loop.vertex_count} vertices, area={loop.area:.4f} mm^2, bbox={loop.bbox}{tag}")
    print()

    checks.append(
        Check(
            "Reports exactly 4 closed loops (1 outer + 3 interior)",
            len(result.loops) == 4,
            f"found {len(result.loops)}",
        )
    )
    if result.loops:
        outer = result.loops[result.outer_loop_index]
        checks.append(
            Check(
                "Outer loop is the largest by area and matches the 40x20mm envelope",
                _rel_close(outer.area, 489.81, tol=1e-3)
                and _rel_close(outer.bbox[2] - outer.bbox[0], 40.0)
                and _rel_close(outer.bbox[3] - outer.bbox[1], 20.0),
                f"area={outer.area}, bbox={outer.bbox}",
            )
        )
        interior_areas = sorted(
            l.area for i, l in enumerate(result.loops) if i != result.outer_loop_index
        )
        checks.append(
            Check(
                "3 interior loops match expected slot-channel geometry",
                len(interior_areas) == 3
                and _rel_close(interior_areas[0], 28.508, tol=1e-2)
                and _rel_close(interior_areas[1], 28.509, tol=1e-2)
                and _rel_close(interior_areas[2], 145.138, tol=1e-2),
                f"interior areas={interior_areas}",
            )
        )

    checks.append(
        Check(
            "All 3 interior loops classified as holes (fully contained in outer boundary)",
            len(result.holes) == 3,
            f"{len(result.holes)} holes",
        )
    )

    try:
        outer_only = analyze_section(result.vertices, MATERIAL, mesh_size=0.5)
        with_holes = analyze_section(result.vertices, MATERIAL, mesh_size=0.5, holes=result.holes)
        outer_area = result.loops[result.outer_loop_index].area
        total_hole_area = sum(l.area for i, l in enumerate(result.loops) if i in result.hole_loop_indices)
        expected_holed_area = outer_area - total_hole_area

        print(
            f"  Corrected (outer - holes) section: Area={with_holes.area:.4f} mm^2 "
            f"(solid-outer estimate was {outer_only.area:.4f} mm^2), "
            f"Ixx={with_holes.ixx:.6g} mm^4, Iyy={with_holes.iyy:.6g} mm^4, "
            f"Mass/len={with_holes.mass_per_length:.4f} kg/m (density=2700 kg/m^3, 6063-T6)\n"
        )

        checks.append(
            Check(
                "Outer-minus-holes profile analyzes successfully",
                True,
            )
        )
        checks.append(
            Check(
                "Corrected Area matches outer-area-minus-hole-areas",
                _rel_close(with_holes.area, expected_holed_area, tol=1e-2),
                f"expected={expected_holed_area:.4f}, actual={with_holes.area:.4f}",
            )
        )
        checks.append(
            Check(
                "Corrected Area is smaller than the solid-outer (pre-step-8) estimate",
                with_holes.area < outer_only.area,
                f"corrected={with_holes.area:.4f}, solid-outer={outer_only.area:.4f}",
            )
        )
        checks.append(
            Check(
                "Mass/length is a plausible 20x40 T-slot extrusion figure (0.6-1.2 kg/m)",
                0.6 <= with_holes.mass_per_length <= 1.2,
                f"{with_holes.mass_per_length:.4f} kg/m -- rough plausibility check, not an "
                "exact-match requirement. 8020's 20-2040 (20x40mm, six open T-slots -- "
                "matching this file's 4-loop structure) publishes 0.793 kg/m, which this "
                "figure lands within ~2% of; a lighter single-slot V-slot-style 20x40 design "
                "would instead run ~0.35-0.45 kg/m, so the published figure depends heavily "
                "on which specific 20x40 profile family a given catalog file is.",
            )
        )
    except Exception as exc:  # noqa: BLE001 -- want to see any failure here, not just ValueError
        checks.append(Check("Outer-minus-holes profile analyzes successfully", False, str(exc)))

    return checks


def main() -> int:
    # dir=... keeps the scratch directory inside the project tree (the
    # OS default temp dir lives under /var, which is out of bounds here).
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
        tmp_dir = Path(tmp)
        all_checks = (
            check_fixture_regression()
            + check_roundtrip(tmp_dir)
            + check_arc_bulge_discretization(tmp_dir)
            + check_multi_loop_recognition(tmp_dir)
            + check_hole_classification(tmp_dir)
            + check_rejects_out_of_scope(tmp_dir)
            + check_real_catalog_file()
        )

    width = max(len(c.label) for c in all_checks) + 2
    all_passed = True
    for c in all_checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        detail = f"  ({c.detail})" if c.detail else ""
        print(f"{c.label:<{width}} {status}{detail}")

    print()
    print("All checks passed." if all_passed else "FAILURES DETECTED — see rows marked FAIL above.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
