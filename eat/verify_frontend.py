"""
Verification harness for the frontend (build step 6).

Drives the actual page with a headless browser (Playwright) rather than
re-testing the math (already verified in steps 1-5): this checks that the
frontend calls the right endpoints with the right payloads and correctly
renders what comes back, for these flows:

1. Sketch a 50x100mm rectangle by clicking its four corners -> close loop
   -> confirm the resulting POST /section response has the exact step-1
   Area/Ixx/Iyy, and that the results panel actually renders -- with the
   main view trimmed to Area + Mass Per Length and everything else
   (Centroid/Ixx/Iyy/Izz included) moved under "More Info" rather than
   dropped. Checked as exact label lists both ways, not as counts.
2. Import eat/fixtures/20X40_KJN992891.dxf (the real multi-loop catalog
   file) -> confirm POST /section/from-dxf returns the step-8
   holes-subtracted Area, the 3 interior holes are echoed back, and the
   canvas actually renders them distinctly (not just that the API says
   so) via a pixel-level scan for the hole-stroke color.
3. Fill in length + the default point load, select 6061-T6 Aluminum, run
   Analyze beam -> confirm POST /beam's max_moment matches the material-
   independent closed-form value (|P|*L/4) exactly, max_deflection matches
   |P|*L^3/(48*E*Ixx) for 6061's actual E, and both charts rendered an SVG
   path. This runs against the KJN profile still loaded from flow 2, which
   -- conveniently for testing -- has two walls (curved, unrestrained bore
   ribs) that eat.local_buckling genuinely cannot classify, so this flow
   also confirms the Local (Plate) Buckling panel renders "Not classified"
   plus the caveat explaining why for those, real k/class/SF numbers for
   the rest, and one table row per API segment.
4. Open the History panel -> confirm the three runs above appear,
   most-recent-first, with the top row tagged as the beam analysis ->
   click it to reload -> confirm no new POST /section or /beam fired (the
   reload is a display of the *stored* result, not a recompute) and that
   the reloaded GET /history/{id} response's beam_result matches flow 3's
   live /beam response exactly -> delete a row and confirm it disappears
   from the list. eat.history's own add/list/get/delete logic is
   unit-tested in isolation in eat/verify_history.py; this only checks
   the UI wiring. Every history entry any flow here creates (all four) is
   swept up at the end, restoring eat/history.json -- the real file a
   user's actual runs live in -- to its pre-test state.
5. Import eat/fixtures/rectangle_50x100.dxf (exact vertices, unlike flow
   1's mouse clicks -- needed for a tight match) with 6061-T6 -> confirm
   the "Compared to Baseline" section's seven dual-bar values (Mass Per
   Length, Stiffness-to-Weight and Strength-to-Weight each for Axial/
   Bending X/Bending Y) against the default built-in KJN baseline match
   hand-computed figures (both sides' numbers independently known: the
   rectangle's from the textbook Ixx/Iyy/Z formulas, the KJN baseline's
   from eat/verify_baseline.py's own established figures) -> set that
   same rectangle entry as the baseline from the History panel -> confirm
   every profile/baseline bar pair reads equal (comparing it against
   itself) -> kill and relaunch the server process (a real "simulated
   restart") -> confirm GET /baseline still reports that entry, and that
   a fresh analysis against the restarted server still compares
   correctly. eat.baseline's own setting-persistence/resolution logic and
   the six metrics' formulas (plus the algebraic sanity check that the
   axial ones reduce to pure material properties) are unit-tested in
   isolation in eat/verify_baseline.py; this only checks the UI wiring
   end-to-end. The baseline selection is restored to its pre-test state
   afterward, same as the history sweep above. Also confirms every metric
   reads in simplest SI form (MN·m/kg, N·m³/kg, kN·m/kg, N·m²/kg -- no
   nested "/(kg/m)" fraction anywhere), that the label was rescaled in step
   with the number, and that exactly the two axial rows -- the two that
   algebraically reduce to E/density and yield/density -- carry the note
   saying so, with the bending rows and mass per length deliberately not
   carrying it. Finally, that the baseline's three entry points (topbar
   readout, the comparison panel's "Change…", the History modal) all name
   the same baseline and both buttons reach the same picker.
6. With that same rectangle still loaded, check the Design Review panel:
   that it renders *below* the profile canvas (measured geometrically),
   that the solid bar's one finding -- a wasted-core finding, which is a
   Structural one -- lands in the Structural column and not the DFM one,
   that the empty DFM column states it passed rather than rendering blank,
   that Structural points at Beam Results for the load-dependent buckling
   checks, and that hovering the finding paints it onto the sketch canvas
   in the Structural colour specifically (counted as --structural pixels,
   with --warning pixels confirmed absent, so a miscategorised highlight
   fails) while clicking pins it so it survives the mouse leaving. Whether
   the suggestions themselves are sound is judged in
   eat/verify_suggestions.py, against profiles with known right answers.

7. Local (plate) buckling, reference case: a 60x40mm tube with a 1.2mm
   wall (3mm fillets), built with ezdxf and imported fresh -- 6063-T6
   Aluminum, 2m simply supported, an 800N mid-span point load, a 4kN axial
   load. This is the case documented in eat/local_buckling.py and this
   session's backend verification: global Euler buckling passes
   comfortably (SF ~2.74) while one wall's local plate buckling fails
   (SF ~0.77), which is the entire reason this check exists -- the global
   number alone would call the design safe. Confirms every field of every
   segment the API returns against an independent recomputation from the
   same vertices (eat.beam + eat.local_buckling, called directly, not
   through HTTP), then confirms every number actually on screen against
   that same API response (parsed back out of the table, allowing only
   for display rounding) -- not "a table rendered," but "this table cell,
   for this wall, holds this number." Also confirms the governing wall's
   row is visually marked, and, importing the plain rectangle_50x100.dxf
   fixture afterward, that the panel disappears entirely for a profile
   with no wall structure to report (a solid bar) rather than rendering
   an empty table, while the Euler result above it still renders fully --
   the two are meant to read as separate results, never as one merged
   into the other. With the tube's results on screen (the one case here
   carrying both a point load and an axial load), also confirms Beam
   Results splits into exactly two groups and that nothing crosses between
   them: moment/stress/deflection/reactions and both diagrams in the
   applied-load group, the Euler tiles and the per-wall plate table in the
   buckling group -- checked by DOM containment, not adjacency.

8. Layout, at 1920x1080 / 1536x864 / 1440x900 / 1024x768: no horizontal
   overflow at any of them (the per-wall plate table scrolls inside its own
   wrapper rather than pushing the page sideways), the design-review panel
   rendering directly below the canvas at every width, the profile canvas
   fully above the fold on every 16:9 / 16:10 size, and the two review
   categories side by side on desktop but stacked at tablet width. Column
   balance is printed in the check's detail rather than asserted -- it
   depends on how many findings the loaded profile has, so a threshold
   would be a fixture check in disguise.

Also checks two smaller additions to the sketch canvas / results panel:

- Axis indicator: a fixed corner gizmo on the sketch canvas showing which
  screen direction is +X / +Y. Verified via a confined pixel scan (mirrors
  app.js's `drawAxisIndicator` geometry, same technique as `canvas_point()`
  below) confirming the arm line + label color render to the *right* of
  the gizmo's origin for X and *above* it for Y -- not just that something
  renders, but that the two axes aren't swapped, since that convention
  must match eat.beam's load-axis selector (Y bends about Ixx / screen-up,
  X bends about Iyy / screen-right) exactly.
- Solid-fill comparison ("More Info"): for the holes-bearing KJN fixture,
  confirms the frontend's extra POST /section (outer boundary, no holes)
  matches an independent `eat.section.analyze_section` call on the same
  outer vertices (not re-verifying the section engine itself, already
  done in verify_section.py / verify_dxf.py -- just confirming the
  frontend calls the right endpoint with the right payload), and that its
  Area lands on step 7's already-established ~489.81 mm^2 outer-loop
  figure. Also confirms the comparison is skipped (hidden) entirely for
  the hole-free rectangle from flow 1.

Also captures two full-page screenshots -- a 16:9 desktop one at the path
given on the command line (or eat/verify_frontend_screenshot.png by
default) and a tablet-width one alongside it with a `_tablet` suffix --
and checks the browser console for errors.

Run with: python -m eat.verify_frontend [screenshot_path]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from playwright.sync_api import sync_playwright

from eat.beam import BoundaryCondition, PointLoad, analyze_beam
from eat.history import DEFAULT_HISTORY_PATH
from eat.local_buckling import analyze_local_buckling
from eat.materials import get_material
from eat.section import analyze_section
from eat.verify_local_buckling import rounded_rect

PORT = 8799
# Columns in the Local (Plate) Buckling table. Named because several
# positional assertions below index into it, and it gained the
# yield-capped "Effective SF" column alongside the elastic one.
LB_COLUMNS = 11
BASE_URL = f"http://127.0.0.1:{PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

MARGIN_PX = 28
VIEW_WIDTH_MM = 300


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _title_case_label(text: str) -> str:
    """Python port of app.js's titleCaseLabel -- must stay in lockstep with
    it, since this is what a test computes as the DOM's expected text."""
    return " ".join(
        w[0].upper() + w[1:] if re.fullmatch(r"[A-Za-z]+", w) else w for w in text.split(" ")
    )


