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

Also confirms the run-history side effects (build step 9): POST /section,
/section/from-dxf, and /beam each append exactly one history entry (except
when `save_history: false` is passed, e.g. the frontend's solid-fill
comparison), GET /history's summaries and GET /history/{id}'s full entry
round-trip correctly, and DELETE /history/{id} works -- eat.history's own
add/list/get/delete logic is unit-tested in isolation in
eat/verify_history.py; this file only checks the HTTP wiring and the
auto-logging side effect. main() sweeps up every history entry any check
in this file created, restoring eat/history.json -- the real file a
user's actual runs live in -- to its pre-test state, the same discipline
check_materials() already applies to materials.json.

Also confirms the baseline-comparison endpoints (build step 10): GET
/baseline defaults to the built-in profile and includes the Z-moduli and
yield strength the frontend's dual-bar stiffness/strength-to-weight
comparison needs, POST /baseline selects a history entry as the baseline
(validating entry_id and rejecting an unknown one), and deleting the
referenced history entry falls back to the built-in baseline rather than
erroring -- eat.baseline's own setting-persistence/resolution logic (plus
the built-in profile's hand-checkable figures and the six comparison
metrics' formulas) is unit-tested in isolation in eat/verify_baseline.py;
this file only checks the HTTP wiring. main() restores eat/baseline.json's
setting to its pre-test state, same restore-to-original-state discipline
as the other two shared JSON files.

Also confirms the design-suggestions endpoint (build step 11): POST
/suggestions works from inline geometry or a section_id and agrees
between the two, returns a geometry reference the frontend can highlight,
logs nothing to history, and -- the part that actually matters -- leaves
the real KJN extrusion alone while flagging the L-angle's unfilleted
inner corner. Whether each suggestion is sound engineering is judged in
eat/verify_suggestions.py against five profiles with known right answers;
this file only checks the HTTP wiring.

Run with: python -m eat.verify_api
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf import units as ezdxf_units
from fastapi.testclient import TestClient

