"""
EAT beam engine — closed-form single-span beam analysis with point loads.

See UNITS.md for the unit system (mm-N-MPa) and the load direction
convention. Point loads act along either the section's local y-axis
(bending about the centroidal x-axis / Ixx) or its local x-axis (bending
about the centroidal y-axis / Iyy), selected per-analysis via `LoadAxis`
(default Y, matching pre-existing behaviour); loads are assumed to pass
through the shear centre so no torsion is induced. Axial load always acts
along the section's long axis (Z, the beam's length direction),
independent of the transverse load axis. Reported bending moment uses the
standard sagging-positive statics sign (positive under a simply supported
span's point load); reaction moments at fixed supports come out negative
(hogging). Deflection is reported positive in the direction of the applied
load, matching how beam-deflection tables are conventionally presented.

For each boundary condition, a single point load's reactions and internal
moment M(x) are exact closed-form results from statics (determinate cases)
or the moment-area / double-integration method (indeterminate cases:
fixed-fixed, fixed-pinned) — each checked against a known textbook special
case in eat/verify_beam.py. Deflection v(x) is the corresponding exact
closed-form curve from the same method. Multiple point loads are combined
by superposition, which is exact for linear beam theory.

Because a single span's moment diagram under point loads only is always
piecewise LINEAR, its extreme value (and hence the governing bending
stress) occurs exactly at a load position or a support — found directly,
with no search. The deflection curve is piecewise cubic/quartic, so its
extremum is located by evaluating the exact closed-form deflection
function (the superposition sum above) on a fine grid with a local
quadratic refinement — a numerical *search* over an already-exact
analytic function, not a numerical beam solver.

Euler buckling load (Pcr = pi^2 * E * I_min / (K*L)^2, weak-axis I) is a
column-capacity property independent of the transverse point loads above;
a buckling safety factor is only meaningful against an axial compressive
load, so `axial_load` is a separate, optional input.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

from eat.materials import get_material
from eat.section import Material, SectionResult, analyze_section


class BoundaryCondition(str, Enum):
    """x=0 / x=L reference ends are documented per case below."""

    FIXED_FIXED = "fixed_fixed"  # x=0 and x=L both fixed
    FIXED_FREE = "fixed_free"  # x=0 fixed, x=L free (cantilever)
    SIMPLY_SUPPORTED = "simply_supported"  # x=0 and x=L both pinned
    FIXED_PINNED = "fixed_pinned"  # x=0 fixed, x=L pinned


class LoadAxis(str, Enum):
    """Which section axis a transverse point load acts along.

    Y (default): load acts along the section's local y-axis, bending it
    about the centroidal x-axis (Ixx) -- the tool's original convention.
    X: load acts along the section's local x-axis, bending it about the
    centroidal y-axis (Iyy). All point loads in one `analyze_beam` call
    must share the same axis (mixed-axis/biaxial bending is out of scope
    for now); axial load is unaffected by this choice -- it always acts
    along the section's long axis (Z).
    """

    Y = "y"
    X = "x"


# Effective length factors for Euler buckling, Pcr = pi^2 EI / (K L)^2.
# Fixed-pinned's theoretical value is the root of tan(u) = u (u = 4.4934),
# K = pi / u = 0.6992..., commonly rounded to 0.7 in textbooks/AISC tables.
K_FACTOR: dict[BoundaryCondition, float] = {
    BoundaryCondition.FIXED_FIXED: 0.5,
    BoundaryCondition.FIXED_FREE: 2.0,
    BoundaryCondition.SIMPLY_SUPPORTED: 1.0,
    BoundaryCondition.FIXED_PINNED: 0.6992,
}


@dataclass
class PointLoad:
    """A transverse point load along a section axis (see `LoadAxis`).

    `position_fraction` is measured from the x=0 reference end for the
    selected boundary condition (see `BoundaryCondition`), 0 <= f <= 1.
    `magnitude` is signed, N, positive in the +axis direction.
    `axis` defaults to Y (the tool's original convention); all point loads
    passed to one `analyze_beam` call must share the same axis.
    """

    position_fraction: float
    magnitude: float
    axis: LoadAxis = LoadAxis.Y


@dataclass
class Reaction:
    label: str
    x: float  # mm
    force: float  # N
    moment: float  # N.mm


@dataclass
class BeamResult:
    boundary_condition: str
    length: float  # mm
    material: str
    reactions: list[Reaction]

    load_axis: str  # "x" or "y" -- which section axis the point loads bend about (see LoadAxis)

    max_moment: float  # N.mm, signed
    max_moment_position: float  # mm

    max_bending_stress: float  # MPa, magnitude (worst-case fibre)
    max_bending_stress_position: float  # mm

    safety_factor: float | None  # yield_strength / max_bending_stress

    max_deflection: float  # mm, signed
    max_deflection_position: float  # mm

    effective_length_factor: float  # K
    euler_buckling_load: float  # N
    axial_load: float | None  # N
    buckling_safety_factor: float | None  # euler_buckling_load / axial_load

    # Sampled curves for charting (e.g. the frontend's SVG plots), all
    # evaluated from the same exact closed-form functions as the max-value
    # fields above -- not a separate/approximate computation. diagram_x is
    # shared across the other three parallel arrays.
    diagram_x: list[float]  # mm
    moment_diagram: list[float]  # N.mm, same length as diagram_x
    bending_stress_diagram: list[float]  # MPa (magnitude), same length as diagram_x
    deflection_diagram: list[float]  # mm, same length as diagram_x

    def summary(self) -> str:
        axis_note = "Ixx (loads along local Y)" if self.load_axis == "y" else "Iyy (loads along local X)"
        lines = [
            f"Boundary condition : {self.boundary_condition}",
            f"Material           : {self.material}",
            f"Length             : {self.length:,.4f} mm",
            f"Load axis          : {self.load_axis.upper()}, bending about {axis_note}",
            "Reactions:",
        ]
        for r in self.reactions:
            lines.append(f"  {r.label:<24} force = {r.force:>14,.4f} N   moment = {r.moment:>16,.4f} N.mm")
        lines += [
            f"Max moment          = {self.max_moment:,.6g} N.mm  at x = {self.max_moment_position:,.4f} mm",
            f"Max bending stress  = {self.max_bending_stress:,.6g} MPa  at x = {self.max_bending_stress_position:,.4f} mm",
            f"Safety factor       = {self.safety_factor if self.safety_factor is not None else 'n/a (no yield strength)'}",
            f"Max deflection      = {self.max_deflection:,.6g} mm  at x = {self.max_deflection_position:,.4f} mm",
            f"Effective length K  = {self.effective_length_factor}",
            f"Euler buckling load = {self.euler_buckling_load:,.6g} N",
            f"Axial load (Z, along length) = "
            + (f"{self.axial_load:,.6g} N" if self.axial_load is not None else "n/a"),
            f"Buckling safety fac.= {self.buckling_safety_factor if self.buckling_safety_factor is not None else 'n/a (no axial load given)'}",
        ]
        return "\n".join(lines)


# --- Per-boundary-condition single-point-load closed-form solutions ---
#
# Each returns (R_left, M_left, R_right, M_right, m_of_x, v_of_x):
#   R_left/R_right : reaction forces at x=0 / x=L, N
#   M_left/M_right : reaction moments at x=0 / x=L, N.mm (0 where the
#                    support is a pin/roller/free end)
#   m_of_x(x)      : bending moment, N.mm, vectorized over a numpy array
#   v_of_x(x)      : deflection, mm, vectorized over a numpy array


def _simply_supported_single(P: float, a: float, L: float, EI: float):
    b = L - a
    R_A = P * b / L
    R_B = P * a / L

    def m_of_x(x):
        return np.where(x <= a, R_A * x, R_B * (L - x))

    def v_of_x(x):
        branch1 = P * b * x * (L**2 - b**2 - x**2) / (6 * L * EI)
        branch2 = P * a * (L - x) * (2 * L * x - a**2 - x**2) / (6 * L * EI)
        return np.where(x <= a, branch1, branch2)

    return R_A, 0.0, R_B, 0.0, m_of_x, v_of_x


def _fixed_free_single(P: float, a: float, L: float, EI: float):
    # Fixed at x=0, free at x=L.
    R_A = P
    M_A = -P * a  # hogging

    def m_of_x(x):
        return np.where(x <= a, -P * (a - x), 0.0)

    def v_of_x(x):
        branch1 = P * x**2 * (3 * a - x) / (6 * EI)
        branch2 = P * a**2 * (3 * x - a) / (6 * EI)
        return np.where(x <= a, branch1, branch2)

    return R_A, M_A, 0.0, 0.0, m_of_x, v_of_x


def _fixed_fixed_single(P: float, a: float, L: float, EI: float):
    b = L - a
    M_A = -P * a * b**2 / L**2
    M_B = -P * a**2 * b / L**2
    R_A = P * b**2 * (3 * a + b) / L**3
    R_B = P * a**2 * (3 * b + a) / L**3

    def m_of_x(x):
        return np.where(x <= a, M_A + R_A * x, M_B + R_B * (L - x))

    def v_of_x(x):
        branch1 = P * b**2 * x**2 * (3 * a * L - x * (3 * a + b)) / (6 * L**3 * EI)
        branch2 = P * a**2 * (L - x) ** 2 * (3 * b * L - (L - x) * (3 * b + a)) / (6 * L**3 * EI)
        return np.where(x <= a, branch1, branch2)

    return R_A, M_A, R_B, M_B, m_of_x, v_of_x


def _fixed_pinned_single(P: float, a: float, L: float, EI: float):
    # Fixed at x=0, pinned at x=L. Redundant M_A found from the
    # compatibility condition (zero slope at the fixed end), then v(x) by
    # direct double integration of EI*v'' = -M(x) with continuity at x=a
    # and the fixed-end conditions v(0)=v'(0)=0 (v(L)=0 then follows
    # automatically and is checked in eat/verify_beam.py).
    b = L - a
    M_A_mag = P * b * (L**2 - b**2) / (2 * L**2)  # positive magnitude, hogging
    M_A = -M_A_mag
    R_B = P * a**2 * (3 * L - a) / (2 * L**3)
    R_A = P - R_B

    def m_of_x(x):
        return np.where(x <= a, M_A + R_A * x, R_B * (L - x))

    Va = M_A_mag * a**2 / 2 - R_A * a**3 / 6  # EI * v(a), region 1
    Sa = M_A_mag * a - R_A * a**2 / 2  # EI * v'(a), region 1
    C3 = Sa + R_B * (L * a - a**2 / 2)
    C4 = Va + R_B * (L * a**2 / 2 - a**3 / 6) - C3 * a

    def v_of_x(x):
        branch1 = (M_A_mag * x**2 / 2 - R_A * x**3 / 6) / EI
        branch2 = (-R_B * (L * x**2 / 2 - x**3 / 6) + C3 * x + C4) / EI
        return np.where(x <= a, branch1, branch2)

    return R_A, M_A, R_B, 0.0, m_of_x, v_of_x


_SINGLE_LOAD_SOLVERS = {
    BoundaryCondition.SIMPLY_SUPPORTED: _simply_supported_single,
    BoundaryCondition.FIXED_FREE: _fixed_free_single,
    BoundaryCondition.FIXED_FIXED: _fixed_fixed_single,
    BoundaryCondition.FIXED_PINNED: _fixed_pinned_single,
}

_REACTION_LABELS = {
    BoundaryCondition.SIMPLY_SUPPORTED: ("Pin A (x=0)", "Pin B (x=L)"),
    BoundaryCondition.FIXED_FREE: ("Fixed support (x=0)", "Free end (x=L)"),
    BoundaryCondition.FIXED_FIXED: ("Fixed support A (x=0)", "Fixed support B (x=L)"),
    BoundaryCondition.FIXED_PINNED: ("Fixed support (x=0)", "Pinned support (x=L)"),
}


def _refine_extremum(x_grid: np.ndarray, y_grid: np.ndarray, v_of_x, L: float):
    """Refine a grid-located extremum with a local quadratic fit, then
    re-evaluate the exact function at the refined location."""
    idx = int(np.argmax(np.abs(y_grid)))
    if idx == 0 or idx == len(x_grid) - 1:
        return float(x_grid[idx]), float(y_grid[idx])

    x0, x1, x2 = x_grid[idx - 1], x_grid[idx], x_grid[idx + 1]
    y0, y1, y2 = y_grid[idx - 1], y_grid[idx], y_grid[idx + 1]
    denom = (x0 - x1) * (x0 - x2) * (x1 - x2)
    if abs(denom) < 1e-12:
        return float(x1), float(y1)

    a_coef = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / denom
    b_coef = (x2**2 * (y0 - y1) + x1**2 * (y2 - y0) + x0**2 * (y1 - y2)) / denom
    if abs(a_coef) < 1e-14:
        return float(x1), float(y1)

    x_star = -b_coef / (2 * a_coef)
    x_star = min(max(x_star, 0.0), L)
    y_star = float(np.asarray(v_of_x(np.array([x_star])))[0])
    return float(x_star), y_star


def analyze_beam(
    section: SectionResult,
    material: Material,
    length: float,
    boundary_condition: BoundaryCondition | str,
    point_loads: list[PointLoad],
    axial_load: float | None = None,
    deflection_grid_points: int = 4001,
    diagram_points: int = 121,
) -> BeamResult:
    """Analyze a single-span beam under transverse point loads.

    `section` and `material` are typically `eat.section.analyze_section`'s
    result and the `Material` passed to it. See the module docstring and
    UNITS.md for the unit system and sign/direction conventions.

    All `point_loads` must share one `axis` (see `LoadAxis`); that choice
    selects which section moment of inertia (Ixx or Iyy) and section
    modulus (Zxx or Zyy) governs bending -- the closed-form solvers below
    are unchanged either way, only EI and Z are swapped. `axial_load`, if
    given, is independent of this choice: it always acts along the
    section's long axis (Z, the beam's length direction).
    """
    bc = BoundaryCondition(boundary_condition)
    if length <= 0:
        raise ValueError("length must be positive")
    if not point_loads:
        raise ValueError("at least one point load is required")

    load_axes = {LoadAxis(load.axis) for load in point_loads}
    if len(load_axes) > 1:
        raise ValueError(
            "All point loads in one analysis must share the same axis "
            "(mixed X/Y point loads are not yet supported)."
        )
    load_axis = load_axes.pop()

    solver = _SINGLE_LOAD_SOLVERS[bc]
    if load_axis == LoadAxis.X:
        I_bend = section.iyy
        z_plus, z_minus = section.zyy_plus, section.zyy_minus
    else:
        I_bend = section.ixx
        z_plus, z_minus = section.zxx_plus, section.zxx_minus
    EI = material.E * I_bend

    total_R_left = total_M_left = total_R_right = total_M_right = 0.0
    m_funcs = []
    v_funcs = []
    load_positions = []

    for load in point_loads:
        if not 0.0 <= load.position_fraction <= 1.0:
            raise ValueError(
                f"point load position_fraction must be in [0, 1], got {load.position_fraction}"
            )
        a = load.position_fraction * length
        load_positions.append(a)
        R_l, M_l, R_r, M_r, m_of_x, v_of_x = solver(load.magnitude, a, length, EI)
        total_R_left += R_l
        total_M_left += M_l
        total_R_right += R_r
        total_M_right += M_r
        m_funcs.append(m_of_x)
        v_funcs.append(v_of_x)

    def total_m(x):
        return sum(f(x) for f in m_funcs)

    def total_v(x):
        return sum(f(x) for f in v_funcs)

    # Moment diagram is piecewise linear -> its extremum is exactly at a
    # support or a load position; no search needed.
    candidate_x = np.array(sorted({0.0, length, *load_positions}))
    m_candidates = total_m(candidate_x)
    peak_idx = int(np.argmax(np.abs(m_candidates)))
    max_moment = float(m_candidates[peak_idx])
    max_moment_position = float(candidate_x[peak_idx])

    z_worst = min(z_plus, z_minus)
    max_bending_stress = abs(max_moment) / z_worst
    max_bending_stress_position = max_moment_position

    safety_factor = None
    if material.yield_strength is not None:
        safety_factor = (
            float("inf") if max_bending_stress == 0 else material.yield_strength / max_bending_stress
        )

    # Deflection is piecewise cubic/quartic -> locate its extremum by a
    # dense grid search over the exact closed-form curve, refined locally.
    x_grid = np.union1d(np.linspace(0.0, length, deflection_grid_points), np.array(load_positions))
    v_grid = total_v(x_grid)
    max_deflection_position, max_deflection = _refine_extremum(x_grid, v_grid, total_v, length)

    I_min = min(section.ixx, section.iyy)
    K = K_FACTOR[bc]
    euler_buckling_load = math.pi**2 * material.E * I_min / (K * length) ** 2
    buckling_safety_factor = (
        euler_buckling_load / axial_load if axial_load else None
    )

    label_left, label_right = _REACTION_LABELS[bc]
    reactions = [
        Reaction(label_left, 0.0, total_R_left, total_M_left),
        Reaction(label_right, length, total_R_right, total_M_right),
    ]

    diagram_x = np.union1d(np.linspace(0.0, length, diagram_points), np.array(load_positions))
    diagram_m = total_m(diagram_x)
    diagram_stress = np.abs(diagram_m) / z_worst
    diagram_v = total_v(diagram_x)

    return BeamResult(
        boundary_condition=bc.value,
        length=length,
        material=material.name,
        reactions=reactions,
        load_axis=load_axis.value,
        max_moment=max_moment,
        max_moment_position=max_moment_position,
        max_bending_stress=max_bending_stress,
        max_bending_stress_position=max_bending_stress_position,
        safety_factor=safety_factor,
        max_deflection=max_deflection,
        max_deflection_position=max_deflection_position,
        effective_length_factor=K,
        euler_buckling_load=euler_buckling_load,
        axial_load=axial_load,
        buckling_safety_factor=buckling_safety_factor,
        diagram_x=diagram_x.tolist(),
        moment_diagram=diagram_m.tolist(),
        bending_stress_diagram=diagram_stress.tolist(),
        deflection_diagram=diagram_v.tolist(),
    )


def _example_json() -> dict:
    return {
        "material_name": "6061-T6 Aluminum (Extruded)",
        "section": {"vertices": [[0, 0], [50, 0], [50, 100], [0, 100]], "mesh_size": None},
        "length": 1000,
        "boundary_condition": "simply_supported",
        "point_loads": [{"position_fraction": 0.5, "magnitude": -500, "axis": "y"}],
        "axial_load": None,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eat.beam", description="Analyze a single-span beam with point loads."
    )
    parser.add_argument(
        "spec",
        nargs="?",
        help="Path to a JSON spec file. Omit to print an example spec.",
    )
    args = parser.parse_args(argv)

    if args.spec is None:
        print(json.dumps(_example_json(), indent=2))
        print("\nSave this as a file and pass it as an argument to analyze it.", file=sys.stderr)
        return 0

    data = json.loads(Path(args.spec).read_text())
    material = get_material(data["material_name"])
    sec_spec = data["section"]
    vertices = [tuple(p) for p in sec_spec["vertices"]]
    section = analyze_section(vertices, material, mesh_size=sec_spec.get("mesh_size"))
    point_loads = [PointLoad(**pl) for pl in data["point_loads"]]

    result = analyze_beam(
        section,
        material,
        length=data["length"],
        boundary_condition=data["boundary_condition"],
        point_loads=point_loads,
        axial_load=data.get("axial_load"),
    )
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
