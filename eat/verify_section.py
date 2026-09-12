"""
Verification harness for the section engine (build step 1).

Runs `analyze_section` against two shapes with known closed-form properties
and compares Area, Ixx, Iyy, and J to hand/textbook values within a small
tolerance:

- A plain rectangle (b x d): exact closed-form formulas.
- A standard doubly-symmetric I-beam: Area/Ixx/Iyy from the sum-of-rectangles
  method (exact for a sharp-cornered I with no fillets); J from the standard
  open thin-walled sum of b*t^3/3 over the three rectangular segments (a
  well-known engineering approximation, not exact — hence the looser
  tolerance on that row).

Run with: python -m eat.verify_section
"""

from __future__ import annotations

from dataclasses import dataclass

from eat.section import Material, analyze_section

MATERIAL = Material(name="Test Steel", E=200_000, nu=0.3, yield_strength=250)


@dataclass
class Check:
    shape: str
    quantity: str
    expected: float
    actual: float
    tol: float  # relative tolerance

    @property
    def rel_error(self) -> float:
        if self.expected == 0:
            return abs(self.actual)
        return abs(self.actual - self.expected) / abs(self.expected)

    @property
    def passed(self) -> bool:
        return self.rel_error <= self.tol


def rectangle_vertices(b: float, d: float) -> list[tuple[float, float]]:
    return [(0, 0), (b, 0), (b, d), (0, d)]


def rectangle_j_approx(a: float, b: float) -> float:
    """Roark's approximate torsion constant for a solid rectangle.

    a = longer side, b = shorter side.
    """
    ratio = b / a
    return a * b**3 * (1 / 3 - 0.21 * ratio * (1 - ratio**4 / 12))


def i_beam_vertices(d: float, bf: float, tf: float, tw: float) -> list[tuple[float, float]]:
    """Doubly-symmetric I-beam, centred on the origin.

    d = overall depth, bf = flange width, tf = flange thickness,
    tw = web thickness. Sharp corners (no fillets).
    """
    hd = d / 2
    hw = tw / 2
    hf = bf / 2
    return [
        (-hf, -hd),
        (hf, -hd),
        (hf, -hd + tf),
        (hw, -hd + tf),
        (hw, hd - tf),
        (hf, hd - tf),
        (hf, hd),
        (-hf, hd),
        (-hf, hd - tf),
        (-hw, hd - tf),
        (-hw, -hd + tf),
        (-hf, -hd + tf),
    ]


def i_beam_hand_calc(d: float, bf: float, tf: float, tw: float) -> dict[str, float]:
    """Sum-of-rectangles hand calc for a sharp-cornered doubly-symmetric I."""
    hw = d - 2 * tf  # web height between flanges

    a_flange = bf * tf
    a_web = tw * hw
    area = 2 * a_flange + a_web

    # Ixx: two flanges (own inertia + parallel axis) + web about its own centroid
    y_flange = (d - tf) / 2  # centroid of each flange from section centroid
    i_flange_own = bf * tf**3 / 12
    ixx = 2 * (i_flange_own + a_flange * y_flange**2) + tw * hw**3 / 12

    # Iyy: flanges + web, all centred on the y axis already
    iyy = 2 * (tf * bf**3 / 12) + hw * tw**3 / 12

    # Open thin-walled approximation: J = sum(b * t^3 / 3) over each segment
    j = 2 * (bf * tf**3 / 3) + hw * tw**3 / 3

    return {"area": area, "ixx": ixx, "iyy": iyy, "j": j}


def run() -> list[Check]:
    checks: list[Check] = []

    # --- Plain rectangle: b=50 mm, d=100 mm ---
    b, d = 50.0, 100.0
    rect_result = analyze_section(rectangle_vertices(b, d), MATERIAL, mesh_size=1.0)
    rect_expected = {
        "area": b * d,
        "ixx": b * d**3 / 12,
        "iyy": d * b**3 / 12,
        "j": rectangle_j_approx(a=d, b=b),
    }
    checks += [
        Check("Rectangle 50x100", "Area", rect_expected["area"], rect_result.area, 1e-3),
        Check("Rectangle 50x100", "Ixx", rect_expected["ixx"], rect_result.ixx, 1e-3),
        Check("Rectangle 50x100", "Iyy", rect_expected["iyy"], rect_result.iyy, 1e-3),
        # J uses an approximate closed-form series -> looser tolerance
        Check("Rectangle 50x100", "J", rect_expected["j"], rect_result.j, 5e-3),
    ]

    # --- Standard I-beam (sharp corners): d=200, bf=100, tf=10, tw=6 mm ---
    d, bf, tf, tw = 200.0, 100.0, 10.0, 6.0
    i_result = analyze_section(i_beam_vertices(d, bf, tf, tw), MATERIAL, mesh_size=1.0)
    i_expected = i_beam_hand_calc(d, bf, tf, tw)
    checks += [
        Check("I-beam 200x100x10/6", "Area", i_expected["area"], i_result.area, 1e-3),
        Check("I-beam 200x100x10/6", "Ixx", i_expected["ixx"], i_result.ixx, 1e-3),
        Check("I-beam 200x100x10/6", "Iyy", i_expected["iyy"], i_result.iyy, 1e-3),
        # Open thin-walled J is a known approximation (no fillet/warping
        # interaction terms) -> looser tolerance
        Check("I-beam 200x100x10/6", "J", i_expected["j"], i_result.j, 5e-2),
    ]

    return checks


def main() -> int:
    checks = run()

    header = f"{'Shape':<22}{'Qty':<6}{'Expected':>16}{'Computed':>16}{'Rel. err':>12}  Status"
    print(header)
    print("-" * len(header))
    all_passed = True
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        print(
            f"{c.shape:<22}{c.quantity:<6}{c.expected:>16,.4f}{c.actual:>16,.4f}"
            f"{c.rel_error * 100:>11.4f}%  {status}"
        )

    print()
    if all_passed:
        print("All checks within tolerance.")
    else:
        print("FAILURES DETECTED — see rows marked FAIL above.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