def wait_for_server(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE_URL, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def start_server():
    return subprocess.Popen(
        [str(PROJECT_ROOT / ".venv" / "bin" / "uvicorn"), "eat.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def restart_server(old_server):
    """A real process kill + fresh launch -- not just re-reading a file --
    for a genuine "simulated restart" of the baseline-persistence check."""
    old_server.terminate()
    try:
        old_server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        old_server.kill()
    new_server = start_server()
    if not wait_for_server():
        raise RuntimeError("Server did not come back up after simulated restart")
    return new_server


def canvas_point(bbox, css_width, wx, wy):
    """Mirror app.js's worldToScreen(), returning absolute page coordinates."""
    css_height = round(css_width * 0.75)
    pixels_per_mm = (css_width - 2 * MARGIN_PX) / VIEW_WIDTH_MM
    local_x = MARGIN_PX + wx * pixels_per_mm
    local_y = css_height - MARGIN_PX - wy * pixels_per_mm
    return bbox["x"] + local_x, bbox["y"] + local_y


def _rel_close(actual, expected, tol=1e-3):
    if expected == 0:
        return abs(actual) < 1e-9
    return abs(actual - expected) / abs(expected) <= tol


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def _post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _delete(url):
    urllib.request.urlopen(urllib.request.Request(url, method="DELETE"), timeout=5)


def main() -> int:
    screenshot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "eat" / "verify_frontend_screenshot.png"
    checks: list[Check] = []
    console_errors: list[str] = []
    captured: dict[str, dict] = {}

    server = start_server()
    history_file_before: bytes | None = None
    history_count_before = 0
    baseline_setting_before: dict | None = None
    state_snapshotted = False  # gates the restore in the finally below
    try:
        if not wait_for_server():
            checks.append(Check("Server started", False, "timed out waiting for port"))
            return _report(checks)
        checks.append(Check("Server started", True))

        # Every flow below hits POST /section, /section/from-dxf, and/or
        # /beam, each of which logs to the real eat/history.json (the same
        # file an actual user's runs live in) as a side effect -- snapshot
        # it before touching it so it can be put back at the end, same
        # restore-to-original-state discipline as verify_api.py. Same for
        # the baseline *selection* itself (eat/baseline.json), which the
        # History flow's "set as baseline" actions change.
        #
        # The snapshot is the FILE, not the set of ids. Deleting whatever
        # is new at the end was sufficient only while history grew without
        # bound; with `HISTORY_MAX_ENTRIES` in force the entries these
        # flows add EVICT the user's oldest runs, and deleting afterwards
        # cannot bring those back.
        history_file_before = (
            DEFAULT_HISTORY_PATH.read_bytes() if DEFAULT_HISTORY_PATH.exists() else None
        )
        history_count_before = len(_get_json(BASE_URL + "/history"))
        baseline_setting_before = _get_json(BASE_URL + "/baseline")
        state_snapshotted = True

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            post_log: list[str] = []
            history_entry_captures: list[dict] = []

            def on_response(resp):
                if resp.request.method == "POST":
                    post_log.append(resp.url)
                if resp.request.method == "GET" and "/history/" in resp.url:
                    try:
                        history_entry_captures.append(resp.json())
                    except Exception:
                        pass
                # Longer/more-specific suffixes first: "/section/from-dxf"
                # etc. would otherwise also match the shorter "/section"
                # check below (endswith).
                for key in (
                    "/section/from-dxf",
                    "/section/from-pdf",
                    "/section/to-dxf",
                    "/baseline",
                    "/section",
                    "/beam",
                    "/materials",
                    "/history",
                ):
                    if resp.url.endswith(key):
                        try:
                            captured[key] = resp.json()
                        except Exception:
                            pass
                        break

            page.on("response", on_response)

            page.goto(BASE_URL)
            page.wait_for_selector(
                "#material-select option[value]:not([value=''])", state="attached", timeout=10000
            )
            checks.append(Check("Page loads, materials populate", True))

            page.select_option("#material-select", label="6061-T6 Aluminum (Extruded)")

            # --- Flow 1: sketch a 50x100mm rectangle ---
            canvas = page.locator("#sketch-canvas")
            rect_mm = [(0, 0), (50, 0), (50, 100), (0, 100)]
            for wx, wy in rect_mm:
                bbox = canvas.bounding_box()
                x, y = canvas_point(bbox, bbox["width"], wx, wy)
                page.mouse.click(x, y)

            page.click("#btn-close-loop")
            page.wait_for_selector("#section-results:not([hidden])", timeout=5000)

            section_resp = captured.get("/section")
            checks.append(Check("Sketch flow: POST /section captured", section_resp is not None))
            if section_resp:
                # Loose tolerance here, not tight: clicking is subject to
                # CDP's sub-pixel mouse-dispatch quantization (confirmed via
                # debug instrumentation during development -- the app's own
                # cssWidth/pixelsPerMm exactly matched what this test
                # computes; only the dispatched click position itself lands
                # up to ~0.5px off), which this exact rectangle amplifies
                # into a fraction-of-a-percent area error. That's a testing
                # artifact of automated pixel-clicking, not an app bug --
                # the DXF-import check below exercises the identical
                # /section code path with exact vertices (no mouse
                # involved) and matches step 1 exactly.
                #
                # The tolerance is 2.5% rather than the 1% it was before the
                # 16:9 layout sweep, because that sweep caps the sketch
                # column: at this 1440px test viewport the canvas is ~673px
                # wide instead of ~749px, so pixels-per-mm drops from ~2.31
                # to ~2.06 and the same fixed ~0.5px dispatch error becomes
                # ~0.24mm instead of ~0.22mm. On the 50mm width that is
                # ~0.5%, and Iyy goes with the width cubed, so ~1.5% --
                # which is exactly what this check now reads. Nothing about
                # the geometry pipeline changed; the ruler the mouse is
                # using just got shorter.
                checks.append(
                    Check(
                        "Sketch flow: Area/Ixx/Iyy close to step-1 (click precision, see comment)",
                        _rel_close(section_resp["area"], 5000.0, tol=2.5e-2)
                        and _rel_close(section_resp["ixx"], 4166666.6666666665, tol=2.5e-2)
                        and _rel_close(section_resp["iyy"], 1041666.6666666666, tol=2.5e-2),
                        f"area={section_resp['area']}, ixx={section_resp['ixx']}, iyy={section_resp['iyy']}",
                    )
                )
            # The main view is deliberately two rows -- Area and Mass Per
            # Length -- with everything else (second moments included) one
            # click away under "More Info". Checked as an exact list, not a
            # count: the point of the trim is *which* two questions the
            # panel answers without being asked, so a swap that kept the
            # count at two would defeat it.
            primary_labels = page.locator("#section-result-grid-primary dt").all_inner_texts()
            checks.append(
                Check(
                    "Sketch flow: main Section Results view is Area + Mass Per Length only",
                    primary_labels == ["Area", "Mass Per Length"],
                    f"{primary_labels}",
                )
            )
            # textContent, not inner_text(): this grid lives inside a
            # collapsed <details>, and inner_text() reports "" for anything
            # not currently rendered.
            secondary_labels = page.eval_on_selector_all(
                "#section-result-grid-secondary dt", "els => els.map((e) => e.textContent)"
            )
            moved_under_more_info = ["Centroid", "Ixx", "Iyy", "Izz"]
            checks.append(
                Check(
                    "Sketch flow: Centroid/Ixx/Iyy/Izz moved into 'More Info', not dropped",
                    all(label in secondary_labels for label in moved_under_more_info)
                    and not any(label in primary_labels for label in moved_under_more_info),
                    f"secondary={secondary_labels}",
                )
            )
            checks.append(
                Check(
                    "Sketch flow: 'More Info' still carries the full secondary set",
                    len(secondary_labels) >= 16,
                    f"{len(secondary_labels)} rows",
                )
            )
            # Scoped to the results panel: the Suggestions section is a
            # second <details class="more-info"> on the page.
            more_info_open = page.locator("#section-results .more-info").get_attribute("open")
            checks.append(Check("Sketch flow: 'More Info' is collapsed by default", more_info_open is None))

            solid_fill_hidden = page.locator("#solid-fill-comparison").evaluate("el => el.hidden")
            checks.append(
                Check(
                    "Sketch flow: solid-fill comparison hidden for a hole-free profile",
                    solid_fill_hidden is True,
                )
            )

            # Axis indicator: a fixed corner gizmo, independent of pan/zoom.
            # Mirrors drawAxisIndicator()'s own geometry (reading the live
            # MARGIN_PX constant from the page, same technique as
            # canvas_point() below) and scans two separate, narrow bands --
            # one running right from the gizmo's origin, one running up --
            # so a swapped X/Y convention would fail this even though
            # "something renders in the corner" would not catch it.
            axis_regions = page.evaluate(
                """
                (marginPx) => {
                    const canvas = document.getElementById('sketch-canvas');
                    const ctx = canvas.getContext('2d');
                    const dpr = window.devicePixelRatio || 1;
                    const cssHeight = canvas.height / dpr;
                    const originX = marginPx + 10;
                    const originY = cssHeight - marginPx - 44;
                    const armLength = 26;

                    function scanBox(cssX0, cssY0, cssX1, cssY1) {
                        const x0 = Math.max(0, Math.floor(cssX0 * dpr));
                        const y0 = Math.max(0, Math.floor(cssY0 * dpr));
                        const x1 = Math.min(canvas.width, Math.ceil(cssX1 * dpr));
                        const y1 = Math.min(canvas.height, Math.ceil(cssY1 * dpr));
                        const data = ctx.getImageData(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0)).data;
                        let line = false, label = false;
                        for (let i = 0; i < data.length; i += 4) {
                            const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
                            if (a > 50 && Math.abs(r - 64) < 25 && Math.abs(g - 70) < 25 && Math.abs(b - 96) < 25) line = true;
                            if (a > 50 && Math.abs(r - 141) < 40 && Math.abs(g - 149) < 40 && Math.abs(b - 196) < 40) label = true;
                        }
                        return { line, label };
                    }

                    return {
                        // Right of origin, thin vertical band: the +X arm/label.
                        xRegion: scanBox(originX, originY - 8, originX + armLength + 22, originY + 8),
                        // Above origin, thin horizontal band: the +Y arm/label.
                        yRegion: scanBox(originX - 8, originY - armLength - 22, originX + 8, originY + 8),
                    };
                }
                """,
                MARGIN_PX,
            )
            checks.append(
                Check(
                    "Axis indicator: +X arm and 'X' label render to the right of the gizmo origin",
                    axis_regions["xRegion"]["line"] and axis_regions["xRegion"]["label"],
                    f"{axis_regions['xRegion']}",
                )
            )
            checks.append(
                Check(
                    "Axis indicator: +Y arm and 'Y' label render above the gizmo origin",
                    axis_regions["yRegion"]["line"] and axis_regions["yRegion"]["label"],
                    f"{axis_regions['yRegion']}",
                )
            )

            # --- Flow 2: DXF import ---
            page.click("#btn-clear")
            captured.pop("/section", None)
            # Use the real catalog file, not the rectangle: it's centered at
            # the origin with negative coordinates (bbox x:[-20,20],
            # y:[-10,10]) -- exactly the case that rendered off in a corner
            # under the old fixed view assumption. The rectangle fixture
            # starts at (0,0) and wouldn't actually exercise the fix.
            fixture = PROJECT_ROOT / "eat" / "fixtures" / "20X40_KJN992891.dxf"
            page.set_input_files("#dxf-file-input", str(fixture))
            page.wait_for_selector("#section-results:not([hidden])", timeout=5000)

            dxf_resp = captured.get("/section/from-dxf")
            checks.append(Check("DXF import flow: POST /section/from-dxf captured", dxf_resp is not None))
            if dxf_resp:
                # Build step 8: Area is now the outer boundary MINUS its 3
                # interior holes (the corrected metal cross-section, per
                # eat.verify_dxf's real-catalog-file check), not the
                # solid-outer 489.81 mm^2 step-7 reported before holes
                # were subtracted.
                checks.append(
                    Check(
                        "DXF import flow: holes-subtracted Area matches step-8 corrected value",
                        _rel_close(dxf_resp["area"], 287.655, tol=1e-3),
                        f"area={dxf_resp['area']}",
                    )
                )
                checks.append(
                    Check(
                        "DXF import flow: 3 interior holes echoed back in response",
                        len(dxf_resp.get("holes", [])) == 3,
                        f"{len(dxf_resp.get('holes', []))} holes",
                    )
                )

            # Solid-fill comparison ("More Info"): the frontend fires an
            # extra POST /section (outer boundary only, no holes) once it
            # sees holes.length > 0, independent of whether the <details>
            # is actually expanded -- wait for it to land rather than
            # relying on the earlier #section-results wait, which resolves
            # before this fire-and-forget fetch necessarily completes.
            # state="attached" (not the default "visible"): the element's
            # own `hidden` attribute is what's under test here, and it sits
            # inside a collapsed <details> that's never opened in this
            # flow, which would otherwise make it fail a visibility wait
            # regardless of its own hidden state.
            page.wait_for_selector("#solid-fill-comparison:not([hidden])", state="attached", timeout=5000)
            solid_resp = captured.get("/section")
            checks.append(
                Check("DXF import flow: solid-fill comparison's POST /section captured", solid_resp is not None)
            )
            if solid_resp and dxf_resp:
                outer_vertices = [tuple(p) for p in dxf_resp["vertices"]]
                expected_solid = analyze_section(outer_vertices, get_material("6061-T6 Aluminum (Extruded)"))
                checks.append(
                    Check(
                        "Solid-fill comparison: Area matches outer-loop-alone calc "
                        "(~489.81 mm^2, step 7's pre-hole-subtraction figure)",
                        _rel_close(solid_resp["area"], expected_solid.area, tol=1e-6)
                        and _rel_close(solid_resp["area"], 489.81, tol=1e-3),
                        f"area={solid_resp['area']}",
                    )
                )
                checks.append(
                    Check(
                        "Solid-fill comparison: Ixx/Iyy match an independent analyze_section call "
                        "on the same outer vertices",
                        _rel_close(solid_resp["ixx"], expected_solid.ixx, tol=1e-6)
                        and _rel_close(solid_resp["iyy"], expected_solid.iyy, tol=1e-6),
                        f"ixx={solid_resp['ixx']} (expected {expected_solid.ixx}), "
                        f"iyy={solid_resp['iyy']} (expected {expected_solid.iyy})",
                    )
                )
                checks.append(
                    Check(
                        "Solid-fill comparison: Area/Ixx/Iyy exceed the holes-subtracted values "
                        "(filling the holes back in can only add material)",
                        solid_resp["area"] > dxf_resp["area"]
                        and solid_resp["ixx"] > dxf_resp["ixx"]
                        and solid_resp["iyy"] > dxf_resp["iyy"],
                    )
                )
            row_count = page.locator("#solid-fill-grid dt").count()
            checks.append(
                Check(
                    "Solid-fill comparison: rendered (Area/Ixx/Iyy/Mass rows)",
                    row_count == 4,
                    f"{row_count} rows",
                )
            )

            checks.append(
                Check(
                    "DXF import flow: profile shows as closed",
                    page.locator("#btn-close-loop").is_disabled() and not page.locator("#btn-export-dxf").is_disabled(),
                )
            )

            # --- Flow 2b: PDF drawing import ---
            # The extraction itself is verified against the real supplier
            # drawings in eat/verify_pdf.py (including overlays rendered
            # back onto the sheets). What matters here is the wiring: the
            # profile reaches the canvas, the provenance panel renders, and
            # the length the drawing states prefills the beam input.
            drawing = PROJECT_ROOT / "B18 - Tower - Extrusion - Standard Light A (1).pdf"
            if drawing.exists():
                page.click("#btn-clear")
                captured.pop("/section/from-pdf", None)
                # Clearing the sketch hides the beam panel, so #input-length
                # isn't fillable through the UI here -- set it directly. It
                # must start empty for the prefill-on-empty behaviour under
                # test to be exercised at all.
                page.evaluate("document.getElementById('input-length').value = ''")
                page.set_input_files("#pdf-file-input", str(drawing))
                page.wait_for_selector("#pdf-import-section:not([hidden])", state="attached", timeout=15000)

                pdf_resp = captured.get("/section/from-pdf")
                checks.append(Check("PDF import flow: POST /section/from-pdf captured", pdf_resp is not None))
                if pdf_resp:
                    info = pdf_resp.get("pdf_import") or {}
                    checks.append(
                        Check(
                            "PDF import flow: 4 holes extracted and echoed back",
                            len(pdf_resp.get("holes", [])) == 4,
                            f"{len(pdf_resp.get('holes', []))} holes",
                        )
                    )
                    checks.append(
                        Check(
                            "PDF import flow: scale reported as 2:1 with every dimension confirmed",
                            info.get("scale") == "2:1"
                            and bool(info.get("dimension_checks"))
                            and all(d["agrees"] for d in info["dimension_checks"]),
                            f"{info.get('scale')}, "
                            f"{sum(1 for d in info.get('dimension_checks', []) if d['agrees'])}"
                            f"/{len(info.get('dimension_checks', []))}",
                        )
                    )

                # The dimension-check table is the panel's whole point --
                # confirm it actually rendered rows, not just that the
                # section un-hid.
                dim_rows = page.locator("#pdf-dim-table tr").count()
                checks.append(
                    Check(
                        "PDF import flow: dimension-check table rendered (header + one row per callout)",
                        dim_rows >= 4,
                        f"{dim_rows} rows",
                    )
                )
                length_value = page.input_value("#input-length")
                checks.append(
                    Check(
                        "PDF import flow: beam length prefilled from the drawing's 2500mm callout",
                        length_value == "2500",
                        f"input-length={length_value!r}",
                    )
                )
                checks.append(
                    Check(
                        "PDF import flow: profile shows as closed and drawn on the canvas",
                        page.locator("#btn-close-loop").is_disabled()
                        and not page.locator("#btn-export-dxf").is_disabled(),
                    )
                )

                # A profile that did NOT come from a PDF must not keep
                # showing the previous import's provenance. This also puts
                # the DXF profile back in place for the beam flow below, so
                # wait for the import to actually land -- #section-results
                # is already visible from the PDF import, so waiting on
                # that would return immediately and race the fetch.
                captured.pop("/section/from-dxf", None)
                captured.pop("/section", None)
                page.set_input_files("#dxf-file-input", str(fixture))
                deadline = time.time() + 20
                while "/section/from-dxf" not in captured and time.time() < deadline:
                    page.wait_for_timeout(100)
                page.wait_for_selector("#pdf-import-section[hidden]", state="attached", timeout=10000)
                # This import also kicks off the solid-fill comparison's
                # fire-and-forget POST /section. Let it land here, or it
                # shows up later as an unexplained POST in the history
                # flow's "frozen, not recomputed" check.
                deadline = time.time() + 20
                while "/section" not in captured and time.time() < deadline:
                    page.wait_for_timeout(100)
                dxf_resp = captured.get("/section/from-dxf", dxf_resp)
                checks.append(
                    Check(
                        "PDF import flow: provenance panel clears when a non-PDF profile is loaded",
                        page.locator("#pdf-import-section").get_attribute("hidden") is not None,
                    )
                )
            else:
                checks.append(
                    Check(
                        "PDF import flow: reference drawing present",
                        False,
                        f"{drawing.name} not found in the project root",
                    )
                )

            # Pixel-level check that the imported profile is actually
            # centered on the canvas, not clustered in a corner: find the
            # bounding box of accent-colored pixels (the confirmed-profile
            # fill/stroke color) and confirm its center falls near the
            # canvas's own center.
            pixel_bbox = page.evaluate(
                """
                () => {
                    const canvas = document.getElementById('sketch-canvas');
                    const ctx = canvas.getContext('2d');
                    const w = canvas.width, h = canvas.height;
                    const data = ctx.getImageData(0, 0, w, h).data;
                    let minX = w, minY = h, maxX = 0, maxY = 0, found = false;
                    for (let y = 0; y < h; y += 2) {
                        for (let x = 0; x < w; x += 2) {
                            const i = (y * w + x) * 4;
                            const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
                            if (a > 50 && r > 180 && g > 220 && b < 150) {
                                found = true;
                                if (x < minX) minX = x;
                                if (x > maxX) maxX = x;
                                if (y < minY) minY = y;
                                if (y > maxY) maxY = y;
                            }
                        }
                    }
                    return found ? { minX, minY, maxX, maxY, canvasW: w, canvasH: h } : null;
                }
                """
            )
            if pixel_bbox is None:
                checks.append(Check("DXF import flow: profile is centered on canvas", False, "no accent pixels found"))
            else:
                shape_cx = (pixel_bbox["minX"] + pixel_bbox["maxX"]) / 2
                shape_cy = (pixel_bbox["minY"] + pixel_bbox["maxY"]) / 2
                canvas_cx = pixel_bbox["canvasW"] / 2
                canvas_cy = pixel_bbox["canvasH"] / 2
                off_x = abs(shape_cx - canvas_cx) / pixel_bbox["canvasW"]
                off_y = abs(shape_cy - canvas_cy) / pixel_bbox["canvasH"]
                checks.append(
                    Check(
                        "DXF import flow: profile is centered on canvas (not in a corner)",
                        off_x < 0.15 and off_y < 0.15,
                        f"shape center offset from canvas center: {off_x:.1%} x, {off_y:.1%} y",
                    )
                )

            # Pixel-level check that interior holes actually render, not
            # just that the API echoes them back: scan for the hole
            # stroke color (#8D95C4). The profile is closed at this point
            # (isClosed=true), so drawPolygon()'s only other user of this
            # color -- the open/unclosed-loop stroke -- can't be active;
            # any matching pixels must be from drawHole().
            hole_pixel_found = page.evaluate(
                """
                () => {
                    const canvas = document.getElementById('sketch-canvas');
                    const ctx = canvas.getContext('2d');
                    const w = canvas.width, h = canvas.height;
                    const data = ctx.getImageData(0, 0, w, h).data;
                    for (let y = 0; y < h; y += 2) {
                        for (let x = 0; x < w; x += 2) {
                            const i = (y * w + x) * 4;
                            const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
                            if (a > 50 && Math.abs(r - 141) < 12 && Math.abs(g - 149) < 12 && Math.abs(b - 196) < 12) {
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """
            )
            checks.append(
                Check(
                    "DXF import flow: interior holes render distinctly (hole-stroke pixels found)",
                    hole_pixel_found,
                )
            )

            # --- Flow 3: beam analysis ---
            page.fill("#input-length", "1000")
            page.select_option("#input-bc", "simply_supported")
            # default point load row (0.5, -1000) is already present
            page.click("#btn-analyze-beam")
            page.wait_for_selector("#beam-results:not([hidden])", timeout=5000)

            beam_resp = captured.get("/beam")
            checks.append(Check("Beam flow: POST /beam captured", beam_resp is not None))
            if beam_resp:
                E_6061_mpa = 69000.0
                ixx = dxf_resp["ixx"] if dxf_resp else 4166666.6666666665
                expected_moment = 1000.0 * 1000.0 / 4  # |P|*L/4
                expected_deflection = 1000.0 * 1000.0**3 / (48 * E_6061_mpa * ixx)
                checks.append(
                    Check(
                        "Beam flow: max_moment matches closed form exactly",
                        _rel_close(abs(beam_resp["max_moment"]), expected_moment, 1e-6),
                        f"expected={expected_moment}, actual={beam_resp['max_moment']}",
                    )
                )
                checks.append(
                    Check(
                        "Beam flow: max_deflection matches closed form for 6061's E",
                        _rel_close(abs(beam_resp["max_deflection"]), expected_deflection, 1e-3),
                        f"expected={expected_deflection}, actual={beam_resp['max_deflection']}",
                    )
                )

            stress_svg = page.locator("#chart-stress svg path.chart-line").count()
            defl_svg = page.locator("#chart-deflection svg path.chart-line").count()
            checks.append(Check("Beam flow: both charts rendered a data line", stress_svg == 1 and defl_svg == 1))

            # Local (plate) buckling was wired into this same /beam call, and
            # the KJN profile (still loaded from flow 2) happens to have two
            # genuinely unclassifiable walls -- curved, unrestrained bore
            # ribs, per eat.local_buckling -- so this is the natural place
            # to check the "Not classified" rendering path without building
            # a dedicated fixture for it.
            lb_resp = beam_resp.get("local_buckling") if beam_resp else None
            checks.append(
                Check(
                    "Local buckling: /beam response carries a local_buckling block with segments",
                    lb_resp is not None and len(lb_resp.get("segments", [])) > 0,
                    f"{lb_resp}",
                )
            )
            if lb_resp:
                page.wait_for_selector("#local-buckling-section:not([hidden])", timeout=5000)
                # inner_text() returns the rendered text -- .panel__subtitle
                # is upper-cased via CSS (text-transform), so compare
                # case-insensitively rather than against the source casing.
                subtitle = page.locator("#local-buckling-section h3").inner_text().upper()
                checks.append(
                    Check(
                        "Local buckling: has its own heading, distinguishing it from Euler buckling above",
                        "LOCAL" in subtitle and "PLATE" in subtitle and "BUCKLING" in subtitle,
                        f"{subtitle!r}",
                    )
                )

                segments = lb_resp["segments"]
                uncertain_segments = [s for s in segments if s["support"] == "uncertain"]
                classified_segments = [s for s in segments if s["support"] != "uncertain"]
                checks.append(
                    Check(
                        "Local buckling: KJN's genuinely unclassifiable walls (curved / unrestrained) are present",
                        len(uncertain_segments) > 0,
                        f"{len(uncertain_segments)} of {len(segments)} segments uncertain",
                    )
                )

                all_rows = page.locator("#local-buckling-table tr[data-wall]").count()
                caveat_rows = page.locator("#local-buckling-table tr.local-buckling-table__caveat").count()
                checks.append(
                    Check(
                        "Local buckling: one table row per segment the API returned",
                        all_rows - caveat_rows == len(segments),
                        f"rows={all_rows}, caveat rows={caveat_rows}, segments={len(segments)}",
                    )
                )

                if uncertain_segments:
                    u = uncertain_segments[0]
                    u_row = page.locator(
                        f'#local-buckling-table tr[data-wall="{u["index"]}"]:not(.local-buckling-table__caveat)'
                    )
                    u_cells = u_row.locator("td").all_inner_texts()
                    checks.append(
                        Check(
                            "Local buckling: an unclassifiable wall shows 'Not classified', not blank",
                            len(u_cells) == LB_COLUMNS and u_cells[1] == "Not classified",
                            f"{u_cells}",
                        )
                    )
                    checks.append(
                        Check(
                            "Local buckling: an unclassifiable wall's k / sigma_cr / class / SF read "
                            "'—', not blank and not a fabricated number",
                            len(u_cells) == LB_COLUMNS
                            and u_cells[5] == "—"
                            and u_cells[6] == "—"
                            and u_cells[7] == "—"
                            and u_cells[9] == "—"
                            and u_cells[10] == "—",
                            f"{u_cells}",
                        )
                    )
                    u_caveat_text = page.locator(
                        f'#local-buckling-table tr.local-buckling-table__caveat[data-wall="{u["index"]}"] td'
                    ).inner_text()
                    checks.append(
                        Check(
                            "Local buckling: the reason it can't be classified is shown, not just the state",
                            u_caveat_text == " · ".join(u["caveats"]) and len(u["caveats"]) > 0,
                            f"caveats={u['caveats']}, shown={u_caveat_text!r}",
                        )
                    )

                if classified_segments:
                    c = classified_segments[0]
                    c_row = page.locator(
                        f'#local-buckling-table tr[data-wall="{c["index"]}"]:not(.local-buckling-table__caveat)'
                    )
                    c_cells = c_row.locator("td").all_inner_texts()
                    checks.append(
                        Check(
                            "Local buckling: a classifiable wall shows a real edge condition, not "
                            "'Not classified'",
                            len(c_cells) == LB_COLUMNS
                            and c_cells[1] == _title_case_label(c["supports_label"])
                            and c_cells[5] != "—",
                            f"{c_cells}",
                        )
                    )

            # --- Flow 4: run history ---
            post_log_before_history = list(post_log)

            page.click("#btn-open-history")
            page.wait_for_selector("#history-modal:not([hidden])", timeout=5000)
            # The modal itself becomes visible synchronously, but its list
            # is populated only after openHistory()'s GET /history resolves
            # -- wait for a row rather than counting immediately, or this
            # races the fetch and sees zero every time.
            page.wait_for_selector("#history-list .history-row", timeout=5000)

            history_rows = page.locator("#history-list .history-row")
            row_count = history_rows.count()
            checks.append(
                Check(
                    "History: modal lists at least 3 entries (rectangle, KJN section, KJN beam)",
                    row_count >= 3,
                    f"{row_count} rows",
                )
            )
            empty_hint_hidden = page.locator("#history-empty-hint").evaluate("el => el.hidden")
            checks.append(Check("History: empty-state hint hidden when entries exist", empty_hint_hidden is True))

            first_row_tags = history_rows.first.locator(".history-row__tag").all_inner_texts()
            checks.append(
                Check(
                    "History: most-recent entry (top row) is the beam analysis, tagged 'beam'",
                    "beam" in first_row_tags,
                    f"tags={first_row_tags}",
                )
            )

            history_entry_captures.clear()
            history_rows.first.locator(".history-row__main").click()
            # state="attached", not the default "visible": a hidden modal
            # is (correctly) not visible, so waiting for "visible" on a
            # selector that only matches once it's hidden would never
            # resolve -- same reasoning as the solid-fill wait above.
            page.wait_for_selector("#history-modal[hidden]", state="attached", timeout=5000)

            new_posts = post_log[len(post_log_before_history):]
            checks.append(
                Check(
                    "History: loading an entry issues no new POST /section or /beam (frozen, not recomputed)",
                    # (loadHistoryEntry also triggers a fresh GET /baseline
                    # for the baseline comparison, but that's a GET, so it
                    # never lands in post_log regardless.)
                    all(not (u.endswith("/section") or u.endswith("/beam")) for u in new_posts),
                    f"new POSTs={new_posts}",
                )
            )
            checks.append(
                Check(
                    "History: GET /history/{id} was captured for the clicked entry",
                    len(history_entry_captures) >= 1,
                )
            )
            if history_entry_captures and beam_resp:
                loaded_beam = history_entry_captures[-1].get("beam_result") or {}
                checks.append(
                    Check(
                        "History: reloaded entry's stored beam_result matches flow 3's live "
                        "/beam response exactly (frozen, not recomputed differently)",
                        loaded_beam.get("max_moment") == beam_resp["max_moment"]
                        and loaded_beam.get("max_deflection") == beam_resp["max_deflection"],
                        f"loaded=(moment={loaded_beam.get('max_moment')}, defl={loaded_beam.get('max_deflection')}), "
                        f"original=(moment={beam_resp['max_moment']}, defl={beam_resp['max_deflection']})",
                    )
                )

            checks.append(
                Check(
                    "History: reloaded view shows both the section and beam results panels",
                    page.locator("#section-results").is_visible() and page.locator("#beam-results").is_visible(),
                )
            )
            reloaded_stress_svg = page.locator("#chart-stress svg path.chart-line").count()
            checks.append(Check("History: reloaded beam chart re-rendered a data line", reloaded_stress_svg == 1))

            # --- Delete a row ---
            page.click("#btn-open-history")
            page.wait_for_selector("#history-modal:not([hidden])", timeout=5000)
            page.wait_for_selector("#history-list .history-row", timeout=5000)
            count_before_delete = page.locator("#history-list .history-row").count()
            page.locator("#history-list .history-row").first.locator(".history-row__delete").click()
            try:
                page.wait_for_function(
                    f"document.querySelectorAll('#history-list .history-row').length === {count_before_delete - 1}",
                    timeout=3000,
                )
                delete_worked = True
            except Exception:
                delete_worked = False
            checks.append(
                Check(
                    "History: delete button removes the row from the list",
                    delete_worked,
                    f"count before delete={count_before_delete}",
                )
            )
            page.click("#btn-close-history")

            # --- Flow 5: baseline comparison ---
            # Import the exact rectangle_50x100.dxf fixture (not mouse
            # clicks -- flow 1's own comment notes those aren't precise
            # enough for a tight match) with 6061-T6 (already selected,
            # unchanged since flow 1): a hand-checkable case against the
            # built-in KJN baseline, geometry and material both fully known
            # on both sides.
            page.click("#btn-clear")
            captured.pop("/section/from-dxf", None)
            page.set_input_files("#dxf-file-input", str(PROJECT_ROOT / "eat" / "fixtures" / "rectangle_50x100.dxf"))
            page.wait_for_selector("#section-results:not([hidden])", timeout=5000)
            page.wait_for_selector("#baseline-section:not([hidden])", timeout=5000)
            page.wait_for_selector("#baseline-metrics .baseline-metric", timeout=5000)

            baseline_name_builtin = page.locator("#baseline-name").inner_text()
            checks.append(
                Check(
                    "Baseline: indicator shows the built-in profile by default",
                    "KJN" in baseline_name_builtin,
                    baseline_name_builtin,
                )
            )

            def read_baseline_metrics():
                """{(group, label): (profile_value, baseline_value)} read
                from the dual-bar DOM structure -- group titles and metric
                blocks are siblings in #baseline-metrics, in document order,
                so a group applies to every metric until the next title."""
                raw = page.evaluate(
                    """
                    () => {
                        const container = document.getElementById('baseline-metrics');
                        const out = [];
                        let group = null;
                        for (const child of container.children) {
                            if (child.classList.contains('baseline-metric-group__title')) {
                                group = child.textContent;
                            } else if (child.classList.contains('baseline-metric')) {
                                const label = child.querySelector('.baseline-metric__label').textContent;
                                const values = [...child.querySelectorAll('.baseline-metric__row-value')].map(e => e.textContent);
                                const note = child.querySelector('.baseline-metric__note');
                                out.push({
                                    group, label,
                                    profile: values[0], baseline: values[1],
                                    note: note ? note.textContent : null,
                                });
                            }
                        }
                        return out;
                    }
                    """
                )

                def parse(text):
                    return float(text.split(" ", 1)[0].replace(",", ""))

                return {(m["group"], m["label"]): (parse(m["profile"]), parse(m["baseline"])) for m in raw}

            def read_baseline_metric_meta():
                """{(group, label): (unit_string, note_or_None)} from the
                same DOM, for the unit-simplification and material-property
                disclaimer checks."""
                raw = page.evaluate(
                    """
                    () => {
                        const container = document.getElementById('baseline-metrics');
                        const out = [];
                        let group = null;
                        for (const child of container.children) {
                            if (child.classList.contains('baseline-metric-group__title')) {
                                group = child.textContent;
                            } else if (child.classList.contains('baseline-metric')) {
                                const note = child.querySelector('.baseline-metric__note');
                                out.push({
                                    group,
                                    label: child.querySelector('.baseline-metric__label').textContent,
                                    value: child.querySelector('.baseline-metric__row-value').textContent,
                                    note: note ? note.textContent : null,
                                });
                            }
                        }
                        return out;
                    }
                    """
                )
                return {
                    (m["group"], m["label"]): (m["value"].split(" ", 1)[1].strip(), m["note"])
                    for m in raw
                }

            metrics_vs_builtin = read_baseline_metrics()
            # 6061-T6 (E=69000 MPa, yield=241 MPa) 50x100mm rectangle
            # (textbook Ixx/Iyy/Z, mass=2700*0.005=13.5 kg/m) vs the
            # built-in KJN baseline's own already-cross-checked figures
            # (eat/verify_baseline.py: ei_xx=826,448,688.97,
            # ei_yy=3,187,731,329.51, governing Zxx=1199.49, Zyy=2313.30,
            # mass=0.7766685396, yield=214 MPa for 6063-T6).
            #
            # Displayed in simplified SI units rather than the compound
            # N/(kg/m) and N·mm²/(kg/m) these come out of the engine in, so
            # the expected figures below are the same quantities rescaled
            # once (see app.js's SPECIFIC_* scales):
            #   EA/μ    N/(kg/m)     -> MN·m/kg  (x 1e-6)
            #   EI/μ    N·mm²/(kg/m) -> N·m³/kg  (x 1e-6)
            #   σy·A/μ  N/(kg/m)     -> kN·m/kg  (x 1e-3)
            #   σy·Z/μ  N·mm/(kg/m)  -> N·m²/kg  (x 1e-3)
            expected = {
                (None, "Mass Per Length"): (13.499999999999968, 0.7766685396237651),
                ("Stiffness-to-Weight", "Axial"): (25.555555555555556, 25.51851851851852),
                ("Stiffness-to-Weight", "Bending (X)"): (5324.074074073969, 4104.3652045655146),
                ("Stiffness-to-Weight", "Bending (Y)"): (21296.296296296963, 1064.0944583319955),
                ("Strength-to-Weight", "Axial"): (89.25925925925927, 79.25925925925926),
                ("Strength-to-Weight", "Bending (X)"): (743.8271604938096, 637.3970154675323),
                ("Strength-to-Weight", "Bending (Y)"): (1487.6543209877005, 330.50248778381286),
            }
            metrics_match = all(
                key in metrics_vs_builtin
                and _rel_close(metrics_vs_builtin[key][0], exp_profile, 5e-3)
                and _rel_close(metrics_vs_builtin[key][1], exp_baseline, 5e-3)
                for key, (exp_profile, exp_baseline) in expected.items()
            )
            checks.append(
                Check(
                    "Baseline: all 7 rectangle-vs-KJN-baseline dual-bar values match hand-computed figures",
                    metrics_match,
                    f"{metrics_vs_builtin} vs expected {expected}",
                )
            )

            # --- Units: simplest SI form, no fraction-inside-a-fraction ---
            # These were the only compound units left in the UI. The values
            # above already prove the rescaling is numerically right; this
            # proves the label was rescaled with it, so the two can't drift
            # apart (a wrong unit on a right number is the worse failure).
            meta = read_baseline_metric_meta()
            expected_units = {
                (None, "Mass Per Length"): "kg/m",
                ("Stiffness-to-Weight", "Axial"): "MN·m/kg",
                ("Stiffness-to-Weight", "Bending (X)"): "N·m³/kg",
                ("Stiffness-to-Weight", "Bending (Y)"): "N·m³/kg",
                ("Strength-to-Weight", "Axial"): "kN·m/kg",
                ("Strength-to-Weight", "Bending (X)"): "N·m²/kg",
                ("Strength-to-Weight", "Bending (Y)"): "N·m²/kg",
            }
            unit_mismatches = [
                f"{key}: {meta.get(key, (None, None))[0]!r} != {want!r}"
                for key, want in expected_units.items()
                if meta.get(key, (None, None))[0] != want
            ]
            checks.append(
                Check(
                    "Units: every baseline metric reads in simplest SI form (no '/(kg/m)' compounds)",
                    not unit_mismatches,
                    "; ".join(unit_mismatches),
                )
            )
            compound_units = [
                f"{key}: {unit!r}" for key, (unit, _) in meta.items() if "(" in unit or "/(" in unit
            ]
            checks.append(
                Check(
                    "Units: no unit anywhere in the comparison contains a nested fraction",
                    not compound_units,
                    "; ".join(compound_units),
                )
            )

            # --- The two material-property-only rows carry their note ---
            # Exactly two of the seven reduce algebraically to a material
            # property (EA/μ = E/ρ, σy·A/μ = σy/ρ), so they read identically
            # for any profile in the same alloy -- correct, but it looks
            # like a broken comparison unexplained. The bending rows keep an
            # I or a Z that does not cancel, and mass per length keeps its
            # area, so those must NOT carry the note: checked both ways, so
            # this can't pass by noting everything.
            noted = {key for key, (_, note) in meta.items() if note}
            expected_noted = {
                ("Stiffness-to-Weight", "Axial"),
                ("Strength-to-Weight", "Axial"),
            }
            checks.append(
                Check(
                    "Baseline: the two axial rows -- and only those -- are flagged as material-property-only",
                    noted == expected_noted,
                    f"noted={sorted(str(k) for k in noted)}",
                )
            )
            # And the note says the thing that actually explains it.
            axial_note = meta[("Stiffness-to-Weight", "Axial")][1] or ""
            checks.append(
                Check(
                    "Baseline: the axial note explains the cancellation rather than just warning",
                    "cancels the area" in axial_note and "density" in axial_note,
                    f"{axial_note[:120]!r}",
                )
            )
            # Same material both sides here (6061 rectangle vs 6063 KJN are
            # different alloys, so these differ) -- but the *claim* the note
            # makes is checked directly in eat/verify_baseline.py, which
            # recomputes both axial metrics for two unrelated geometries in
            # one material and asserts they land on E/rho and yield/rho.

            # --- Baseline is reachable from, and consistent across, every
            # entry point. It gets switched constantly, so there are three:
            # the topbar (always visible, and doubles as the readout), the
            # comparison panel's "Change…", and the History modal that lists
            # the candidates. Redundancy is only useful if they agree, so
            # what's checked is that all three name the same baseline and
            # that both buttons reach the same picker. ---
            panel_name = page.locator("#baseline-name").inner_text().strip()
            topbar_name = page.locator("#topbar-baseline-name").inner_text().strip()
            page.click("#btn-topbar-baseline")
            page.wait_for_selector("#history-modal:not([hidden])", timeout=5000)
            modal_name = page.locator("#history-baseline-name").inner_text().strip()
            checks.append(
                Check(
                    "Baseline: the topbar readout opens the picker, and topbar / panel / modal "
                    "all name the same baseline",
                    panel_name != "" and panel_name == topbar_name == modal_name,
                    f"panel={panel_name!r}, topbar={topbar_name!r}, modal={modal_name!r}",
                )
            )
            page.click("#btn-close-history")
            page.wait_for_selector("#history-modal", state="hidden", timeout=3000)
            page.click("#btn-change-baseline")
            page.wait_for_selector("#history-modal:not([hidden])", timeout=5000)
            checks.append(
                Check(
                    "Baseline: the comparison panel's 'Change…' reaches the same picker",
                    page.locator("#history-modal").is_visible(),
                )
            )
            page.click("#btn-close-history")
            page.wait_for_selector("#history-modal", state="hidden", timeout=3000)

            # --- Set this same rectangle entry as the baseline ---
            page.click("#btn-open-history")
            page.wait_for_selector("#history-modal:not([hidden])", timeout=5000)
            page.wait_for_selector("#history-list .history-row", timeout=5000)
            first_row = page.locator("#history-list .history-row").first
            first_row.locator(".history-row__actions button").first.click()  # "Set as Baseline"
            page.wait_for_function(
                "document.querySelector('#history-list .history-row .history-row__tag--baseline') !== null",
                timeout=3000,
            )
            checks.append(Check("Baseline: 'Set as Baseline' tags the row in the History list", True))
            page.click("#btn-close-history")

            page.wait_for_function(
                "document.getElementById('baseline-name').textContent.indexOf('KJN') === -1",
                timeout=3000,
            )
            metrics_vs_self = read_baseline_metrics()
            checks.append(
                Check(
                    "Baseline: comparing the rectangle against itself (now the baseline) reads equal bars",
                    len(metrics_vs_self) == len(expected)
                    and all(_rel_close(profile, base, 1e-2) for profile, base in metrics_vs_self.values()),
                    f"{metrics_vs_self}",
                )
            )

            baseline_after_set = _get_json(BASE_URL + "/baseline")
            checks.append(
                Check(
                    "Baseline: GET /baseline reflects the just-selected history entry",
                    baseline_after_set["source"] == "history",
                    f"{baseline_after_set}",
                )
            )

            # --- Simulated restart: kill and relaunch the server process,
            # then confirm the selection survived on disk, not just in the
            # running process's memory. ---
            server = restart_server(server)
            baseline_after_restart = _get_json(BASE_URL + "/baseline")
            checks.append(
                Check(
                    "Baseline: selection persists across a simulated restart (fresh process, same file)",
                    baseline_after_restart["source"] == "history"
                    and baseline_after_restart["history_entry_id"] == baseline_after_set["history_entry_id"],
                    f"before={baseline_after_set}, after={baseline_after_restart}",
                )
            )

            # And a fresh page load against the restarted server still
            # produces the correct (self-comparison, equal bars) figures --
            # not just that the raw setting file survived, but that it's
            # actually wired up correctly end-to-end afterward too.
            page.reload()
            page.wait_for_selector(
                "#material-select option[value]:not([value=''])", state="attached", timeout=10000
            )
            page.select_option("#material-select", label="6061-T6 Aluminum (Extruded)")
            page.set_input_files("#dxf-file-input", str(PROJECT_ROOT / "eat" / "fixtures" / "rectangle_50x100.dxf"))
            page.wait_for_selector("#baseline-section:not([hidden])", timeout=5000)
            page.wait_for_selector("#baseline-metrics .baseline-metric", timeout=5000)
            metrics_after_restart = read_baseline_metrics()
            checks.append(
                Check(
                    "Baseline: post-restart, a fresh analysis still compares correctly against the persisted baseline",
                    len(metrics_after_restart) == len(expected)
                    and all(_rel_close(profile, base, 1e-2) for profile, base in metrics_after_restart.values()),
                    f"{metrics_after_restart}",
                )
            )

            # --- Flow 6: design review panel (DFM / Structural) ---
            # The rectangle fixture is still loaded from flow 5, and a solid
            # bar has exactly one thing to say about it -- a wasted-core
            # finding (see eat/verify_suggestions.py), which is a Structural
            # one. So this checks the panel sits under the canvas, that the
            # finding lands in the Structural column and NOT the DFM one
            # (with DFM showing its empty state rather than nothing at all),
            # and that hovering paints it onto the sketch in that category's
            # own colour, clicking pins it there.
            page.wait_for_selector("#suggestions-section:not([hidden])", timeout=8000)

            # Placement: the panel must render below the sketch canvas, not
            # beside it or above it -- checked geometrically, since that is
            # the actual requirement, not a class name.
            canvas_box = page.locator("#sketch-canvas").bounding_box()
            review_box = page.locator("#suggestions-section").bounding_box()
            checks.append(
                Check(
                    "Design review: the panel sits below the profile canvas",
                    review_box["y"] >= canvas_box["y"] + canvas_box["height"],
                    f"canvas bottom={canvas_box['y'] + canvas_box['height']}, panel top={review_box['y']}",
                )
            )

            page.wait_for_selector("#structural-list .suggestion", timeout=5000)
            structural_titles = page.locator("#structural-list .suggestion__title").all_inner_texts()
            dfm_titles = page.locator("#dfm-list .suggestion__title").all_inner_texts()
            checks.append(
                Check(
                    "Design review: the solid rectangle's wasted-core finding is filed under Structural",
                    len(structural_titles) == 1 and "middle third" in structural_titles[0],
                    f"structural={structural_titles}",
                )
            )
            checks.append(
                Check(
                    "Design review: it is NOT also (or instead) listed under DFM",
                    dfm_titles == [],
                    f"dfm={dfm_titles}",
                )
            )
            checks.append(
                Check(
                    "Design review: the empty DFM category states it passed rather than rendering blank",
                    page.locator("#dfm-empty").is_visible()
                    and not page.locator("#structural-empty").is_visible(),
                    f"dfm-empty visible={page.locator('#dfm-empty').is_visible()}, "
                    f"structural-empty visible={page.locator('#structural-empty').is_visible()}",
                )
            )
            checks.append(
                Check(
                    "Design review: the finding names the geometry it applies to",
                    page.locator("#structural-list .suggestion__detail").count() == 1
                    and "Ixx" in page.locator("#structural-list .suggestion__detail").first.inner_text(),
                    page.locator("#structural-list .suggestion__detail").first.inner_text()[:120],
                )
            )
            # The Structural column has to say where the load-dependent
            # buckling checks went, or the split reads as "buckling was
            # dropped" rather than "buckling lives in Beam Results".
            crossref = page.locator("#structural-group .review-group__crossref").inner_text()
            checks.append(
                Check(
                    "Design review: Structural points at Beam Results for the load-dependent buckling checks",
                    "Beam Results" in crossref and "buckling" in crossref.lower(),
                    f"{crossref[:140]!r}",
                )
            )

            # Pixel-level: the highlight is drawn in the finding's own
            # category colour -- --structural (#8FA6FF) for this one, not the
            # --warning amber a DFM finding would use. Counting those exact
            # pixels says both that it rendered at all and that it rendered
            # as the right category, rather than just that a class toggled.
            def highlight_pixels(r0, g0, b0):
                return page.evaluate(
                    """
                    ([r0, g0, b0]) => {
                        const canvas = document.getElementById('sketch-canvas');
                        const data = canvas.getContext('2d')
                            .getImageData(0, 0, canvas.width, canvas.height).data;
                        let n = 0;
                        for (let i = 0; i < data.length; i += 4) {
                            if (Math.abs(data[i] - r0) < 20 && Math.abs(data[i + 1] - g0) < 20
                                && Math.abs(data[i + 2] - b0) < 20) n++;
                        }
                        return n;
                    }
                    """,
                    [r0, g0, b0],
                )

            def structural_pixels():
                return highlight_pixels(0x8F, 0xA6, 0xFF)

            def dfm_pixels():
                return highlight_pixels(0xFF, 0xB8, 0x4D)

            before_hover = structural_pixels()
            page.locator("#structural-list .suggestion").first.hover()
            page.wait_for_timeout(250)
            during_hover = structural_pixels()
            during_hover_dfm = dfm_pixels()
            checks.append(
                Check(
                    "Design review: hovering a Structural finding highlights it on the canvas "
                    "in the Structural colour, not the DFM one",
                    before_hover == 0 and during_hover > 0 and during_hover_dfm == 0,
                    f"before={before_hover}, hovering={during_hover}, dfm-coloured={during_hover_dfm}",
                )
            )

            page.locator("#structural-list .suggestion").first.click()
            page.mouse.move(5, 5)
            page.wait_for_timeout(250)
            pinned_class = page.locator("#structural-list .suggestion").first.get_attribute("class") or ""
            checks.append(
                Check(
                    "Design review: clicking pins the highlight so it survives the mouse leaving",
                    "suggestion--pinned" in pinned_class and structural_pixels() > 0,
                    f"class={pinned_class!r}, pixels={structural_pixels()}",
                )
            )

            page.locator("#structural-list .suggestion").first.click()  # unpin, so the screenshot is clean
            page.wait_for_timeout(150)

            # A solid bar has no wall structure for this check to say
            # anything about (see eat/verify_local_buckling.py) -- confirm
            # the panel disappears entirely rather than rendering an empty
            # table, while the Euler summary above it still renders fully.
            # Reuses the existing rectangle_50x100.dxf fixture; no new file.
            # Run before the tube reference case below (not after), so the
            # final screenshot shows the actual new panel in action rather
            # than this "nothing to show" state.
            #
            # #input-length is filled explicitly rather than relying on
            # whatever flow 3 last left it at: flow 5's post-restart
            # page.reload() above resets every form value to blank, and
            # nothing beam-related runs between there and here to refill it.
            page.click("#btn-clear")
            captured.pop("/section/from-dxf", None)
            captured.pop("/beam", None)
            page.set_input_files(
                "#dxf-file-input", str(PROJECT_ROOT / "eat" / "fixtures" / "rectangle_50x100.dxf")
            )
            page.wait_for_selector("#section-results:not([hidden])", timeout=5000)
            page.fill("#input-length", "1000")
            page.click("#btn-analyze-beam")
            page.wait_for_selector("#beam-results:not([hidden])", timeout=5000)
            page.wait_for_timeout(150)
            checks.append(
                Check(
                    "Local buckling flow: hidden entirely for a solid bar (no wall structure to report), "
                    "while the Euler result above it still renders",
                    page.locator("#local-buckling-section").is_hidden()
                    and page.locator("#beam-summary-grid dt").count() > 0,
                )
            )

            # --- Flow 7: local (plate) buckling panel, reference case ---
            # The documented reference case from this session's backend work:
            # a 60x40 tube with a 1.2mm wall (3mm outer/inner fillets), 2m
            # simply supported, an 800N mid-span point load and a 4kN axial
            # load. Global Euler buckling comfortably passes (SF ~2.74) while
            # one wall's local plate buckling fails (SF ~0.77) -- exactly the
            # case this feature exists to catch and the Euler check alone
            # would miss. Built directly with ezdxf (temp dir kept inside the
            # project tree, not the OS default, per this project's sandbox
            # convention -- see eat/verify_dxf.py) rather than committing a
            # new binary fixture for one geometry only this check uses.
            outer_pts = rounded_rect(0, 0, 60, 40, 3.0)
            hole_pts = rounded_rect(1.2, 1.2, 58.8, 38.8, 3.0, ccw=False)

            with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "eat") as tmp:
                tube_path = Path(tmp) / "tube_60x40_1p2mm.dxf"
                doc = ezdxf.new(dxfversion="R2010")
                doc.units = ezdxf.units.MM
                msp = doc.modelspace()
                outer_pl = msp.add_lwpolyline(outer_pts)
                outer_pl.closed = True
                hole_pl = msp.add_lwpolyline(hole_pts)
                hole_pl.closed = True
                doc.saveas(tube_path)

                page.click("#btn-clear")
                captured.pop("/section/from-dxf", None)
                captured.pop("/beam", None)
                page.select_option("#material-select", label="6063-T6 Aluminum (Extruded)")
                page.set_input_files("#dxf-file-input", str(tube_path))
                page.wait_for_selector("#section-results:not([hidden])", timeout=5000)

            tube_section_resp = captured.get("/section/from-dxf")
            checks.append(Check("Local buckling flow: tube profile imported", tube_section_resp is not None))

            page.fill("#input-length", "2000")
            page.select_option("#input-bc", "simply_supported")
            while page.locator(".point-load-row__remove").count() > 0:
                page.locator(".point-load-row__remove").first.click()
            page.click("#btn-add-load")
            page.locator(".point-load-row input.input").nth(0).fill("0.5")
            page.locator(".point-load-row input.input").nth(1).fill("-800")
            page.fill("#input-axial", "4000")
            page.click("#btn-analyze-beam")
            page.wait_for_selector("#beam-results:not([hidden])", timeout=5000)
            page.wait_for_selector("#local-buckling-section:not([hidden])", timeout=5000)

            tube_beam_resp = captured.get("/beam")
            checks.append(Check("Local buckling flow: POST /beam captured for the tube", tube_beam_resp is not None))

            if tube_beam_resp:
                # Independent re-derivation of the same scenario from the
                # exact vertices used to build the DXF -- not "the API
                # returned something", but "the API and the display both
                # agree with the math, computed a second way". This is
                # what ties the captured/rendered numbers back to the
                # documented reference case (global SF 2.74 / local SF
                # 0.77) rather than the test just trusting its own server.
                material = get_material("6063-T6 Aluminum (Extruded)")
                expected_section = analyze_section(outer_pts, material, holes=[hole_pts])
                expected_beam = analyze_beam(
                    expected_section,
                    material,
                    length=2000.0,
                    boundary_condition=BoundaryCondition.SIMPLY_SUPPORTED,
                    point_loads=[PointLoad(0.5, -800.0)],
                    axial_load=4000.0,
                )
                expected_lb = analyze_local_buckling(
                    outer_pts,
                    [hole_pts],
                    material,
                    applied_axial_stress=abs(4000.0) / expected_section.area,
                    moment=expected_beam.max_moment,
                    bending_axis=expected_beam.load_axis,
                    section=expected_section,
                )
                rated = [s for s in expected_lb.segments if s.safety_factor is not None]
                expected_governing = min(rated, key=lambda s: s.safety_factor) if rated else None

                checks.append(
                    Check(
                        "Local buckling flow: reproduces the documented reference case "
                        "(global Euler SF ~2.74, worst local plate SF ~0.77)",
                        expected_governing is not None
                        and _rel_close(expected_beam.buckling_safety_factor, 2.74, tol=2e-3)
                        and _rel_close(expected_governing.safety_factor, 0.77, tol=2e-3),
                        f"global SF={expected_beam.buckling_safety_factor:.4f}, "
                        f"worst local SF={expected_governing.safety_factor if expected_governing else None}",
                    )
                )
                checks.append(
                    Check(
                        "Local buckling flow: API's global buckling SF matches the independent computation",
                        _rel_close(
                            tube_beam_resp["buckling_safety_factor"], expected_beam.buckling_safety_factor, 1e-4
                        ),
                        f"api={tube_beam_resp['buckling_safety_factor']}, "
                        f"expected={expected_beam.buckling_safety_factor}",
                    )
                )

                api_lb = tube_beam_resp.get("local_buckling")
                checks.append(
                    Check(
                        "Local buckling flow: API's local_buckling has one segment per expected wall",
                        api_lb is not None and len(api_lb["segments"]) == len(expected_lb.segments),
                        f"api={len(api_lb['segments']) if api_lb else None}, expected={len(expected_lb.segments)}",
                    )
                )

                if api_lb and len(api_lb["segments"]) == len(expected_lb.segments):
                    # 1. API JSON vs. the independent computation -- same
                    #    physics, computed twice, from the same vertices.
                    field_tol = {
                        "width": 3e-3,
                        "thickness": 3e-3,
                        "slenderness": 3e-3,
                        "k": 3e-3,
                        "elastic_critical_stress": 1e-2,
                        "applied_stress": 1e-2,
                        "safety_factor": 1.5e-2,
                    }
                    mismatches = []
                    api_by_index = {s["index"]: s for s in api_lb["segments"]}
                    for exp_seg in expected_lb.segments:
                        api_seg = api_by_index.get(exp_seg.index)
                        if api_seg is None:
                            mismatches.append(f"wall {exp_seg.index}: no matching API segment")
                            continue
                        if api_seg["support"] != exp_seg.support:
                            mismatches.append(
                                f"wall {exp_seg.index} support: api={api_seg['support']} expected={exp_seg.support}"
                            )
                        if api_seg["section_class"] != exp_seg.section_class:
                            mismatches.append(
                                f"wall {exp_seg.index} class: api={api_seg['section_class']} "
                                f"expected={exp_seg.section_class}"
                            )
                        for field, tol in field_tol.items():
                            av, ev = api_seg[field], getattr(exp_seg, field)
                            if (av is None) != (ev is None):
                                mismatches.append(f"wall {exp_seg.index} {field}: api={av} expected={ev}")
                            elif av is not None and not _rel_close(av, ev, tol):
                                mismatches.append(f"wall {exp_seg.index} {field}: api={av} expected={ev}")
                    checks.append(
                        Check(
                            "Local buckling flow: every field of every segment matches the "
                            "independent computation",
                            len(mismatches) == 0,
                            "; ".join(mismatches[:8]),
                        )
                    )

                    # 2. What's actually on screen vs. the API JSON -- the
                    #    part of this that's actually a frontend test.
                    dom_mismatches = []
                    for seg in api_lb["segments"]:
                        row = page.locator(
                            f'#local-buckling-table tr[data-wall="{seg["index"]}"]:not(.local-buckling-table__caveat)'
                        )
                        cells = row.locator("td").all_inner_texts()
                        if len(cells) != LB_COLUMNS:
                            dom_mismatches.append(
                                f"wall {seg['index']}: expected {LB_COLUMNS} cells, got {len(cells)}"
                            )
                            continue

                        def parse(text):
                            return None if text == "—" else float(text.replace(",", ""))

                        expected_edges = (
                            "Not classified"
                            if seg["support"] == "uncertain"
                            else _title_case_label(seg["supports_label"])
                        )
                        if cells[0] != str(seg["index"]):
                            dom_mismatches.append(f"wall {seg['index']}: # cell shows {cells[0]!r}")
                        if cells[1] != expected_edges:
                            dom_mismatches.append(
                                f"wall {seg['index']}: edges cell shows {cells[1]!r}, expected {expected_edges!r}"
                            )
                        shown_class = None if cells[7] == "—" else int(cells[7])
                        if shown_class != seg["section_class"]:
                            dom_mismatches.append(
                                f"wall {seg['index']} class: shown={shown_class}, api={seg['section_class']}"
                            )
                        for key, col, tol in (
                            ("width", 2, 3e-3),
                            ("thickness", 3, 3e-3),
                            ("slenderness", 4, 3e-3),
                            ("k", 5, 3e-3),
                            ("elastic_critical_stress", 6, 1e-2),
                            ("applied_stress", 8, 1e-2),
                            ("safety_factor", 9, 1.5e-2),
                            ("effective_safety_factor", 10, 1.5e-2),
                        ):
                            api_val = seg[key]
                            shown = parse(cells[col])
                            if (api_val is None) != (shown is None):
                                dom_mismatches.append(f"wall {seg['index']} {key}: shown={shown}, api={api_val}")
                            elif api_val is not None and not _rel_close(shown, api_val, tol):
                                dom_mismatches.append(f"wall {seg['index']} {key}: shown={shown}, api={api_val}")
                    checks.append(
                        Check(
                            "Local buckling flow: every displayed number matches the API response "
                            "(exactly, allowing for display rounding)",
                            len(dom_mismatches) == 0,
                            "; ".join(dom_mismatches[:8]),
                        )
                    )

                    governing_row_class = (
                        page.locator(
                            f'#local-buckling-table tr[data-wall="{api_lb["governing_index"]}"]'
                            ":not(.local-buckling-table__caveat)"
                        ).get_attribute("class")
                        or ""
                    )
                    checks.append(
                        Check(
                            "Local buckling flow: the governing (worst SF) wall's row is visually marked",
                            "local-buckling-table__row--governing" in governing_row_class,
                            f"class={governing_row_class!r}",
                        )
                    )

            # --- Beam Results: the two questions are visually separate ---
            # The tube case above is the one that has both -- an applied
            # point load AND an axial load -- so it is where the split is
            # actually testable. What matters is that no tile can be read as
            # belonging to the other group: everything the point loads cause
            # in one card, everything about axial stability in the other,
            # each diagram with the quantity it plots.
            # Tile labels are upper-cased by CSS and inner_text() returns
            # what's rendered, so compare in upper case -- same reason the
            # local-buckling subtitle check above does.
            applied_labels = [t.upper() for t in page.locator("#beam-summary-grid dt").all_inner_texts()]
            buckling_labels = [t.upper() for t in page.locator("#beam-buckling-grid dt").all_inner_texts()]
            checks.append(
                Check(
                    "Beam Results: the applied-load group holds moment / stress / deflection and no buckling tile",
                    {"MAX MOMENT", "MAX BENDING STRESS", "MAX DEFLECTION", "BENDING SAFETY FACTOR"}
                    <= set(applied_labels)
                    and not any("BUCKLING" in label or "EULER" in label for label in applied_labels),
                    f"{applied_labels}",
                )
            )
            checks.append(
                Check(
                    "Beam Results: the buckling group holds the Euler tiles and nothing from the load case",
                    {"EULER BUCKLING LOAD", "EULER SAFETY FACTOR"} <= set(buckling_labels)
                    and not any(
                        word in label
                        for label in buckling_labels
                        for word in ("MOMENT", "DEFLECTION", "BENDING SAFETY")
                    ),
                    f"{buckling_labels}",
                )
            )
            # Structural containment, not just adjacency: the per-wall plate
            # table has to live inside the buckling card and the diagrams
            # inside the applied-load card, or the headings are decoration.
            containment = page.evaluate(
                """
                () => ({
                    localBucklingInBucklingGroup: !!document
                        .getElementById('local-buckling-section')
                        .closest('.beam-group--buckling'),
                    chartsInAppliedGroup: !!document
                        .getElementById('chart-deflection')
                        .closest('.beam-group--applied'),
                    groupCount: document.querySelectorAll('#beam-results .beam-group').length,
                })
                """
            )
            checks.append(
                Check(
                    "Beam Results: the per-wall plate table sits inside the buckling group, "
                    "the diagrams inside the applied-load group",
                    containment["localBucklingInBucklingGroup"]
                    and containment["chartsInAppliedGroup"]
                    and containment["groupCount"] == 2,
                    f"{containment}",
                )
            )

            # --- Flow 8: the two safety factors, and the states that
            # aren't a number ---
            # Still on the tube from flow 7, which has both factors.
            lb_headers = [h.strip().upper() for h in page.locator("#local-buckling-table th").all_inner_texts()]
            checks.append(
                Check(
                    "Local buckling: elastic and yield-capped factors are separate, named columns",
                    "ELASTIC SF" in lb_headers and "EFFECTIVE SF" in lb_headers,
                    f"{lb_headers}",
                )
            )
            lb_api = captured.get("/beam", {}).get("local_buckling") or {}
            rated_api = [s for s in lb_api.get("segments", []) if s.get("effective_safety_factor") is not None]
            if rated_api:
                row = page.locator("#local-buckling-table tr[data-wall]:not(.local-buckling-table__caveat)").first
                cells = [c.strip() for c in row.locator("td").all_inner_texts()]
                seg = next(s for s in lb_api["segments"] if str(s["index"]) == cells[0])
                checks.append(
                    Check(
                        "Local buckling: the two displayed factors match the API's two values",
                        _rel_close(float(cells[-2].replace(",", "")), seg["safety_factor"], 5e-3)
                        and _rel_close(float(cells[-1].replace(",", "")), seg["effective_safety_factor"], 5e-3),
                        f"displayed elastic={cells[-2]}, effective={cells[-1]}; "
                        f"api={seg['safety_factor']}, {seg['effective_safety_factor']}",
                    )
                )
                # The effective factor can never exceed the elastic one --
                # capping capacity can only lower it.
                checks.append(
                    Check(
                        "Local buckling: effective SF never exceeds the elastic SF",
                        all(s["effective_safety_factor"] <= s["safety_factor"] * (1 + 1e-9) for s in rated_api),
                        f"{[(s['safety_factor'], s['effective_safety_factor']) for s in rated_api]}",
                    )
                )

            # R8: a TENSILE axial load must suppress the buckling check
            # rather than reporting a negative "safety factor".
            page.fill("#input-axial", "-4000")
            captured.pop("/beam", None)
            page.click("#btn-analyze-beam")
            page.wait_for_selector("#beam-results:not([hidden])", timeout=15000)
            page.wait_for_timeout(200)
            buckling_text = page.locator("#beam-buckling-grid").inner_text()
            checks.append(
                Check(
                    "Tensile axial load: buckling reads 'N/A — tension', not a negative factor",
                    "N/A" in buckling_text and "tension" in buckling_text.lower()
                    and "-" not in buckling_text.split("EULER SAFETY FACTOR")[-1][:24],
                    f"{buckling_text!r}",
                )
            )
            checks.append(
                Check(
                    "Tensile axial load: the API suppresses the factor and says why",
                    (captured.get("/beam", {}).get("buckling_status") == "tension")
                    and captured.get("/beam", {}).get("buckling_safety_factor") is None,
                    f"status={captured.get('/beam', {}).get('buckling_status')}",
                )
            )
            # ...and the Euler LOAD itself is still reported, since it is a
            # property of the profile and length, not of the load case.
            checks.append(
                Check(
                    "Tensile axial load: the Euler buckling load is still shown",
                    "EULER BUCKLING LOAD" in buckling_text.upper(),
                )
            )

            # R10: zero load -> the safety factor is INFINITE, which is a
            # different fact from "this material has no yield strength".
            # Both used to render as a bare "n/a".
            page.fill("#input-axial", "")
            loads = page.locator("#point-loads-list input")
            loads.nth(1).fill("0")
            captured.pop("/beam", None)
            page.click("#btn-analyze-beam")
            page.wait_for_selector("#beam-results:not([hidden])", timeout=15000)
            page.wait_for_timeout(200)
            summary_text = page.locator("#beam-summary-grid").inner_text()
            checks.append(
                Check(
                    "Zero load: the bending safety factor reads as infinite, with the reason",
                    "∞" in summary_text and "no bending stress" in summary_text.lower(),
                    f"{summary_text[:260]!r}",
                )
            )
            checks.append(
                Check(
                    "Zero load: the API distinguishes it from a missing yield strength",
                    captured.get("/beam", {}).get("safety_factor_status") == "no_stress",
                    f"status={captured.get('/beam', {}).get('safety_factor_status')}",
                )
            )

            # --- Flow 9: unsymmetric bending is visible for an L-angle ---
            page.click("#btn-clear")
            page.set_input_files(
                "#dxf-file-input", str(PROJECT_ROOT / "eat" / "fixtures" / "l_angle_50x50x5.dxf")
            )
            page.wait_for_selector("#section-results:not([hidden])", timeout=15000)
            page.fill("#input-length", "1000")
            existing = page.locator("#point-loads-list input")
            if existing.count() >= 2:
                existing.nth(1).fill("-500")
            captured.pop("/beam", None)
            page.click("#btn-analyze-beam")
            page.wait_for_selector("#beam-results:not([hidden])", timeout=15000)
            page.wait_for_timeout(200)
            beam_api = captured.get("/beam", {})
            checks.append(
                Check(
                    "L-angle: the API reports a non-zero asymmetry and out-of-plane deflection",
                    beam_api.get("asymmetry", 0) > 0.1
                    and abs(beam_api.get("max_deflection_transverse", 0)) > 1e-6,
                    f"asymmetry={beam_api.get('asymmetry')}, "
                    f"transverse={beam_api.get('max_deflection_transverse')}",
                )
            )
            summary_text = page.locator("#beam-summary-grid").inner_text().upper()
            checks.append(
                Check(
                    "L-angle: the out-of-plane deflection is surfaced, not silently dropped",
                    "OUT-OF-PLANE DEFLECTION" in summary_text,
                    f"{summary_text[:240]!r}",
                )
            )
            checks.append(
                Check(
                    "L-angle: the peak stress names the fibre it acts on",
                    "FROM CENTROID" in summary_text,
                    f"{summary_text[:240]!r}",
                )
            )
            # ...and a SYMMETRIC profile must not grow that tile.
            page.click("#btn-clear")
            page.set_input_files(
                "#dxf-file-input", str(PROJECT_ROOT / "eat" / "fixtures" / "rectangle_50x100.dxf")
            )
            page.wait_for_selector("#section-results:not([hidden])", timeout=15000)
            page.fill("#input-length", "1000")
            page.click("#btn-analyze-beam")
            page.wait_for_selector("#beam-results:not([hidden])", timeout=15000)
            page.wait_for_timeout(200)
            checks.append(
                Check(
                    "Symmetric rectangle: no out-of-plane tile (it would be identically zero)",
                    "OUT-OF-PLANE" not in page.locator("#beam-summary-grid").inner_text().upper(),
                )
            )

            # --- Flow 10: the busy indicator, and the double-click guard ---
            # A real profile takes 1-3 s; without an indicator that reads as
            # a dead click. Caught by holding /section back in the page.
            page.evaluate(
                """
                () => {
                  const real = window.fetch;
                  window.fetch = function (input, init) {
                    const url = typeof input === 'string' ? input : (input && input.url) || '';
                    if ((url.endsWith('/section') || url.endsWith('/section/from-dxf'))
                        && (init || {}).method === 'POST') {
                      return real.apply(this, arguments)
                        .then(r => new Promise(res => setTimeout(() => res(r), 1200)));
                    }
                    return real.apply(this, arguments);
                  };
                }
                """
            )
            page.click("#btn-clear")
            page.set_input_files(
                "#dxf-file-input", str(PROJECT_ROOT / "eat" / "fixtures" / "l_angle_50x50x5.dxf")
            )
            page.wait_for_timeout(350)
            checks.append(
                Check(
                    "Busy indicator: visible while a section analysis is in flight",
                    page.locator("#busy-indicator").is_visible(),
                    page.locator("#busy-indicator").get_attribute("class") or "",
                )
            )
            page.wait_for_selector("#section-results:not([hidden])", timeout=20000)
            page.wait_for_function(
                "() => document.getElementById('busy-indicator').hidden", timeout=20000
            )
            checks.append(
                Check(
                    "Busy indicator: cleared once every in-flight request has finished",
                    page.locator("#busy-indicator").is_hidden(),
                )
            )
            # R4's user-facing half: the app has to ASK whether any local
            # state file had to be reset on startup. A reset material list
            # leaves it unable to analyze anything, so silence there would
            # be baffling. (What the warnings say, and that a damaged store
            # resets rather than 500-ing, is covered in verify_history.py /
            # verify_materials.py / verify_baseline.py.)
            with page.expect_request(lambda r: r.url.endswith("/warnings"), timeout=15000):
                page.reload(wait_until="networkidle")
            checks.append(Check("Startup asks the server whether any local state was reset", True))
            page.wait_for_selector("#material-select:not([disabled])", timeout=15000)
            checks.append(
                Check(
                    "...and with healthy stores it shows no warning banner",
                    page.locator("#error-banner").is_hidden(),
                    page.locator("#error-banner-text").inner_text(),
                )
            )

            # O7: a double-click must place ONE vertex, not two coincident
            # ones. The engine accepts coincident vertices, so this is about
            # the drawing and the vertex count telling the truth.
            bbox = page.locator("#sketch-canvas").bounding_box()
            page.mouse.dblclick(bbox["x"] + 120, bbox["y"] + 120)
            page.wait_for_timeout(200)
            after_dblclick_undo_disabled = page.locator("#btn-undo").is_disabled()
            page.click("#btn-undo")
            page.wait_for_timeout(150)
            checks.append(
                Check(
                    "Double-click on the canvas places exactly one vertex",
                    (not after_dblclick_undo_disabled)
                    and page.locator("#btn-undo").is_disabled(),
                    f"undo enabled after dblclick={not after_dblclick_undo_disabled}, "
                    f"sketch empty after one undo={page.locator('#btn-undo').is_disabled()}",
                )
            )
            # ...while two genuinely separate clicks still place two.
            page.mouse.click(bbox["x"] + 60, bbox["y"] + 60)
            page.wait_for_timeout(120)
            page.mouse.click(bbox["x"] + 200, bbox["y"] + 60)
            page.wait_for_timeout(120)
            page.click("#btn-undo")
            page.wait_for_timeout(120)
            checks.append(
                Check(
                    "...while two separate clicks still place two vertices",
                    not page.locator("#btn-undo").is_disabled(),
                )
            )
            page.click("#btn-clear")

            # Leave a real profile on screen: the layout checks and the
            # final screenshots below both need the design-review panel
            # rendered, and this flow would otherwise end on a blank canvas.
            page.set_input_files(
                "#dxf-file-input", str(PROJECT_ROOT / "eat" / "fixtures" / "rectangle_50x100.dxf")
            )
            page.wait_for_selector("#section-results:not([hidden])", timeout=20000)
            page.wait_for_selector("#suggestions-section:not([hidden])", timeout=20000)


            # --- Layout: 16:9 / 16:10 desktop and tablet ---
            # The sweep this pass was aimed at, checked at each target size
            # against the properties that are actually required rather than
            # proxies for them:
            #
            #  * nothing overflows horizontally (the per-wall plate table is
            #    the wide element; it has to scroll inside its own wrapper,
            #    never push the page sideways);
            #  * the profile canvas is fully visible without scrolling on a
            #    16:9 / 16:10 screen -- what "optimized for 16:9" has to mean
            #    for a sketching tool, and the reason the sketch column is
            #    capped rather than free to grow with the window;
            #  * the design-review panel renders directly under the canvas,
            #    with nothing wedged between them -- that panel is what now
            #    occupies the space a 4:3 canvas leaves below itself, which
            #    used to be empty navy all the way down;
            #  * the two review categories sit side by side where the column
            #    is wide enough for two readable measures, and stack at
            #    tablet width.
            #
            # Column *balance* is deliberately reported rather than asserted:
            # it depends entirely on how many findings the loaded profile has
            # (the tube on screen here has none, so the panel is two
            # empty-state lines), and a threshold tuned on one fixture would
            # be a fixture check wearing a layout check's label.
            layout_notes: list[str] = []
            layout_failures: list[str] = []
            for label, vw, vh, canvas_must_fit, want_side_by_side in (
                ("1920x1080 16:9", 1920, 1080, True, True),
                ("1536x864 16:9", 1536, 864, True, True),
                ("1440x900 16:10", 1440, 900, True, True),
                ("1024x768 tablet", 1024, 768, False, False),
            ):
                page.set_viewport_size({"width": vw, "height": vh})
                page.wait_for_timeout(350)  # debounced resize -> layoutCanvas()
                m = page.evaluate(
                    """
                    () => {
                        const r = (el) => el.getBoundingClientRect();
                        const canvas = r(document.getElementById('sketch-canvas'));
                        const review = r(document.getElementById('suggestions-section'));
                        const left = r(document.querySelector('.layout__main'));
                        const right = r(document.querySelector('.panel--controls'));
                        const dfm = r(document.getElementById('dfm-group'));
                        const structural = r(document.getElementById('structural-group'));
                        return {
                            scrollWidth: document.documentElement.scrollWidth,
                            innerWidth: window.innerWidth,
                            canvasBottomFromTop: canvas.bottom + window.scrollY,
                            reviewTop: review.top,
                            reviewVisible: review.height > 0,
                            canvasBottom: canvas.bottom,
                            leftHeight: left.height,
                            rightHeight: right.height,
                            categoriesSideBySide: Math.abs(dfm.top - structural.top) < 4,
                        };
                    }
                    """
                )
                taller = max(m["leftHeight"], m["rightHeight"])
                gap_fraction = abs(m["leftHeight"] - m["rightHeight"]) / taller if taller else 0.0
                layout_notes.append(
                    f"{label}: canvas bottom={m['canvasBottomFromTop']:.0f}, "
                    f"column gap={gap_fraction:.0%}"
                )
                if m["scrollWidth"] > m["innerWidth"] + 1:
                    layout_failures.append(
                        f"{label}: page scrolls horizontally ({m['scrollWidth']} > {m['innerWidth']})"
                    )
                if not m["reviewVisible"]:
                    layout_failures.append(f"{label}: the design-review panel did not render")
                # Below the canvas, and immediately below it: a gap larger
                # than the grid gap plus the panel's own chrome would mean
                # something got between them, or the panel drifted out of
                # the sketch column entirely.
                gap_below_canvas = m["reviewTop"] - m["canvasBottom"]
                if not (0 <= gap_below_canvas <= 160):
                    layout_failures.append(
                        f"{label}: design review is not directly below the canvas "
                        f"(gap {gap_below_canvas:.0f}px)"
                    )
                if canvas_must_fit and m["canvasBottomFromTop"] > vh:
                    layout_failures.append(
                        f"{label}: the profile canvas runs past the fold "
                        f"({m['canvasBottomFromTop']:.0f} > {vh})"
                    )
                if m["categoriesSideBySide"] != want_side_by_side:
                    layout_failures.append(
                        f"{label}: DFM/Structural side-by-side={m['categoriesSideBySide']}, "
                        f"expected {want_side_by_side}"
                    )

            checks.append(
                Check(
                    "Layout: no horizontal overflow, canvas above the fold, design review under it, "
                    "categories side-by-side on desktop and stacked on tablet",
                    not layout_failures,
                    "; ".join(layout_failures) if layout_failures else "; ".join(layout_notes),
                )
            )

            # Two screenshots, not one: a 16:9 desktop (the size this sweep
            # targeted) and the tablet width where the two-column grid and
            # the design-review split both collapse.
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            tablet_path = screenshot_path.with_name(screenshot_path.stem + "_tablet" + screenshot_path.suffix)
            page.set_viewport_size({"width": 1024, "height": 768})
            page.wait_for_timeout(350)
            page.screenshot(path=str(tablet_path), full_page=True)
            checks.append(Check(f"Tablet-width screenshot saved to {tablet_path}", tablet_path.exists()))

            page.set_viewport_size({"width": 1920, "height": 1080})
            page.wait_for_timeout(350)
            page.screenshot(path=str(screenshot_path), full_page=True)
            checks.append(Check(f"Screenshot saved to {screenshot_path}", screenshot_path.exists()))

            checks.append(Check("No browser console errors", len(console_errors) == 0, "; ".join(console_errors[:5])))

            browser.close()
    finally:
        # In the `finally`, not at the end of the happy path: these two
        # files are the ones a real user's runs and preferences live in,
        # and a flow that raises (a changed selector, a hung
        # wait_for_selector, Ctrl-C) used to skip the restore entirely and
        # leave this script's fixtures sitting in eat/history.json. That
        # has actually happened. The server is still up at this point --
        # it is torn down in the inner `finally` below, after the restore
        # has had its chance to run.
        try:
            if state_snapshotted:
                _restore_state(checks, history_file_before, history_count_before, baseline_setting_before)
        except Exception as exc:  # never mask the original failure
            checks.append(Check("History/baseline restored to pre-test state", False, repr(exc)))
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    return _report(checks)


def _restore_state(
    checks: list[Check],
    history_file_before: bytes | None,
    history_count_before: int,
    baseline_setting_before: dict | None,
) -> None:
    """Put eat/history.json and eat/baseline.json back exactly as they were
    before this run, by restoring the snapshotted file rather than deleting
    the entries these flows added -- with `HISTORY_MAX_ENTRIES` in force
    those additions evict the user's oldest runs, which deleting cannot
    undo. Then restore whichever baseline was selected, since the baseline
    flow deliberately changes it."""
    if history_file_before is None:
        DEFAULT_HISTORY_PATH.unlink(missing_ok=True)
    else:
        DEFAULT_HISTORY_PATH.write_bytes(history_file_before)
    history_final_count = len(_get_json(BASE_URL + "/history"))
    checks.append(
        Check(
            "History file restored to its pre-test state",
            history_final_count == history_count_before,
            f"before={history_count_before}, after={history_final_count}",
        )
    )

    if baseline_setting_before is not None:
        if baseline_setting_before["source"] == "history":
            _post_json(
                BASE_URL + "/baseline",
                {"type": "history", "entry_id": baseline_setting_before["history_entry_id"]},
            )
        else:
            _post_json(BASE_URL + "/baseline", {"type": "builtin"})
        baseline_final = _get_json(BASE_URL + "/baseline")
        checks.append(
            Check(
                "Baseline setting restored to its pre-test state",
                baseline_final["source"] == baseline_setting_before["source"]
                and baseline_final["history_entry_id"] == baseline_setting_before["history_entry_id"],
                f"before={baseline_setting_before}, after={baseline_final}",
            )
        )


def _report(checks: list[Check]) -> int:
    width = max(len(c.label) for c in checks) + 2
    all_passed = True
    for c in checks:
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
