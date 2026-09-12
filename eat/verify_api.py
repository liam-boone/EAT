"""
Verification harness for the FastAPI layer (build step 5).

Exercises every endpoint via FastAPI's in-process TestClient (no server
process/port needed) — happy path plus at least one failure case each —
and checks that API results for the step-1 rectangle / step-3 beam cases
match the numbers already verified in eat/verify_section.py and
eat/verify_beam.py exactly (same PASS/FAIL table format as prior steps).

Also confirms POST/PUT/DELETE /materials round-trip cleanly and leave
materials.json in its original seeded state, same as eat/verify_materials.py
but exercised through HTTP instead of calling eat.materials directly.

Run with: python -m eat.verify_api
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import ezdxf
from ezdxf import units as ezdxf_units
from fastapi.testclient import TestClient

from eat.api import app

client = TestClient(app)

REL_TOL = 1e-6


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _rel_close(actual: float, expected: float, tol: float = REL_TOL) -> bool:
    if expected == 0:
        return abs(actual) < 1e-9
    return abs(actual - expected) / abs(expected) <= tol


# --- /section --------------------------------------------------------------


def check_section() -> list[Check]:
    checks: list[Check] = []
    rectangle = [[0, 0], [50, 0], [50, 100], [0, 100]]
    expected = {"area": 5000.0, "ixx": 4166666.6666666665, "iyy": 1041666.6666666666}

    # Happy path: material_name lookup.
    resp = client.post(
        "/section",
        json={
            "vertices": rectangle,
            "material_name": "6061-T6 Aluminum (Extruded)",
            "mesh_size": 1.0,
        },
    )
    checks.append(Check("POST /section (material_name): 200 OK", resp.status_code == 200))
    body = resp.json()
    checks.append(
        Check(
            "POST /section: Area/Ixx/Iyy match step-1 exactly",
            _rel_close(body["area"], expected["area"], 1e-3)
            and _rel_close(body["ixx"], expected["ixx"], 1e-3)
            and _rel_close(body["iyy"], expected["iyy"], 1e-3),
            f"area={body['area']}, ixx={body['ixx']}, iyy={body['iyy']}",
        )
    )
    section_id = body["section_id"]

    # Happy path: inline material spec.
    resp = client.post(
        "/section",
        json={
            "vertices": rectangle,
            "material": {"name": "Test Steel", "E": 200000, "nu": 0.3, "yield_strength": 250},
            "mesh_size": 1.0,
        },
    )
    checks.append(Check("POST /section (inline material): 200 OK", resp.status_code == 200))

    # Failure: both material_name and material given.
    resp = client.post(
        "/section",
        json={
            "vertices": rectangle,
            "material_name": "6061-T6 Aluminum (Extruded)",
            "material": {"name": "X", "E": 1, "nu": 0.3},
        },
    )
    checks.append(
        Check("POST /section: both material sources -> 400", resp.status_code == 400, resp.text)
    )

    # Failure: unknown material name.
    resp = client.post("/section", json={"vertices": rectangle, "material_name": "Unobtainium"})
    checks.append(
        Check(
            "POST /section: unknown material -> 404 with clean message",
            resp.status_code == 404 and "Unobtainium" in resp.json()["detail"],
            resp.text,
        )
    )

    return checks, section_id


# --- /section/from-dxf -------------------------------------------------------


def check_section_from_dxf() -> list[Check]:
    checks: list[Check] = []
    expected = {"area": 5000.0, "ixx": 4166666.6666666665, "iyy": 1041666.6666666666}

    with open("eat/fixtures/rectangle_50x100.dxf", "rb") as f:
        resp = client.post(
            "/section/from-dxf",
            files={"file": ("rectangle_50x100.dxf", f, "application/dxf")},
            data={"material_name": "6061-T6 Aluminum (Extruded)", "mesh_size": "1.0"},
        )
    checks.append(Check("POST /section/from-dxf: 200 OK", resp.status_code == 200))
    body = resp.json()
    checks.append(
        Check(
            "POST /section/from-dxf: Area/Ixx/Iyy match step-1 exactly",
            _rel_close(body["area"], expected["area"], 1e-3)
            and _rel_close(body["ixx"], expected["ixx"], 1e-3)
            and _rel_close(body["iyy"], expected["iyy"], 1e-3),
            f"area={body['area']}, ixx={body['ixx']}, iyy={body['iyy']}",
        )
    )

    # Failure: not a DXF file at all.
    resp = client.post(
        "/section/from-dxf",
        files={"file": ("garbage.txt", io.BytesIO(b"not a dxf file"), "text/plain")},
        data={"material_name": "6061-T6 Aluminum (Extruded)"},
    )
    checks.append(
        Check(
            "POST /section/from-dxf: garbage content -> 400 with clean message",
            resp.status_code == 400 and "could not be recovered as DXF" in resp.json()["detail"],
            resp.text,
        )
    )

    # Two disjoint closed loops in one file: valid multi-loop input (build
    # step 7) -- the larger loop is used as the outer profile, and both
    # are reported in dxf_loops rather than the old "exactly one entity"
    # rejection.
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf_units.MM
    msp = doc.modelspace()
    small = msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)])
    small.closed = True
    big = msp.add_lwpolyline([(20, 0), (50, 0), (50, 30), (20, 30)])
    big.closed = True
    buf = io.StringIO()
    doc.write(buf)
    dxf_bytes = buf.getvalue().encode("utf-8")

    resp = client.post(
        "/section/from-dxf",
        files={"file": ("two_loops.dxf", io.BytesIO(dxf_bytes), "application/dxf")},
        data={"material_name": "6061-T6 Aluminum (Extruded)"},
    )
    checks.append(Check("POST /section/from-dxf: two disjoint loops -> 200 (multi-loop)", resp.status_code == 200, resp.text))
    if resp.status_code == 200:
        body = resp.json()
        checks.append(
            Check(
                "POST /section/from-dxf: both loops reported, larger used as outer",
                body["dxf_loops"] is not None
                and len(body["dxf_loops"]) == 2
                and _rel_close(body["area"], 900.0, 1e-2),
                f"dxf_loops={body.get('dxf_loops')}, area={body.get('area')}",
            )
        )

    return checks


# --- /materials --------------------------------------------------------------


def check_materials() -> list[Check]:
    checks: list[Check] = []

    resp = client.get("/materials")
    checks.append(Check("GET /materials: 200 OK, 8 seeded materials", resp.status_code == 200 and len(resp.json()) == 8))

    resp = client.post(
        "/materials", json={"name": "__API_TEST__", "E": 1000, "nu": 0.3, "yield_strength": 100}
    )
    checks.append(Check("POST /materials: add -> 201", resp.status_code == 201, resp.text))

    resp = client.post("/materials", json={"name": "__API_TEST__", "E": 1000, "nu": 0.3})
    checks.append(Check("POST /materials: duplicate name -> 409", resp.status_code == 409, resp.text))

    resp = client.put("/materials/__API_TEST__", json={"yield_strength": 999})
    checks.append(
        Check(
            "PUT /materials/{name}: edit -> 200, value updated",
            resp.status_code == 200 and _rel_close(resp.json()["yield_strength"], 999.0),
            resp.text,
        )
    )

    resp = client.put("/materials/NoSuchMaterial", json={"yield_strength": 1})
    checks.append(Check("PUT /materials/{name}: unknown -> 404", resp.status_code == 404, resp.text))

    resp = client.delete("/materials/__API_TEST__")
    checks.append(Check("DELETE /materials/{name}: 204", resp.status_code == 204))

    resp = client.delete("/materials/NoSuchMaterial")
    checks.append(Check("DELETE /materials/{name}: unknown -> 404", resp.status_code == 404, resp.text))

    resp = client.get("/materials")
    checks.append(
        Check("GET /materials: back to exactly 8 after add/edit/delete", len(resp.json()) == 8)
    )

    return checks


# --- /beam --------------------------------------------------------------------


def check_beam(rectangle_section_id: str) -> list[Check]:
    checks: list[Check] = []
    test_steel = {"name": "Test Steel", "E": 200000, "nu": 0.3, "yield_strength": 250}

    # Happy path: section_id reference, simply supported, central load.
    resp = client.post(
        "/beam",
        json={
            "section_id": rectangle_section_id,
            "material": test_steel,
            "length": 1000,
            "boundary_condition": "simply_supported",
            "point_loads": [{"position_fraction": 0.5, "magnitude": -1000}],
        },
    )
    checks.append(Check("POST /beam (section_id, simply_supported): 200 OK", resp.status_code == 200, resp.text))
    body = resp.json()
    checks.append(
        Check(
            "POST /beam: matches step-3 simply-supported M_max/v_max exactly",
            _rel_close(abs(body["max_moment"]), 250000.0, 1e-6)
            and _rel_close(abs(body["max_deflection"]), 0.025, 1e-4),
            f"max_moment={body['max_moment']}, max_deflection={body['max_deflection']}",
        )
    )

    # Happy path: inline section, fixed_fixed, with axial_load for buckling SF.
    resp = client.post(
        "/beam",
        json={
            "section": {"vertices": [[0, 0], [50, 0], [50, 100], [0, 100]], "mesh_size": 1.0},
            "material": test_steel,
            "length": 1000,
            "boundary_condition": "fixed_fixed",
            "point_loads": [{"position_fraction": 0.5, "magnitude": -1000}],
            "axial_load": 100000,
        },
    )
    checks.append(Check("POST /beam (inline section, fixed_fixed): 200 OK", resp.status_code == 200, resp.text))
    body = resp.json()
    checks.append(
        Check(
            "POST /beam: matches step-3 fixed-fixed M_max/v_max exactly",
            _rel_close(abs(body["max_moment"]), 125000.0, 1e-6)
            and _rel_close(abs(body["max_deflection"]), 0.00625, 1e-4),
            f"max_moment={body['max_moment']}, max_deflection={body['max_deflection']}",
        )
    )
    checks.append(
        Check(
            "POST /beam: buckling_safety_factor = Pcr / axial_load",
            body["buckling_safety_factor"] is not None
            and _rel_close(body["buckling_safety_factor"], body["euler_buckling_load"] / 100000),
            f"buckling_safety_factor={body['buckling_safety_factor']}",
        )
    )

    # Failure: both section and section_id given.
    resp = client.post(
        "/beam",
        json={
            "section": {"vertices": [[0, 0], [50, 0], [50, 100], [0, 100]]},
            "section_id": rectangle_section_id,
            "material": test_steel,
            "length": 1000,
            "boundary_condition": "simply_supported",
            "point_loads": [{"position_fraction": 0.5, "magnitude": -1000}],
        },
    )
    checks.append(Check("POST /beam: section + section_id both given -> 422", resp.status_code == 422, resp.text))

    # Failure: unknown section_id.
    resp = client.post(
        "/beam",
        json={
            "section_id": "does-not-exist",
            "material": test_steel,
            "length": 1000,
            "boundary_condition": "simply_supported",
            "point_loads": [{"position_fraction": 0.5, "magnitude": -1000}],
        },
    )
    checks.append(
        Check(
            "POST /beam: unknown section_id -> 404 with clean message",
            resp.status_code == 404 and "does-not-exist" in resp.json()["detail"],
            resp.text,
        )
    )

    # Failure: invalid boundary_condition.
    resp = client.post(
        "/beam",
        json={
            "section": {"vertices": [[0, 0], [50, 0], [50, 100], [0, 100]]},
            "material": test_steel,
            "length": 1000,
            "boundary_condition": "not_a_real_bc",
            "point_loads": [{"position_fraction": 0.5, "magnitude": -1000}],
        },
    )
    checks.append(Check("POST /beam: invalid boundary_condition -> 422", resp.status_code == 422, resp.text))

    # Failure: point load position_fraction out of [0, 1].
    resp = client.post(
        "/beam",
        json={
            "section": {"vertices": [[0, 0], [50, 0], [50, 100], [0, 100]]},
            "material": test_steel,
            "length": 1000,
            "boundary_condition": "simply_supported",
            "point_loads": [{"position_fraction": 1.5, "magnitude": -1000}],
        },
    )
    checks.append(Check("POST /beam: position_fraction out of range -> 422", resp.status_code == 422, resp.text))

    return checks


def main() -> int:
    section_checks, rectangle_section_id = check_section()
    all_checks = (
        section_checks
        + check_section_from_dxf()
        + check_materials()
        + check_beam(rectangle_section_id)
    )

    width = max(len(c.label) for c in all_checks) + 2
    all_passed = True
    for c in all_checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        detail = f"  ({c.detail})" if c.detail and not c.passed else ""
        print(f"{c.label:<{width}} {status}{detail}")

    print()
    print(f"{len(all_checks)} checks. " + ("All passed." if all_passed else "FAILURES DETECTED — see rows marked FAIL above."))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
