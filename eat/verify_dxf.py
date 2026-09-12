"""
Verification harness for DXF import/export (build step 4).

1. Round-trip test: export the same 50x100mm rectangle used in
   eat/verify_section.py to a scratch DXF, re-import it, confirm the
   vertices match the original to a tight tolerance, then run
   analyze_section on the re-imported vertices and confirm Area/Ixx/Iyy
   match the exact closed-form values from step 1's verification.
2. Prints the extracted vertices for each committed fixture DXF in
   eat/fixtures/ (rectangle, L-angle, C-channel) for an eyeball check.
3. Confirms import fails clearly (raises DxfImportError, doesn't guess)
   for the two most common out-of-scope cases: an open polyline, and a
   file with more than one entity.

Run with: python -m eat.verify_dxf
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import ezdxf

from eat.dxf_io import DxfImportError, export_polygon, import_polygon
from eat.section import Material, analyze_section

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MATERIAL = Material(name="6061-T6", E=68900, nu=0.33, yield_strength=276)


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def check_roundtrip(tmp_dir: Path) -> list[Check]:
    checks: list[Check] = []

    original = [(0.0, 0.0), (50.0, 0.0), (50.0, 100.0), (0.0, 100.0)]
    dxf_path = tmp_dir / "roundtrip_rectangle.dxf"
    export_polygon(original, dxf_path)
    reimported = import_polygon(dxf_path)

    checks.append(Check("Round-trip: vertex count matches", len(reimported) == len(original)))

    coords_match = len(reimported) == len(original) and all(
        abs(rx - ox) < 1e-6 and abs(ry - oy) < 1e-6
        for (rx, ry), (ox, oy) in zip(reimported, original)
    )
    checks.append(
        Check(
            "Round-trip: vertices match original to 1e-6mm",
            coords_match,
            f"reimported={reimported}",
        )
    )

    # Same tolerances/expected values as eat/verify_section.py's rectangle case.
    result = analyze_section(reimported, MATERIAL, mesh_size=1.0)
    b, d = 50.0, 100.0
    expected_area = b * d
    expected_ixx = b * d**3 / 12
    expected_iyy = d * b**3 / 12

    def rel_err(actual, expected):
        return abs(actual - expected) / abs(expected)

    checks.append(
        Check(
            "Round-trip: Area matches step-1 value",
            rel_err(result.area, expected_area) <= 1e-3,
            f"expected={expected_area}, actual={result.area}",
        )
    )
    checks.append(
        Check(
            "Round-trip: Ixx matches step-1 value",
            rel_err(result.ixx, expected_ixx) <= 1e-3,
            f"expected={expected_ixx}, actual={result.ixx}",
        )
    )
    checks.append(
        Check(
            "Round-trip: Iyy matches step-1 value",
            rel_err(result.iyy, expected_iyy) <= 1e-3,
            f"expected={expected_iyy}, actual={result.iyy}",
        )
    )

    return checks


def print_fixture_vertices() -> list[Check]:
    checks: list[Check] = []
    fixtures = sorted(FIXTURES_DIR.glob("*.dxf"))
    checks.append(Check(f"Found fixture DXF files in {FIXTURES_DIR}", len(fixtures) > 0))

    print("\nFixture DXF contents (eyeball check):")
    for path in fixtures:
        try:
            vertices = import_polygon(path)
            print(f"  {path.name}: {vertices}")
            checks.append(Check(f"Import '{path.name}' succeeds", True))
        except DxfImportError as exc:
            print(f"  {path.name}: IMPORT FAILED — {exc}")
            checks.append(Check(f"Import '{path.name}' succeeds", False, str(exc)))
    print()

    return checks


def check_rejects_out_of_scope(tmp_dir: Path) -> list[Check]:
    checks: list[Check] = []

    # Case 1: open polyline (not closed).
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)])  # closed defaults to False
    open_path = tmp_dir / "open_polyline.dxf"
    doc.saveas(open_path)

    try:
        import_polygon(open_path)
        checks.append(Check("Open polyline is rejected", False, "no exception raised"))
    except DxfImportError as exc:
        checks.append(Check("Open polyline is rejected", True, str(exc)))

    # Case 2: two entities in modelspace.
    doc2 = ezdxf.new(dxfversion="R2010")
    doc2.units = ezdxf.units.MM
    msp2 = doc2.modelspace()
    p1 = msp2.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)])
    p1.closed = True
    p2 = msp2.add_lwpolyline([(20, 0), (30, 0), (30, 10), (20, 10)])
    p2.closed = True
    multi_path = tmp_dir / "two_entities.dxf"
    doc2.saveas(multi_path)

    try:
        import_polygon(multi_path)
        checks.append(Check("Multiple entities are rejected", False, "no exception raised"))
    except DxfImportError as exc:
        checks.append(Check("Multiple entities are rejected", True, str(exc)))

    return checks


def main() -> int:
    # dir=... keeps the scratch directory inside the project tree (the
    # OS default temp dir lives under /var, which is out of bounds here).
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
        tmp_dir = Path(tmp)
        all_checks = (
            check_roundtrip(tmp_dir) + print_fixture_vertices() + check_rejects_out_of_scope(tmp_dir)
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