from eat.api import app
from eat.baseline import DEFAULT_BASELINE_SETTING_PATH, get_baseline_setting, set_baseline_setting

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

    # Holes (build step 8): rectangle with a centered square hole,
    # subtracted natively via sectionproperties -- same exact-match case
    # as eat.verify_section's hole check, exercised through the API.
    hole = [[15, 40], [35, 40], [35, 60], [15, 60]]  # 20x20 centered in 50x100
    resp = client.post(
        "/section",
        json={
            "vertices": rectangle,
            "holes": [hole],
            "material_name": "6061-T6 Aluminum (Extruded)",
            "mesh_size": 0.5,
        },
    )
    checks.append(Check("POST /section (with holes): 200 OK", resp.status_code == 200, resp.text))
    body = resp.json()
    checks.append(
        Check(
            "POST /section (with holes): Area/Ixx/Iyy match analytic subtraction",
            _rel_close(body["area"], 4600.0, 1e-3)
            and _rel_close(body["ixx"], 4153333.333333333, 1e-3)
            and _rel_close(body["iyy"], 1028333.3333333333, 1e-3),
            f"area={body['area']}, ixx={body['ixx']}, iyy={body['iyy']}",
        )
    )
    checks.append(
        Check(
            "POST /section (with holes): holes echoed back in response",
            body["holes"] == [hole],
            f"holes={body.get('holes')}",
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

    # Two disjoint closed loops (neither containing the other): step 7
    # treated this as valid multi-loop input, but step 8 tightens that --
    # every non-outer loop must now be a fully-enclosed hole, so a
    # disjoint "interior" loop is out of scope and rejected with a clear
    # reason (same tightening as eat.verify_dxf's equivalent test).
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
    checks.append(
        Check(
            "POST /section/from-dxf: two disjoint (non-hole) loops -> 400 with clean message",
            resp.status_code == 400 and "not fully contained" in resp.json()["detail"],
            resp.text,
        )
    )

    # A loop that IS a proper hole (fully contained in the outer boundary):
    # valid multi-loop input, subtracted from the analyzed section and
    # echoed back in `holes` (build step 8).
    doc2 = ezdxf.new(dxfversion="R2010")
    doc2.units = ezdxf_units.MM
    msp2 = doc2.modelspace()
    outer = msp2.add_lwpolyline([(0, 0), (50, 0), (50, 30), (0, 30)])
    outer.closed = True
    hole = msp2.add_lwpolyline([(20, 10), (30, 10), (30, 20), (20, 20)])
    hole.closed = True
    buf2 = io.StringIO()
    doc2.write(buf2)
    dxf_bytes2 = buf2.getvalue().encode("utf-8")

    resp = client.post(
        "/section/from-dxf",
        files={"file": ("outer_with_hole.dxf", io.BytesIO(dxf_bytes2), "application/dxf")},
        data={"material_name": "6061-T6 Aluminum (Extruded)", "mesh_size": "0.5"},
    )
    checks.append(Check("POST /section/from-dxf (contained hole): 200 OK", resp.status_code == 200, resp.text))
    if resp.status_code == 200:
        body = resp.json()
        expected_area = 50 * 30 - 10 * 10
        checks.append(
            Check(
                "POST /section/from-dxf (contained hole): area subtracted, holes echoed back",
                len(body["holes"]) == 1
                and _rel_close(body["area"], expected_area, 1e-3)
                and len(body["dxf_loops"]) == 2,
                f"holes={body.get('holes')}, area={body.get('area')}",
            )
        )

    return checks


# --- /materials --------------------------------------------------------------


# --- /section/from-pdf -------------------------------------------------------


def check_section_from_pdf() -> list[Check]:
    """HTTP wiring for PDF drawing import. The extraction itself (and whether
    it reads the real supplier drawings correctly) is verified against those
    drawings in eat/verify_pdf.py; this only checks the endpoint."""
    checks: list[Check] = []
    drawing = Path("B18 - Tower - Extrusion - Standard Light A (1).pdf")
    if not drawing.exists():
        checks.append(
            Check(
                "POST /section/from-pdf: reference drawing present",
                False,
                f"{drawing} not found in the project root",
            )
        )
        return checks

    with open(drawing, "rb") as f:
        resp = client.post(
            "/section/from-pdf",
            files={"file": (drawing.name, f, "application/pdf")},
            data={"material_name": "6063-T6 Aluminum (Extruded)"},
        )
    checks.append(Check("POST /section/from-pdf: 200 OK", resp.status_code == 200, resp.text[:200]))
    if resp.status_code != 200:
        return checks
    body = resp.json()
    info = body.get("pdf_import") or {}

    checks.append(
        Check(
            "POST /section/from-pdf: reports the scale it read and how it was confirmed",
            info.get("scale") == "2:1" and "confirmed by" in (info.get("scale_source") or ""),
            f"{info.get('scale')} / {info.get('scale_source')}",
        )
    )
    checks.append(
        Check(
            "POST /section/from-pdf: extrusion length comes back as 2500mm",
            info.get("length_mm") is not None and abs(info["length_mm"] - 2500.0) < 0.5,
            str(info.get("length_mm")),
        )
    )
    dims = info.get("dimension_checks") or []
    checks.append(
        Check(
            "POST /section/from-pdf: every dimension check agrees",
            len(dims) >= 3 and all(d["agrees"] for d in dims),
            f"{sum(1 for d in dims if d['agrees'])}/{len(dims)}",
        )
    )
    checks.append(
        Check(
            "POST /section/from-pdf: title block is read",
            (info.get("title_block") or {}).get("part_number") == "DEX05120096",
            str((info.get("title_block") or {}).get("part_number")),
        )
    )
    # The section engine must actually have run on the extracted geometry.
    checks.append(
        Check(
            "POST /section/from-pdf: section properties computed from the extracted profile",
            body["area"] > 0 and body["ixx"] > 0 and len(body["holes"]) == 4,
            f"area={body['area']:.2f}, holes={len(body['holes'])}",
        )
    )

    # Failure: a PDF that cannot be read confidently must 400 with the reason.
    multi = Path("RDEX05120940 B18 - Tower - Section 1 Spine - XL_Rev 2.pdf")
    if multi.exists():
        with open(multi, "rb") as f:
            resp = client.post(
                "/section/from-pdf",
                files={"file": (multi.name, f, "application/pdf")},
                data={"material_name": "6063-T6 Aluminum (Extruded)"},
            )
        checks.append(
            Check(
                "POST /section/from-pdf: unreadable drawing -> 400 explaining why, not a guess",
                resp.status_code == 400
                and "closes into loops" in resp.json()["detail"],
                resp.text[:160],
            )
        )

    # Failure: not a PDF at all.
    resp = client.post(
        "/section/from-pdf",
        files={"file": ("garbage.txt", io.BytesIO(b"not a pdf file"), "text/plain")},
        data={"material_name": "6061-T6 Aluminum (Extruded)"},
    )
    checks.append(
        Check(
            "POST /section/from-pdf: non-PDF content -> 400 with clean message",
            resp.status_code == 400 and "is not a PDF file" in resp.json()["detail"],
            resp.text[:160],
        )
    )
    return checks


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


# --- /history ------------------------------------------------------------------


def check_history() -> list[Check]:
    checks: list[Check] = []
    test_steel = {"name": "Test Steel", "E": 200000, "nu": 0.3, "yield_strength": 250}
    rectangle = [[0, 0], [50, 0], [50, 100], [0, 100]]

    before_count = len(client.get("/history").json())

    # --- A fresh section-only entry ---
    resp = client.post("/section", json={"vertices": rectangle, "material": test_steel, "mesh_size": 1.0})
    checks.append(Check("POST /section (for history test): 200 OK", resp.status_code == 200, resp.text))
    section_id = resp.json()["section_id"]

    after_section = client.get("/history").json()
    checks.append(
        Check(
            "POST /section creates exactly one new history entry",
            len(after_section) == before_count + 1,
            f"before={before_count}, after={len(after_section)}",
        )
    )
    section_summary = after_section[0]  # most-recent-first
    checks.append(
        Check(
            "New entry's summary: material/area/has_holes/has_beam correct",
            section_summary["material"] == "Test Steel"
            and _rel_close(section_summary["area"], 5000.0, 1e-6)
            and section_summary["has_holes"] is False
            and section_summary["has_beam"] is False,
            f"{section_summary}",
        )
    )
    section_entry_id = section_summary["id"]

    resp = client.get(f"/history/{section_entry_id}")
    checks.append(Check("GET /history/{id}: 200 OK for the section-only entry", resp.status_code == 200, resp.text))
    body = resp.json()
    checks.append(
        Check(
            "GET /history/{id}: vertices and section_result round-trip exactly",
            body["vertices"] == rectangle and _rel_close(body["section_result"]["area"], 5000.0, 1e-6),
            f"vertices={body.get('vertices')}, area={body.get('section_result', {}).get('area')}",
        )
    )
    checks.append(
        Check(
            "GET /history/{id}: section-only entry has no beam_request/beam_result",
            body["beam_request"] is None and body["beam_result"] is None,
        )
    )

    # --- save_history: false suppresses logging (e.g. the frontend's
    # solid-fill comparison, or its section_id refresh before a beam
    # re-analysis) ---
    count_before_suppressed = len(client.get("/history").json())
    resp = client.post(
        "/section",
        json={"vertices": rectangle, "material": test_steel, "mesh_size": 1.0, "save_history": False},
    )
    checks.append(Check("POST /section (save_history=false): 200 OK", resp.status_code == 200, resp.text))
    count_after_suppressed = len(client.get("/history").json())
    checks.append(
        Check(
            "POST /section with save_history=false creates no history entry",
            count_after_suppressed == count_before_suppressed,
            f"before={count_before_suppressed}, after={count_after_suppressed}",
        )
    )

    # --- A beam analysis against the same section adds its own entry ---
    resp = client.post(
        "/beam",
        json={
            "section_id": section_id,
            "material": test_steel,
            "length": 1000,
            "boundary_condition": "simply_supported",
            "point_loads": [{"position_fraction": 0.5, "magnitude": -1000}],
        },
    )
    checks.append(Check("POST /beam (for history test): 200 OK", resp.status_code == 200, resp.text))

    after_beam = client.get("/history").json()
    checks.append(
        Check(
            "POST /beam creates exactly one new history entry (on top of the section one)",
            len(after_beam) == before_count + 2,
            f"count={len(after_beam)}, expected={before_count + 2}",
        )
    )
    beam_summary = after_beam[0]  # most-recent-first
    beam_entry_id = beam_summary["id"]
    checks.append(Check("Beam-triggered entry's summary: has_beam=True", beam_summary["has_beam"] is True))

    resp = client.get(f"/history/{beam_entry_id}")
    body = resp.json()
    checks.append(
        Check(
            "GET /history/{id}: beam entry's beam_request/beam_result match what was just computed",
            body["beam_request"]["length"] == 1000
            and body["beam_request"]["boundary_condition"] == "simply_supported"
            and _rel_close(abs(body["beam_result"]["max_moment"]), 250000.0, 1e-6),
            f"beam_request={body.get('beam_request')}, max_moment={body.get('beam_result', {}).get('max_moment')}",
        )
    )

    # --- DELETE ---
    resp = client.delete(f"/history/{section_entry_id}")
    checks.append(Check("DELETE /history/{id}: 204", resp.status_code == 204, resp.text))
    remaining_ids = [s["id"] for s in client.get("/history").json()]
    checks.append(
        Check(
            "DELETE /history/{id}: entry actually removed from the list",
            section_entry_id not in remaining_ids,
            f"remaining={remaining_ids}",
        )
    )

    resp = client.delete("/history/does-not-exist")
    checks.append(Check("DELETE /history/{id}: unknown id -> 404", resp.status_code == 404, resp.text))

    resp = client.get("/history/does-not-exist")
    checks.append(Check("GET /history/{id}: unknown id -> 404", resp.status_code == 404, resp.text))

    # Clean up the one entry this function didn't already delete above
    # (the beam one) -- overall history-file hygiene across a full
    # verification run is handled by main()'s before/after sweep, but
    # deleting our own known leftover here keeps this function
    # self-contained too.
    client.delete(f"/history/{beam_entry_id}")

    return checks


# --- /baseline -----------------------------------------------------------------


def check_baseline() -> list[Check]:
    checks: list[Check] = []
    rectangle = [[0, 0], [50, 0], [50, 100], [0, 100]]

    # --- Default: the built-in profile ---
    resp = client.get("/baseline")
    checks.append(Check("GET /baseline: 200 OK", resp.status_code == 200, resp.text))
    body = resp.json()
    checks.append(
        Check(
            "GET /baseline: defaults to the built-in KJN profile",
            body["source"] == "builtin" and _rel_close(body["area"], 287.6550146754686, 1e-6),
            f"{body}",
        )
    )
    checks.append(
        Check(
            "GET /baseline: includes Z-moduli and yield strength for the strength-to-weight metrics",
            _rel_close(body["zxx_plus"], 1199.4901146218467, 1e-6)
            and _rel_close(body["zyy_minus"], 2313.300042821097, 1e-6)
            and _rel_close(body["yield_strength"], 214.0, 1e-6),
            f"{body}",
        )
    )

    # --- Select a history entry as the baseline ---
    resp = client.post(
        "/section", json={"vertices": rectangle, "material_name": "6061-T6 Aluminum (Extruded)", "mesh_size": 1.0}
    )
    entry_id = client.get("/history").json()[0]["id"]

    resp = client.post("/baseline", json={"type": "history", "entry_id": entry_id})
    checks.append(Check("POST /baseline (select history entry): 200 OK", resp.status_code == 200, resp.text))
    body = resp.json()
    checks.append(
        Check(
            "POST /baseline: response reflects the newly-selected entry",
            body["source"] == "history"
            and body["history_entry_id"] == entry_id
            and _rel_close(body["area"], 5000.0, 1e-6)
            and _rel_close(body["yield_strength"], 241.0, 1e-6),
            f"{body}",
        )
    )

    resp = client.get("/baseline")
    checks.append(
        Check(
            "GET /baseline: selection persists across a separate request",
            resp.json()["history_entry_id"] == entry_id,
            resp.text,
        )
    )

    # --- Validation failures ---
    resp = client.post("/baseline", json={"type": "history"})
    checks.append(Check("POST /baseline: type=history without entry_id -> 422", resp.status_code == 422, resp.text))

    resp = client.post("/baseline", json={"type": "history", "entry_id": "does-not-exist"})
    checks.append(Check("POST /baseline: unknown entry_id -> 404", resp.status_code == 404, resp.text))

    # --- Deleting the referenced entry falls back to the built-in ---
    client.delete(f"/history/{entry_id}")
    resp = client.get("/baseline")
    checks.append(
        Check(
            "GET /baseline: falls back to builtin once the referenced entry is deleted",
            resp.json()["source"] == "builtin",
            resp.text,
        )
    )

    resp = client.post("/baseline", json={"type": "builtin"})
    checks.append(Check("POST /baseline: reset to builtin -> 200 OK", resp.status_code == 200, resp.text))

    return checks


# --- /suggestions ---------------------------------------------------------------


def check_suggestions() -> list[Check]:
    checks: list[Check] = []
    l_angle = [[0, 0], [50, 0], [50, 10], [10, 10], [10, 60], [0, 60]]

    history_before = len(client.get("/history").json())

    resp = client.post("/suggestions", json={"section": {"vertices": l_angle}})
    checks.append(Check("POST /suggestions (inline section): 200 OK", resp.status_code == 200, resp.text))
    body = resp.json()
    checks.append(
        Check(
            "POST /suggestions: L-angle returns just its unfilleted inner corner",
            len(body) == 1 and body[0]["kind"] == "sharp_corner" and body[0]["points"] == [[10.0, 10.0]],
            f"{[(s['kind'], s['points']) for s in body]}",
        )
    )
    checks.append(
        Check(
            "POST /suggestions: findings carry title, detail and a geometry reference",
            bool(body[0]["title"]) and bool(body[0]["detail"]) and body[0]["ring"] == "outer"
            and body[0]["vertex_indices"] == [3],
            f"{body[0]}",
        )
    )
    checks.append(
        Check(
            "POST /suggestions creates no history entry (it's advice, not an analysis run)",
            len(client.get("/history").json()) == history_before,
            f"before={history_before}, after={len(client.get('/history').json())}",
        )
    )

    # Same profile by section_id rather than re-sending its geometry.
    section = client.post(
        "/section",
        json={"vertices": l_angle, "material_name": "6061-T6 Aluminum (Extruded)", "mesh_size": 1.0},
    )
    section_id = section.json()["section_id"]
    resp = client.post("/suggestions", json={"section_id": section_id})
    checks.append(Check("POST /suggestions (section_id): 200 OK", resp.status_code == 200, resp.text))
    checks.append(
        Check(
            "POST /suggestions: section_id path gives the same answer as inline geometry",
            resp.json() == body,
            f"{resp.text}",
        )
    )

    # A commercial profile should come back quiet -- the engine's whole value
    # depends on not crying wolf (see eat/verify_suggestions.py).
    with open("eat/fixtures/20X40_KJN992891.dxf", "rb") as f:
        dxf = client.post(
            "/section/from-dxf",
            files={"file": ("kjn.dxf", f, "application/dxf")},
            data={"material_name": "6063-T6 Aluminum (Extruded)"},
        )
    resp = client.post("/suggestions", json={"section_id": dxf.json()["section_id"]})
    checks.append(
        Check(
            "POST /suggestions: the real KJN extrusion comes back with nothing to flag",
            resp.status_code == 200 and resp.json() == [],
            resp.text,
        )
    )

    resp = client.post("/suggestions", json={"section": {"vertices": l_angle}, "section_id": section_id})
    checks.append(Check("POST /suggestions: section + section_id both given -> 422", resp.status_code == 422, resp.text))

    resp = client.post("/suggestions", json={})
    checks.append(Check("POST /suggestions: neither section nor section_id -> 422", resp.status_code == 422, resp.text))

    resp = client.post("/suggestions", json={"section_id": "does-not-exist"})
    checks.append(
        Check(
            "POST /suggestions: unknown section_id -> 404 with clean message",
            resp.status_code == 404 and "does-not-exist" in resp.json()["detail"],
            resp.text,
        )
    )

    resp = client.post("/suggestions", json={"section": {"vertices": [[0, 0], [1, 1]]}})
    checks.append(Check("POST /suggestions: degenerate profile -> 400", resp.status_code == 400, resp.text))

    return checks


def main() -> int:
    # Snapshot history before anything runs: check_section/_from_dxf/_beam
    # all trigger their own history-logging as a side effect of exercising
    # /section, /section/from-dxf, and /beam, same as real usage would.
    # Sweeping up everything new at the end (rather than hand-tracking ids
    # through every function) keeps eat/history.json -- the real file a
    # user's actual runs live in, not a throwaway test fixture -- exactly
    # as it was before this script ran, the same restore-to-original-state
    # discipline check_materials() already applies to materials.json.
    history_baseline_ids = {s["id"] for s in client.get("/history").json()}
    # Same discipline for the baseline *setting* (which profile is
    # selected) -- check_baseline() changes it via POST /baseline; restore
    # whatever it was, bypassing the API (there's no GET for the raw
    # setting, only the resolved info) since this is teardown, not part of
    # what's under test.
    baseline_setting_before = get_baseline_setting(DEFAULT_BASELINE_SETTING_PATH)

    section_checks, rectangle_section_id = check_section()
    all_checks = (
        section_checks
        + check_section_from_dxf()
        + check_section_from_pdf()
        + check_materials()
        + check_beam(rectangle_section_id)
        + check_history()
        + check_baseline()
        + check_suggestions()
    )

    history_after = client.get("/history").json()
    new_ids = [s["id"] for s in history_after if s["id"] not in history_baseline_ids]
    for entry_id in new_ids:
        client.delete(f"/history/{entry_id}")
    history_final_count = len(client.get("/history").json())
    all_checks.append(
        Check(
            "History file restored to its pre-test state",
            history_final_count == len(history_baseline_ids),
            f"baseline={len(history_baseline_ids)}, final={history_final_count}",
        )
    )

    set_baseline_setting(baseline_setting_before, DEFAULT_BASELINE_SETTING_PATH)
    all_checks.append(
        Check(
            "Baseline setting restored to its pre-test state",
            get_baseline_setting(DEFAULT_BASELINE_SETTING_PATH) == baseline_setting_before,
            f"{baseline_setting_before}",
        )
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
