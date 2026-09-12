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

    res_y = analyze_beam(
        section, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P, axis=LoadAxis.Y)]
    )
    stress_y_exp = m_exp / zxx_exp
    v_y_exp = abs(P) * L**3 / (48 * MATERIAL.E * ixx_exp)
    checks += [
        Check("L-angle Y-axis: load_axis reported as 'y'", 1.0, 1.0 if res_y.load_axis == "y" else 0.0, 1e-9),
        Check("L-angle Y-axis: |M_max|", m_exp, abs(res_y.max_moment), 1e-6),
        Check("L-angle Y-axis: max bending stress (bends about Ixx)", stress_y_exp, res_y.max_bending_stress, 1e-3),
        Check("L-angle Y-axis: |v_max|", v_y_exp, abs(res_y.max_deflection), 1e-3),
    ]

    res_x = analyze_beam(
        section, MATERIAL, L, BoundaryCondition.SIMPLY_SUPPORTED, [PointLoad(0.5, P, axis=LoadAxis.X)]
    )
    stress_x_exp = m_exp / zyy_exp
    v_x_exp = abs(P) * L**3 / (48 * MATERIAL.E * iyy_exp)
    checks += [
        Check("L-angle X-axis: load_axis reported as 'x'", 1.0, 1.0 if res_x.load_axis == "x" else 0.0, 1e-9),
        Check("L-angle X-axis: |M_max|", m_exp, abs(res_x.max_moment), 1e-6),
        Check("L-angle X-axis: max bending stress (bends about Iyy)", stress_x_exp, res_x.max_bending_stress, 1e-3),
        Check("L-angle X-axis: |v_max|", v_x_exp, abs(res_x.max_deflection), 1e-3),
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

    return checks


def main() -> int:
    checks = run_bc_checks() + run_asymmetric_axis_checks() + run_buckling_checks()

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
