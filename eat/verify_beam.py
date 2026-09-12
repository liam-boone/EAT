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

from eat.beam import BoundaryCondition, K_FACTOR, PointLoad, analyze_beam
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
    checks = run_bc_checks() + run_buckling_checks()

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
