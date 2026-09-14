"""
Verification harness for the beam engine (build step 3).

For each of the four boundary conditions, analyzes a rectangular section
(50 x 100 mm) under a single central point load and compares max bending
moment and max deflection (value and location) against closed-form
textbook results, same PASS/FAIL format as eat/verify_section.py.

The fixed-pinned deflection extremum uses an exact value derived
symbolically from this module's own double-integration formulas
(x* = L(5 - sqrt(5))/5, v_max = sqrt(5) P L^3 / (240 EI)) rather than a
rounded textbook decimal — see the derivation in the project chat history
(sympy critical-point solve of the region-2 deflection curve). This is
the standard, widely-cited result for a propped cantilever under a
central point load (e.g. Hibbeler), just kept in exact form here.

Also verifies the point-load axis selector (`LoadAxis`, build step 9):
an asymmetric L-angle section is analyzed under X-axis and Y-axis point
loads and checked against independently hand-derived (composite-rectangle
method) Ixx/Iyy and Zxx/Zyy values, confirming the two axes give
genuinely different -- and each individually correct -- bending stress
and deflection, plus that mixed X/Y loads in one call are rejected.

Also sanity-checks the Euler buckling effective-length factors (K = 0.5,
2.0, 1.0, 0.6992 for fixed-fixed, fixed-free, simply-supported, and
fixed-pinned respectively) by confirming the resulting critical loads
scale as 1/K^2 relative to the simply-supported (K=1) baseline, for a
simple column with no point loads' worth of complexity beyond that ratio.
Those ratios divide I out, so the ABSOLUTE Pcr is checked separately --
once on a symmetric rectangle, and once on the asymmetric L-angle, where
the weak axis is the principal I22 (108,333 mm^4 by hand) and NOT
min(Ixx, Iyy) (208,333) -- a 1.92x difference in Pcr that a ratio test
cannot see. See `run_principal_axis_buckling_checks`.

Run with: python -m eat.verify_beam
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from eat.beam import BoundaryCondition, K_FACTOR, LoadAxis, PointLoad, analyze_beam
from eat.section import Material, analyze_section

MATERIAL = Material(name="Test Steel", E=200_000, nu=0.3, yield_strength=250)
L = 1000.0  # mm
P = -1000.0  # N (sign is arbitrary for these magnitude checks)


@dataclass
class Check:
    label: str
    expected: float
    actual: float
    tol: float

    @property
    def rel_error(self) -> float:
        if self.expected == 0:
            return abs(self.actual)
        return abs(self.actual - self.expected) / abs(self.expected)

    @property
    def passed(self) -> bool:
        return self.rel_error <= self.tol


def run_bc_checks() -> list[Check]:
    section = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], MATERIAL, mesh_size=1.0)
    EI = MATERIAL.E * section.ixx
    checks: list[Check] = []

    # --- Simply supported, central load ---
    res = analyze_beam(
        section, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P)]
    )
    m_exp = abs(P) * L / 4
    v_exp = abs(P) * L**3 / (48 * EI)
    checks += [
        Check("Simply supported: |M_max|", m_exp, abs(res.max_moment), 1e-6),
        Check("Simply supported: M_max location", L / 2, res.max_moment_position, 1e-6),
        Check("Simply supported: |v_max|", v_exp, abs(res.max_deflection), 1e-4),
        Check("Simply supported: v_max location", L / 2, res.max_deflection_position, 1e-3),
    ]

    # --- Fixed-free cantilever, central load (a = L/2) ---
    res = analyze_beam(section, MATERIAL, L, BoundaryCondition.FIXED_FREE, [PointLoad(0.5, P)])
    m_exp = abs(P) * L / 2
    v_exp = 5 * abs(P) * L**3 / (48 * EI)  # deflection at the free tip
    checks += [
        Check("Fixed-free: |M_max| (at fixed end)", m_exp, abs(res.max_moment), 1e-6),
        Check("Fixed-free: M_max location", 0.0, res.max_moment_position, 1e-9),
        Check("Fixed-free: |v_max| (at tip)", v_exp, abs(res.max_deflection), 1e-4),
        Check("Fixed-free: v_max location", L, res.max_deflection_position, 1e-3),
    ]

    # --- Fixed-fixed, central load ---
    res = analyze_beam(section, MATERIAL, L, BoundaryCondition.FIXED_FIXED, [PointLoad(0.5, P)])
    m_exp = abs(P) * L / 8
    v_exp = abs(P) * L**3 / (192 * EI)
    checks += [
        Check("Fixed-fixed: |M_max| (end/midspan tie)", m_exp, abs(res.max_moment), 1e-6),
        Check("Fixed-fixed: |v_max|", v_exp, abs(res.max_deflection), 1e-4),
        Check("Fixed-fixed: v_max location", L / 2, res.max_deflection_position, 1e-3),
    ]

    # --- Fixed-pinned, central load ---
    res = analyze_beam(section, MATERIAL, L, BoundaryCondition.FIXED_PINNED, [PointLoad(0.5, P)])
    m_exp = 3 * abs(P) * L / 16
    x_exp = L * (5 - math.sqrt(5)) / 5
    v_exp = math.sqrt(5) * abs(P) * L**3 / (240 * EI)
    checks += [
        Check("Fixed-pinned: |M_max| (at fixed end)", m_exp, abs(res.max_moment), 1e-6),
        Check("Fixed-pinned: M_max location", 0.0, res.max_moment_position, 1e-9),
        Check("Fixed-pinned: |v_max|", v_exp, abs(res.max_deflection), 1e-4),
        Check("Fixed-pinned: v_max location", x_exp, res.max_deflection_position, 1e-3),
    ]

    return checks


def run_asymmetric_axis_checks() -> list[Check]:
    """Confirm X-axis vs Y-axis point loads bend an asymmetric section
    about genuinely different, independently hand-calculable moments of
    inertia (Iyy vs Ixx) -- not just that the axis selector runs without
    error.

    Uses a 10mm-thick L-angle (outer footprint 50mm x 60mm) built from two
    non-overlapping rectangles, decomposed by the standard composite-
    section method (each rectangle's own centroidal I plus its
    parallel-axis A*d^2 term) rather than trusting `sectionproperties`'
    own FE result for the expected values:

      R_a (vertical leg):   x in [0, 10],  y in [0, 60]  (10 x 60)
      R_b (horizontal leg): x in [10, 50], y in [0, 10]  (40 x 10)

    Area = 600 + 400 = 1000 mm^2
    Centroid: x_bar = (600*5 + 400*30)/1000 = 15, y_bar = (600*30 + 400*5)/1000 = 20

    Ixx = [10*60^3/12 + 600*(30-20)^2] + [40*10^3/12 + 400*(5-20)^2]
        = [180000 + 60000] + [10000/3 + 90000] = 1,000,000/3 mm^4
    Iyy = [60*10^3/12 + 600*(5-15)^2] + [10*40^3/12 + 400*(30-15)^2]
        = [5000 + 60000] + [160000/3 + 90000] = 625,000/3 mm^4

    Extreme-fibre distances give the governing (smaller) section modulus
    for each axis: Zxx = Ixx/(y_max - y_bar) = (1,000,000/3)/40 = 25000/3
    (top fibre, y=60, governs over the bottom's Ixx/20); Zyy = Iyy/(x_max
    - x_bar) = (625,000/3)/35 = 625000/105 (right fibre, x=50, governs
    over the left's Iyy/15).

    For a simply-supported span (M_max = P*L/4, independent of axis) with
    P=1000N, L=1000mm, E=200,000 MPa, this gives clean closed-form values
    that differ by axis: stress = M_max/Z and deflection = P*L^3/(48*E*I).
    """
    vertices = [(0, 0), (50, 0), (50, 10), (10, 10), (10, 60), (0, 60)]
    section = analyze_section(vertices, MATERIAL, mesh_size=0.5)
    checks: list[Check] = []

    ixx_exp = 1_000_000 / 3
    iyy_exp = 625_000 / 3
    zxx_exp = 25_000 / 3  # governing (smaller) Zxx, top fibre
    zyy_exp = 625_000 / 105  # governing (smaller) Zyy, right fibre

    checks += [
        Check("L-angle: Ixx (composite hand calc)", ixx_exp, section.ixx, 1e-3),
        Check("L-angle: Iyy (composite hand calc)", iyy_exp, section.iyy, 1e-3),
        Check("L-angle: governing Zxx", zxx_exp, min(section.zxx_plus, section.zxx_minus), 1e-3),
        Check("L-angle: governing Zyy", zyy_exp, min(section.zyy_plus, section.zyy_minus), 1e-3),
    ]

    m_exp = abs(P) * L / 4  # moment doesn't depend on axis

    # --- UNSYMMETRIC BENDING, hand-derived --------------------------------
    # This section has Ixy = -150,000, so M/Z about a sketch axis is NOT
    # its bending stress and P L^3 / (48 E Ixx) is NOT its deflection. The
    # expected values below come from unsymmetric-bending theory worked by
    # hand, NOT from the tool -- the previous versions of these four checks
    # compared M/Zxx against a hand-calculated Zxx, which verified the
    # arithmetic of a simplification against the same simplification and so
    # could never have detected that the simplification was the wrong
    # model. That is the mistake being corrected here, so these are written
    # out in full.
    #
    # STRESS. With Mx = M, My = 0 the general linear distribution is
    #   sigma(x, y) = M (Iyy y - Ixy x) / (Ixx Iyy - Ixy^2)
    # and since sigma is linear its extreme is at a vertex. Denominator:
    #   D = (1e6/3)(625000/3) - 150000^2 = (6.25e11 - 2.025e11)/9
    #     = 4.225e11 / 9
    # Evaluating (Iyy y - Ixy x) at each vertex, centroid-relative
    # (centroid is at (15, 20)):
    #   (-15,-20): -6,416,666.67     (35,-20): +1,083,333.33
    #   ( 35,-10): +3,166,666.67     (-5,-10): -2,833,333.33
    #   ( -5, 40): +7,583,333.33 <-- worst    (-15, 40): +6,083,333.33
    # so the governing fibre is the tip of the tall leg, (-5, 40) from the
    # centroid, and with M = 250,000 N.mm
    #   sigma = 250000 * (22,750,000/3) / (4.225e11/9) = 525/13 MPa
    #         = 40.3846...  (against 30.0 for the old M/Zxx answer)
    stress_y_exp = 525 / 13
    #
    # DEFLECTION. Resolve onto the principal axes. tan(2t) = -2Ixy/(Ixx-Iyy)
    # = 300000/125000 = 2.4, and 2.4 = 2(2/3)/(1-(2/3)^2), so tan t = 2/3
    # exactly: cos t = 3/sqrt(13), sin t = 2/sqrt(13), t = 33.690 deg.
    #   I11 = 270833.33 + 162500 = 1,300,000/3
    #   I22 = 270833.33 - 162500 =   325,000/3
    # A load along +y has components c1 = 2/sqrt(13) on e1 and
    # c2 = 3/sqrt(13) on e2; the e1 component is resisted by I22 and the e2
    # component by I11. Both midspan maxima coincide, so along the load
    #   v = (P L^3 / 48E) [ c1^2/I22 + c2^2/I11 ]
    #     = (312500/3) [ (4/13)(3/325000) + (9/13)(3/1300000) ]
    #     = (312500/3)(3/676000) = 312500/676000 mm
    #     = 0.462278...  (against 0.3125 for the old P L^3/(48 E Ixx))
    v_y_exp = 312500 / 676000
    # ...and the out-of-plane component, which the symmetric model had no
    # way to express at all:
    #   v_t = P (L^3/48E)(6/13)(1/I22 - 1/I11) = 0.332840... mm
    v_y_transverse_exp = 1000 * (L**3 / (48 * MATERIAL.E)) * (6 / 13) * (
        3 / 325_000 - 3 / 1_300_000
    )

    res_y = analyze_beam(
        section, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P, axis=LoadAxis.Y)]
    )
    checks += [
        Check("L-angle Y-axis: load_axis reported as 'y'", 1.0, 1.0 if res_y.load_axis == "y" else 0.0, 1e-9),
        Check("L-angle Y-axis: |M_max|", m_exp, abs(res_y.max_moment), 1e-6),
        Check("L-angle Y: stress (unsymmetric theory, by hand)", stress_y_exp, res_y.max_bending_stress, 1e-3),
        Check("L-angle Y: |v_max| along the load (principal axes)", v_y_exp, abs(res_y.max_deflection), 1e-3),
        Check("L-angle Y: out-of-plane deflection", v_y_transverse_exp, abs(res_y.max_deflection_transverse), 1e-3),
        # ...and the peak fibre is the tip of the tall leg, not the extreme
        # fibre in the load direction that M/Zxx implicitly assumed.
        Check("L-angle Y: peak fibre x, from the centroid", -5.0, res_y.max_bending_stress_point[0], 1e-6),
        Check("L-angle Y: peak fibre y, from the centroid", 40.0, res_y.max_bending_stress_point[1], 1e-6),
        # The old M/Z answer must be demonstrably NOT what comes back.
        Check(
            "L-angle Y: stress is not the old M/Zxx value (34.6% low)",
            1.0,
            1.0 if abs(res_y.max_bending_stress - m_exp / zxx_exp) / (m_exp / zxx_exp) > 0.3 else 0.0,
            1e-9,
        ),
    ]

    # Same treatment about the other axis. With Mx = 0, My = M the linear
    # distribution is sigma = M (Ixx x - Ixy y) / D; evaluated at the six
    # vertices the worst is (35, -10) from the centroid at 30,500,000/3, so
    #   sigma = 250000 * (30,500,000/3) / (4.225e11/9) = 54.142... MPa
    # and, with c1 = 3/sqrt(13), c2 = -2/sqrt(13),
    #   v = (312500/3)[ (9/13)(3/325000) + (4/13)(3/1300000) ]
    #     = (312500/3)(6/845000) = 0.739645 mm
    stress_x_exp = 250_000 * (30_500_000 / 3) / (4.225e11 / 9)
    v_x_exp = (312500 / 3) * (6 / 845_000)

    res_x = analyze_beam(
        section, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P, axis=LoadAxis.X)]
    )
    checks += [
        Check("L-angle X-axis: load_axis reported as 'x'", 1.0, 1.0 if res_x.load_axis == "x" else 0.0, 1e-9),
        Check("L-angle X-axis: |M_max|", m_exp, abs(res_x.max_moment), 1e-6),
        Check("L-angle X: stress (unsymmetric theory, by hand)", stress_x_exp, res_x.max_bending_stress, 1e-3),
        Check("L-angle X: |v_max| along the load (principal axes)", v_x_exp, abs(res_x.max_deflection), 1e-3),
        Check("L-angle X: peak fibre x, from the centroid", 35.0, res_x.max_bending_stress_point[0], 1e-6),
        Check("L-angle X: peak fibre y, from the centroid", -10.0, res_x.max_bending_stress_point[1], 1e-6),
    ]

    # Principal axes themselves, hand-derived above.
    checks += [
        Check("L-angle: principal angle = atan(2/3)", math.degrees(math.atan(2 / 3)), res_y.principal_angle_deg, 1e-6),
        Check("L-angle: I11 = 1,300,000/3", 1_300_000 / 3, res_y.i11, 1e-4),
        Check("L-angle: I22 = 325,000/3", 325_000 / 3, res_y.i22, 1e-4),
        Check("L-angle: asymmetry = |Ixy|/sqrt(Ixx*Iyy)", 150_000 / math.sqrt(ixx_exp * iyy_exp), res_y.asymmetry, 1e-4),
        # I11 + I22 must equal Ixx + Iyy -- the trace is rotation-invariant.
        Check("L-angle: I11 + I22 == Ixx + Iyy (invariant)", ixx_exp + iyy_exp, res_y.i11 + res_y.i22, 1e-9),
    ]

    # A symmetric section must be left exactly as it was: no out-of-plane
    # response, and the classical P L^3 / (48 E I) deflection.
    rect = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], MATERIAL, mesh_size=1.0)
    res_sym = analyze_beam(
        rect, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P, axis=LoadAxis.Y)]
    )
    checks += [
        Check("Symmetric rectangle: asymmetry is zero", 0.0, res_sym.asymmetry, 1e-12),
        Check("Symmetric rectangle: no out-of-plane deflection", 0.0, res_sym.max_deflection_transverse, 1e-12),
        Check(
            "Symmetric rectangle: deflection is still P L^3/(48 E Ixx)",
            abs(P) * L**3 / (48 * MATERIAL.E * (50.0 * 100.0**3 / 12)),
            abs(res_sym.max_deflection),
            1e-3,
        ),
        Check(
            "Symmetric rectangle: stress is still M/Z",
            (abs(P) * L / 4) / (50.0 * 100.0**2 / 6),
            res_sym.max_bending_stress,
            1e-3,
        ),
    ]

    # The whole point of the axis selector: X and Y must give genuinely
    # different physics for this asymmetric section, not the same answer
    # twice under a different label.
    checks.append(
        Check(
            "L-angle: X-axis and Y-axis results actually differ",
            1.0,
            1.0 if (res_x.max_bending_stress != res_y.max_bending_stress and abs(res_x.max_deflection) != abs(res_y.max_deflection)) else 0.0,
            1e-9,
        )
    )

    # Mixed-axis loads in one call must be rejected, not silently combined.
    try:
        analyze_beam(
            section,
            MATERIAL,
            L,
            BoundaryCondition.SIMPLY_SUPPORTED,
            [PointLoad(0.3, P, axis=LoadAxis.X), PointLoad(0.7, P, axis=LoadAxis.Y)],
        )
        mixed_rejected = False
    except ValueError:
        mixed_rejected = True
    checks.append(Check("L-angle: mixed X/Y point loads raise ValueError", 1.0, 1.0 if mixed_rejected else 0.0, 1e-9))

    return checks


def run_buckling_checks() -> list[Check]:
    section = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], MATERIAL, mesh_size=1.0)
    checks: list[Check] = []

    pcr = {}
    for bc in BoundaryCondition:
        res = analyze_beam(section, MATERIAL, L, bc, [PointLoad(0.5, P)])
        pcr[bc] = res.euler_buckling_load
        checks.append(Check(f"K factor: {bc.value}", K_FACTOR[bc], res.effective_length_factor, 1e-9))

    baseline = pcr[BoundaryCondition.SIMPLY_SUPPORTED]  # K=1
    for bc, expected_ratio in [
        (BoundaryCondition.FIXED_FIXED, 4.0),  # (1/0.5)^2
        (BoundaryCondition.FIXED_FREE, 0.25),  # (1/2.0)^2
        (BoundaryCondition.FIXED_PINNED, (1 / K_FACTOR[BoundaryCondition.FIXED_PINNED]) ** 2),
    ]:
        actual_ratio = pcr[bc] / baseline
        checks.append(Check(f"Pcr ratio vs simply-supported: {bc.value}", expected_ratio, actual_ratio, 1e-6))

    # ABSOLUTE Pcr, not just the K ratio. The ratios above divide I out
    # entirely, so on their own they say nothing about whether the right
    # second moment of area went in. Rectangle: Iyy = 100*50^3/12 is the
    # weak axis and Ixy = 0, so min(Ixx, Iyy) and the principal minimum
    # coincide, and Pcr = pi^2 E I / L^2 is exact.
    iyy_rect = 100.0 * 50.0**3 / 12.0
    checks.append(
        Check(
            "Pcr absolute, symmetric rectangle (pi^2 E Iyy / L^2)",
            math.pi**2 * MATERIAL.E * iyy_rect / L**2,
            baseline,
            1e-3,
        )
    )

    return checks


def run_principal_axis_buckling_checks() -> list[Check]:
    """A column buckles about its weak PRINCIPAL axis, which is not
    min(Ixx, Iyy) unless the centroidal axes are already principal.

    The L-angle from `run_asymmetric_axis_checks` has Ixy != 0, so this is
    a case where the two genuinely differ. Expected values come from the
    same composite-rectangle hand calc used there, extended to the product
    of area (Ixy_own = 0 for an axis-parallel rectangle, so each leg
    contributes only its A*dx*dy parallel-axis term), then through Mohr's
    circle -- all by hand, independent of what the code does:

      R_a (vertical leg):   A=600, dx = 5-15 = -10, dy = 30-20 = +10
      R_b (horizontal leg): A=400, dx = 30-15 = +15, dy = 5-20 = -15
      Ixy = 600*(-10)(+10) + 400*(+15)(-15) = -60,000 - 90,000 = -150,000

      I_1,2 = (Ixx+Iyy)/2 -/+ sqrt(((Ixx-Iyy)/2)^2 + Ixy^2)
            = 270,833.33 -/+ sqrt(62,500^2 + 150,000^2)
            = 270,833.33 -/+ 162,500
      => I_22 (weak) = 108,333.33 mm^4, against min(Ixx, Iyy) = 208,333.33

    So using min(Ixx, Iyy) would overstate Pcr by 1.923x here. The check
    is on the ABSOLUTE Pcr, and it is written to fail if that happens.
    """
    vertices = [(0, 0), (50, 0), (50, 10), (10, 10), (10, 60), (0, 60)]
    section = analyze_section(vertices, MATERIAL, mesh_size=0.5)
    checks: list[Check] = []

    ixy_exp = -150_000.0
    i22_exp = 625_000 / 6 + 1_000_000 / 6 - 162_500.0  # = 108,333.33
    i_min_naive = 625_000 / 3  # min(Ixx, Iyy) -- the WRONG answer

    checks.append(Check("L-angle: Ixy (composite hand calc)", ixy_exp, section.ixy, 1e-3))

    res = analyze_beam(
        section, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P)]
    )
    pcr_exp = math.pi**2 * MATERIAL.E * i22_exp / L**2
    pcr_naive = math.pi**2 * MATERIAL.E * i_min_naive / L**2
    checks += [
        Check("L-angle: Pcr uses the weak PRINCIPAL axis I22", pcr_exp, res.euler_buckling_load, 2e-3),
        # ...and is demonstrably NOT the min(Ixx, Iyy) answer, which is 1.92x higher
        Check(
            "L-angle: Pcr is not the min(Ixx,Iyy) value",
            1.0,
            1.0 if abs(res.euler_buckling_load - pcr_naive) / pcr_naive > 0.4 else 0.0,
            1e-9,
        ),
    ]

    # A doubly symmetric section must be completely unaffected by the
    # principal-axis treatment: Ixy = 0 makes the two identical.
    rect = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], MATERIAL, mesh_size=1.0)
    res_rect = analyze_beam(
        rect, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P)]
    )
    checks.append(
        Check(
            "Symmetric rectangle: principal I == min(Ixx, Iyy), Pcr unchanged",
            math.pi**2 * MATERIAL.E * min(rect.ixx, rect.iyy) / L**2,
            res_rect.euler_buckling_load,
            1e-9,
        )
    )

    return checks


def main() -> int:
    checks = (
        run_bc_checks()
        + run_asymmetric_axis_checks()
        + run_buckling_checks()
        + run_principal_axis_buckling_checks()
    )

    width = max(len(c.label) for c in checks) + 2
    header = f"{'Check':<{width}}{'Expected':>18}{'Computed':>18}{'Rel. err':>12}  Status"
    print(header)
    print("-" * len(header))
    all_passed = True
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        print(f"{c.label:<{width}}{c.expected:>18,.6g}{c.actual:>18,.6g}{c.rel_error * 100:>11.6f}%  {status}")

    print()
    print("All checks within tolerance." if all_passed else "FAILURES DETECTED — see rows marked FAIL above.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
