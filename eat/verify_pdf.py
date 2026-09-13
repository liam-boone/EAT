"""
Verification for eat.pdf_io — PDF engineering-drawing import.

Run:  python -m eat.verify_pdf  [--overlays DIR]

The thing that can go wrong here is not arithmetic, it is *misreading the
drawing*. So this checks the extraction against facts carried by the
drawings themselves, and renders an overlay of the extracted profile back
onto the original sheet so the result can be confirmed by eye — which is
the only check that really covers "did it trace the right lines".

What is checked, and why each is meaningful
-------------------------------------------

1. **Dimensional self-consistency.** Every linear dimension on the profile
   view is measured off the page (arrowhead tip to arrowhead tip) and
   compared to the value printed beside it. This is independent of the
   title block and of any assumption about scale: it asks the drawing
   whether the geometry being read is the size the drawing says it is.

2. **Stated scale vs drawn scale.** The title block's SCALE must match what
   the dimensions measure. This is the check that catches the worst
   available failure — reading a 2:1 sheet as 1:1 gives a profile that
   looks perfect and has four times the true area.

3. **Consistency of implied density across all seven profiles.** Each
   drawing states a part WEIGHT. Extracted area x length x density should
   reproduce it. The absolute value is not a fair test (the stated weight
   is a CAD figure for the machined part, and rounded) — but the *spread*
   across seven very different profiles is, because a geometry error in
   any one of them would move only that one. Areas here span 390 to
   1894 mm2 and hole counts 4 to 9; the implied density holds inside
   0.3%, which no per-drawing tracing error would survive.

4. **Hole topology against the drawing's own hole callouts.** A sheet that
   says "4 X Ø 5.0 THRU ALL" must yield four holes of Ø5.0.

5. **Failure behaviour.** A sheet that genuinely cannot be read
   confidently must say so rather than return a plausible wrong answer —
   checked against the multi-view assembly drawing, and against
   deliberately corrupted input.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from eat.pdf_io import (
    MM_PER_PT,
    PdfImportError,
    PdfImportResult,
    _fmt_scale,
    import_profile,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The reference drawings, with the facts each sheet states about itself.
# `holes` is what the drawing's own hole callouts add up to, where the
# callouts pin it down; None where the profile's voids are not individually
# called out (chambers, T-slot channels).
REFERENCE = [
    dict(
        file="B18 - Tower - Extrusion - Standard Light A (1).pdf",
        part="DEX05120096",
        scale=2.0,
        length=2500.0,
        weight_g=3048.0,
        holes=4,
    ),
    dict(
        file="B18 - Tower - Extrusion - Standard Light B (1).pdf",
        part="DEX05120098",
        scale=2.0,
        length=2500.0,
        weight_g=2610.0,
        holes=4,
    ),
    dict(
        file="B18 - Tower - Extrusion - Standard Heavy A (1).pdf",
        part="DEX05120097",
        scale=2.0,
        length=2500.0,
        weight_g=4448.0,
        holes=8,
        thru_holes=(4, 5.0),  # "4 X Ø 5.0±0.2 THRU ALL"
    ),
    dict(
        file="B18 - Tower - Extrusion - Standard Heavy B (1).pdf",
        part="DEX05120095",
        scale=2.0,
        length=2500.0,
        weight_g=3625.0,
        holes=6,
        thru_holes=(2, 5.0),
    ),
    dict(
        file="B18 - Tower - Extrusion - Section 10 A (1).pdf",
        part="DEX05120094",
        scale=1.0,
        length=2500.0,
        weight_g=5571.0,
        holes=7,
    ),
    dict(
        file="B18 - Tower - Extrusion - Section 1 Spine A (1).pdf",
        part="DEX05120100",
        scale=2.0,
        length=2500.0,
        weight_g=10839.0,
        holes=8,
        thru_holes=(6, 8.5),  # "6 X Ø 8.5±0.2 THRU ALL"
    ),
    dict(
        file="B18 - Tower - Extrusion - Section 1 ScanF A (1).pdf",
        part="DEX05120099",
        scale=1.0,
        length=2500.0,
        weight_g=12662.0,
        holes=9,
        thru_holes=(4, 8.5),
    ),
]

MULTI_VIEW_SHEET = "RDEX05120940 B18 - Tower - Section 1 Spine - XL_Rev 2.pdf"

# 6063 is nominally 2.70 g/cm3. The stated weights imply slightly less
# because they are CAD figures for the machined part (end threads and
# countersinks) and are rounded, so the absolute value is allowed a wide
# band -- it is the spread across drawings that is the real test.
DENSITY_NOMINAL = 2.70
DENSITY_ABS_TOL = 0.10
DENSITY_SPREAD_TOL = 0.01  # 1% across all seven

results: list[bool] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    results.append(passed)
    status = "PASS" if passed else "FAIL"
    line = f"{label:<74s} {status}"
    if detail:
        line += f"  ({detail})"
    print(line)


def close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def hole_diameters(r: PdfImportResult) -> list[float]:
    """Equivalent diameters of the round holes, largest first."""
    out = []
    for h in r.holes:
        area = 0.0
        for i in range(len(h)):
            x1, y1 = h[i]
            x2, y2 = h[(i + 1) % len(h)]
            area += x1 * y2 - x2 * y1
        area = abs(area) / 2.0
        cx = sum(p[0] for p in h) / len(h)
        cy = sum(p[1] for p in h) / len(h)
        r_max = max(math.dist((cx, cy), p) for p in h)
        circ = area / (math.pi * r_max * r_max) if r_max else 0.0
        if circ >= 0.95:
            out.append(2.0 * math.sqrt(area / math.pi))
    return sorted(out, reverse=True)


def draw_overlay(pdf_path: Path, r: PdfImportResult, out_path: Path) -> None:
    """Render the source sheet with the extracted profile drawn over it."""
    import pymupdf

    src = pymupdf.open(str(pdf_path))
    doc = pymupdf.open()
    page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.show_pdf_page(src[0].rect, src, r.page_index)

    def stroke(points, colour, width):
        pts = r.to_page_points(points)
        shape = page.new_shape()
        for i in range(len(pts)):
            shape.draw_line(pymupdf.Point(*pts[i]), pymupdf.Point(*pts[(i + 1) % len(pts)]))
        shape.finish(color=colour, width=width)
        shape.commit()

    stroke(r.vertices, (0.85, 0.0, 0.0), 1.9)
    for h in r.holes:
        stroke(h, (0.0, 0.35, 0.95), 1.6)

    pix = page.get_pixmap(dpi=170)
    pix.save(str(out_path))
    src.close()
    doc.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eat.verify_pdf")
    parser.add_argument(
        "--overlays",
        metavar="DIR",
        default=None,
        help="also render extracted-profile-over-drawing overlays into DIR",
    )
    args = parser.parse_args(argv)

    overlay_dir = Path(args.overlays) if args.overlays else None
    if overlay_dir:
        overlay_dir.mkdir(parents=True, exist_ok=True)

    missing = [ref["file"] for ref in REFERENCE if not (PROJECT_ROOT / ref["file"]).exists()]
    if missing:
        print("Reference drawings not found in the project root:")
        for m in missing:
            print(f"  - {m}")
        print("\nThese are the real supplier drawings this importer was built against.")
        return 1

    extracted: list[tuple[dict, PdfImportResult]] = []

    print("=== Per-drawing extraction ===")
    for ref in REFERENCE:
        path = PROJECT_ROOT / ref["file"]
        name = ref["file"].split(" - Extrusion - ")[-1].replace(" (1).pdf", "")
        try:
            r = import_profile(path)
        except PdfImportError as exc:
            check(f"{name}: imports", False, str(exc)[:110])
            continue
        extracted.append((ref, r))

        check(f"{name}: part number matches the title block", r.title_block.get("part_number") == ref["part"],
              f"{r.title_block.get('part_number')}")
        check(f"{name}: scale read as {_fmt_scale(ref['scale'])}", close(r.scale, ref["scale"], 1e-6),
              _fmt_scale(r.scale))
        check(f"{name}: extrusion length read as {ref['length']:.0f}mm",
              r.length_mm is not None and close(r.length_mm, ref["length"], 0.5),
              f"{r.length_mm}")
        check(f"{name}: profile is one closed outer boundary + {ref['holes']} hole(s)",
              len(r.holes) == ref["holes"], f"{len(r.holes)} hole(s), {len(r.vertices)} outer vertices")

        # (1) dimensional self-consistency
        checks = r.dimension_checks
        worst = max((abs(c.error_pct) for c in checks), default=999.0)
        check(f"{name}: every measured dimension matches its printed callout",
              bool(checks) and all(c.agrees for c in checks),
              f"{sum(1 for c in checks if c.agrees)}/{len(checks)} agree, worst {worst:.2f}%")

        # (4) hole topology vs the drawing's own THRU-ALL callout
        if "thru_holes" in ref:
            count, dia = ref["thru_holes"]
            found = [d for d in hole_diameters(r) if abs(d - dia) <= 0.15]
            check(f"{name}: finds {count} x Ø{dia:g} THRU-ALL hole(s) as called out",
                  len(found) == count,
                  f"{len(found)} round hole(s) at Ø{dia:g}" +
                  (f" (measured {found[0]:.3f})" if found else ""))

        if overlay_dir:
            out = overlay_dir / f"{name.replace(' ', '_')}.png"
            draw_overlay(path, r, out)

    # (3) cross-drawing consistency of implied density
    print("\n=== Cross-check: implied density from the stated part weights ===")
    densities = []
    for ref, r in extracted:
        rho = ref["weight_g"] / (r.area_mm2 * ref["length"] / 1000.0)
        densities.append(rho)
        name = ref["file"].split(" - Extrusion - ")[-1].replace(" (1).pdf", "")
        print(f"  {name:<22s} area={r.area_mm2:9.2f} mm^2   implied rho = {rho:.4f} g/cm^3")
    if densities:
        lo, hi = min(densities), max(densities)
        spread = (hi - lo) / ((hi + lo) / 2)
        check("Implied density is in the right region for aluminium",
              all(close(d, DENSITY_NOMINAL, DENSITY_ABS_TOL) for d in densities),
              f"{lo:.4f}-{hi:.4f} vs {DENSITY_NOMINAL:.2f} nominal")
        check("Implied density agrees across all 7 profiles (the real area test)",
              spread <= DENSITY_SPREAD_TOL,
              f"spread {spread * 100:.2f}% over areas {min(r.area_mm2 for _, r in extracted):.0f}"
              f"-{max(r.area_mm2 for _, r in extracted):.0f} mm^2")

    # (5) failure behaviour
    print("\n=== Failure behaviour ===")
    multi = PROJECT_ROOT / MULTI_VIEW_SHEET
    if multi.exists():
        try:
            r = import_profile(multi)
            check("Multi-view assembly sheet is refused rather than guessed at", False,
                  f"returned a profile of {r.area_mm2:.0f} mm^2 from page {r.page_index + 1}")
        except PdfImportError as exc:
            msg = str(exc)
            # The refusal must say what it found, page by page -- not just "no".
            per_page = sum(1 for line in msg.splitlines() if line.strip().startswith("- page"))
            reasons = any(
                k in msg.lower()
                for k in ("closes into loops", "more than one", "could be the profile")
            )
            check("Multi-view assembly sheet is refused rather than guessed at", True)
            check(
                "...and the refusal gives a specific reason for every page",
                per_page == 7 and reasons,
                f"{per_page} page(s) explained",
            )

    try:
        import_profile(PROJECT_ROOT / "README.md")
        check("A non-PDF file is rejected", False)
    except PdfImportError as exc:
        check("A non-PDF file is rejected", True, str(exc)[:60])

    try:
        from eat.pdf_io import import_profile_from_bytes

        import_profile_from_bytes(b"%PDF-1.4 not really a pdf", "broken.pdf")
        check("Corrupt PDF bytes are rejected", False)
    except PdfImportError as exc:
        check("Corrupt PDF bytes are rejected", True, str(exc)[:60])

    # A blank vector PDF: valid PDF, no drawing at all.
    try:
        import pymupdf
        from eat.pdf_io import import_profile_from_bytes

        blank = pymupdf.open()
        blank.new_page(width=842, height=595)
        import_profile_from_bytes(blank.tobytes(), "blank.pdf")
        blank.close()
        check("A PDF with no linework is rejected", False)
    except PdfImportError as exc:
        check("A PDF with no linework is rejected", True, str(exc)[:70])

    print()
    failed = results.count(False)
    if failed:
        print(f"{len(results)} checks, {failed} FAILED.")
        return 1
    print(f"{len(results)} checks. All passed.")
    if overlay_dir:
        print(f"Overlays written to {overlay_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
