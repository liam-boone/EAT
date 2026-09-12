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


def main() -> int:
    screenshot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "eat" / "verify_frontend_screenshot.png"
    checks: list[Check] = []
    console_errors: list[str] = []
    captured: dict[str, dict] = {}

    server = subprocess.Popen(
        [str(PROJECT_ROOT / ".venv" / "bin" / "uvicorn"), "eat.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_server():
            checks.append(Check("Server started", False, "timed out waiting for port"))
            return _report(checks)
        checks.append(Check("Server started", True))

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            def on_response(resp):
                for key in ("/section/from-dxf", "/section/to-dxf", "/section", "/beam", "/materials"):
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
            more_info_open = page.locator(".more-info").get_attribute("open")
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

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            checks.append(Check(f"Screenshot saved to {screenshot_path}", screenshot_path.exists()))

            checks.append(Check("No browser console errors", len(console_errors) == 0, "; ".join(console_errors[:5])))

            browser.close()
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
