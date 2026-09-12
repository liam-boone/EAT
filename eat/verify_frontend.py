"""
Verification harness for the frontend (build step 6).

Drives the actual page with a headless browser (Playwright) rather than
re-testing the math (already verified in steps 1-5): this checks that the
frontend calls the right endpoints with the right payloads and correctly
renders what comes back, for three flows:

1. Sketch a 50x100mm rectangle by clicking its four corners -> close loop
   -> confirm the resulting POST /section response has the exact step-1
   Area/Ixx/Iyy, and that the results panel actually renders.
2. Import eat/fixtures/20X40_KJN992891.dxf (the real multi-loop catalog
   file) -> confirm POST /section/from-dxf returns the step-8
   holes-subtracted Area, the 3 interior holes are echoed back, and the
   canvas actually renders them distinctly (not just that the API says
   so) via a pixel-level scan for the hole-stroke color.
3. Fill in length + the default point load, select 6061-T6 Aluminum, run
   Analyze beam -> confirm POST /beam's max_moment matches the material-
   independent closed-form value (|P|*L/4) exactly, max_deflection matches
   |P|*L^3/(48*E*Ixx) for 6061's actual E, and both charts rendered an SVG
   path.
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
   afterward, same as the history sweep above.
6. With that same rectangle still loaded, open the Suggestions panel ->
   confirm it starts collapsed like "More Info", lists the solid bar's one
   finding, and that hovering it paints the highlight onto the sketch
   canvas (counted in --warning pixels, a colour nothing else draws in)
   while clicking pins it so it survives the mouse leaving. Whether the
   suggestions themselves are sound is judged in
   eat/verify_suggestions.py, against profiles with known right answers.

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

Also captures a full-page screenshot (saved under the path given on the
command line, or eat/verify_frontend_screenshot.png by default) and checks
the browser console for errors.

Run with: python -m eat.verify_frontend [screenshot_path]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from eat.materials import get_material
from eat.section import analyze_section

PORT = 8799
BASE_URL = f"http://127.0.0.1:{PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

MARGIN_PX = 28
VIEW_WIDTH_MM = 300


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


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
    history_baseline_ids: set[str] = set()
    baseline_setting_before: dict | None = None
    try:
        if not wait_for_server():
            checks.append(Check("Server started", False, "timed out waiting for port"))
            return _report(checks)
        checks.append(Check("Server started", True))

        # Every flow below hits POST /section, /section/from-dxf, and/or
        # /beam, each of which logs to the real eat/history.json (the same
        # file an actual user's runs live in) as a side effect -- snapshot
        # what's there before touching it so it can all be swept up at the
        # end, same restore-to-original-state discipline as verify_api.py.
        # Same for the baseline *selection* itself (eat/baseline.json),
        # which the History flow's "set as baseline" actions change.
        history_baseline_ids = {e["id"] for e in _get_json(BASE_URL + "/history")}
        baseline_setting_before = _get_json(BASE_URL + "/baseline")

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
                checks.append(
                    Check(
                        "Sketch flow: Area/Ixx/Iyy close to step-1 (click precision, see comment)",
                        _rel_close(section_resp["area"], 5000.0, tol=1e-2)
                        and _rel_close(section_resp["ixx"], 4166666.6666666665, tol=1e-2)
                        and _rel_close(section_resp["iyy"], 1041666.6666666666, tol=1e-2),
                        f"area={section_resp['area']}, ixx={section_resp['ixx']}, iyy={section_resp['iyy']}",
                    )
                )
            primary_count = page.locator("#section-result-grid-primary dt").count()
            checks.append(
                Check(
                    "Sketch flow: trimmed primary results rendered (Area/Centroid/Ixx/Iyy/Izz/Mass)",
                    primary_count == 6,
                    f"{primary_count} rows",
                )
            )
            secondary_count = page.locator("#section-result-grid-secondary dt").count()
            checks.append(
                Check(
                    "Sketch flow: 'More Info' secondary results rendered",
                    secondary_count >= 10,
                    f"{secondary_count} rows",
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
                                out.push({ group, label, profile: values[0], baseline: values[1] });
                            }
                        }
                        return out;
                    }
                    """
                )

                def parse(text):
                    return float(text.split(" ", 1)[0].replace(",", ""))

                return {(m["group"], m["label"]): (parse(m["profile"]), parse(m["baseline"])) for m in raw}

            metrics_vs_builtin = read_baseline_metrics()
            # 6061-T6 (E=69000 MPa, yield=241 MPa) 50x100mm rectangle
            # (textbook Ixx/Iyy/Z, mass=2700*0.005=13.5 kg/m) vs the
            # built-in KJN baseline's own already-cross-checked figures
            # (eat/verify_baseline.py: ei_xx=826,448,688.97,
            # ei_yy=3,187,731,329.51, governing Zxx=1199.49, Zyy=2313.30,
            # mass=0.7766685396, yield=214 MPa for 6063-T6).
            expected = {
                (None, "Mass Per Length"): (13.499999999999968, 0.7766685396237651),
                ("Stiffness-to-Weight", "Axial"): (25555555.555555556, 25518518.51851852),
                ("Stiffness-to-Weight", "Bending (X)"): (5324074074.073969, 4104365204.5655146),
                ("Stiffness-to-Weight", "Bending (Y)"): (21296296296.296963, 1064094458.3319955),
                ("Strength-to-Weight", "Axial"): (89259.25925925927, 79259.25925925926),
                ("Strength-to-Weight", "Bending (X)"): (743827.1604938096, 637397.0154675323),
                ("Strength-to-Weight", "Bending (Y)"): (1487654.3209877005, 330502.48778381286),
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

            # --- Flow 6: suggestions panel ---
            # The rectangle fixture is still loaded from flow 5, and a solid
            # bar has exactly one thing to say about it (see
            # eat/verify_suggestions.py) -- so this checks the panel is
            # collapsed by default, lists that finding, and paints it onto
            # the sketch when hovered and keeps it there when clicked.
            page.wait_for_selector("#suggestions-section:not([hidden])", timeout=8000)
            suggestions_open = page.locator("#suggestions-section details").get_attribute("open")
            checks.append(
                Check("Suggestions: section is collapsed by default, like 'More Info'", suggestions_open is None)
            )

            page.locator("#suggestions-section summary").click()
            page.wait_for_selector("#suggestion-list .suggestion", timeout=5000)
            titles = page.locator(".suggestion__title").all_inner_texts()
            checks.append(
                Check(
                    "Suggestions: the solid rectangle's wasted-core finding is listed",
                    len(titles) == 1 and "middle third" in titles[0],
                    f"{titles}",
                )
            )
            checks.append(
                Check(
                    "Suggestions: the finding names the geometry it applies to",
                    page.locator(".suggestion__detail").count() == 1
                    and "Ixx" in page.locator(".suggestion__detail").first.inner_text(),
                    page.locator(".suggestion__detail").first.inner_text()[:120],
                )
            )

            # Pixel-level: the highlight is drawn in --warning (#FFB84D), a
            # colour nothing else on the canvas uses, so counting those
            # pixels says whether it actually rendered rather than just
            # whether a class got toggled.
            def warning_pixels():
                return page.evaluate(
                    """
                    () => {
                        const canvas = document.getElementById('sketch-canvas');
                        const data = canvas.getContext('2d')
                            .getImageData(0, 0, canvas.width, canvas.height).data;
                        let n = 0;
                        for (let i = 0; i < data.length; i += 4) {
                            if (Math.abs(data[i] - 255) < 25 && Math.abs(data[i + 1] - 184) < 35
                                && Math.abs(data[i + 2] - 77) < 45) n++;
                        }
                        return n;
                    }
                    """
                )

            before_hover = warning_pixels()
            page.locator(".suggestion").first.hover()
            page.wait_for_timeout(250)
            during_hover = warning_pixels()
            checks.append(
                Check(
                    "Suggestions: hovering one highlights it on the sketch canvas",
                    before_hover == 0 and during_hover > 0,
                    f"before={before_hover}, hovering={during_hover}",
                )
            )

            page.locator(".suggestion").first.click()
            page.mouse.move(5, 5)
            page.wait_for_timeout(250)
            pinned_class = page.locator(".suggestion").first.get_attribute("class") or ""
            checks.append(
                Check(
                    "Suggestions: clicking pins the highlight so it survives the mouse leaving",
                    "suggestion--pinned" in pinned_class and warning_pixels() > 0,
                    f"class={pinned_class!r}, pixels={warning_pixels()}",
                )
            )

            page.locator(".suggestion").first.click()  # unpin, so the screenshot is clean
            page.wait_for_timeout(150)

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            checks.append(Check(f"Screenshot saved to {screenshot_path}", screenshot_path.exists()))

            checks.append(Check("No browser console errors", len(console_errors) == 0, "; ".join(console_errors[:5])))

            browser.close()

        # Sweep up every history entry any flow above created (sketch,
        # DXF import, beam analysis all log one; loading/deleting in flow
        # 4 deliberately don't), restoring eat/history.json to its
        # pre-test state -- same discipline verify_api.py applies to the
        # same file.
        history_after = _get_json(BASE_URL + "/history")
        for e in history_after:
            if e["id"] not in history_baseline_ids:
                _delete(f"{BASE_URL}/history/{e['id']}")
        history_final_count = len(_get_json(BASE_URL + "/history"))
        checks.append(
            Check(
                "History file restored to its pre-test state",
                history_final_count == len(history_baseline_ids),
                f"baseline={len(history_baseline_ids)}, final={history_final_count}",
            )
        )

        # The baseline flow below sets a history entry as the baseline
        # (then restarts the server to prove it persists) -- restore
        # whatever was selected before this script ran. The entry it
        # referenced, if any, is untouched by the sweep above (it
        # predates this run, so it's in history_baseline_ids).
        if baseline_setting_before is not None:
            if baseline_setting_before["source"] == "history":
                _post_json(BASE_URL + "/baseline", {"type": "history", "entry_id": baseline_setting_before["history_entry_id"]})
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
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    return _report(checks)


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
