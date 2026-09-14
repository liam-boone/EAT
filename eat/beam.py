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

Euler buckling load (Pcr = pi^2 * E * I_min / (K*L)^2) is a column-capacity
property independent of the transverse point loads above; a buckling safety
factor is only meaningful against an axial COMPRESSIVE load, so `axial_load`
is a separate, optional input, positive in compression (see UNITS.md). Under
tension the check is suppressed rather than reported as a negative number.
I_min is the least PRINCIPAL second moment of area, not min(Ixx, Iyy) -- see
`_minimum_principal_i` for why the two differ on any profile whose
centroidal axes aren't principal.

UNSYMMETRIC BENDING
-------------------
Bending is solved in full generality, not as M/Z about a sketch axis. For a
section whose centroidal axes are not principal (Ixy != 0 -- an angle, a Z,
or simply a profile drawn at an angle on the canvas) a transverse load bends
it about an axis that is NOT the one the load is perpendicular to, so the
beam deflects sideways as well as along the load, and the peak stress is
higher than M/Z reports. Measured on a 50x60x10 L-angle, M/Zxx understated
the peak fibre stress by 34.6% (30.00 against 40.38 MPa), turning a real
safety factor of 6.19 into a reported 8.33.

Two standard formulations are used, each where it is the natural one:

* STRESS, directly from the centroidal moments (Boresi/Gere; the general
  linear stress distribution with no axial force):

      sigma(x, y) = [ (My*Ixx - Mx*Ixy) x + (Mx*Iyy - My*Ixy) y ]
                    / (Ixx*Iyy - Ixy^2)

  which reduces to Mx*y/Ixx when Ixy = 0. Since sigma is linear over the
  section, its extreme value is attained at an extreme point of the
  section's convex hull -- so evaluating it at `SectionResult.hull` finds
  the true peak exactly, rather than sampling for it.

* DEFLECTION, by resolving onto the PRINCIPAL axes. The load is split into
  components along the two principal directions, each component is solved
  by the same closed-form single-load solvers used for the symmetric case
  (with EI = E*I11 and E*I22 respectively), and the two deflection curves
  are recombined into the global frame. This reuses the exact machinery
  already verified against an independent direct-stiffness FEM rather than
  introducing a second, parallel beam solution.

`max_deflection` and `deflection_diagram` remain the component ALONG THE
LOAD, which is what the deflection chart plots and what the symmetric case
always meant. The out-of-plane component is reported alongside them
(`deflection_diagram_transverse`, `max_deflection_transverse`) and the
resultant magnitude as `max_deflection_resultant`; on a symmetric section
the transverse component is identically zero.
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


# How each "there is no number" state reads, so the two distinct reasons
# never collapse into the same "n/a" the way a bare null did.
_SAFETY_NOTES = {
    "ok": lambda v: f"{v}",
    "no_yield_strength": lambda v: "n/a — this material has no yield strength on file",
    "no_stress": lambda v: "infinite — this load case produces no bending stress",
}
_BUCKLING_NOTES = {
    "compression": lambda v: f"{v}",
    "tension": lambda v: "n/a — the axial load is tensile, so the member cannot buckle",
    "no_axial": lambda v: "n/a — no axial load given",
}


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

    max_deflection: float  # mm, signed, component ALONG the load axis
    max_deflection_position: float  # mm

    effective_length_factor: float  # K
    euler_buckling_load: float  # N
    axial_load: float | None  # N, positive in compression (see UNITS.md)
    buckling_safety_factor: float | None  # euler_buckling_load / axial_load

    # Why there is no buckling safety factor, when there isn't one. A bare
    # null read identically whether no axial load was given or the load was
    # tensile, and a tensile load used to produce a negative "safety
    # factor", which is meaningless -- a member in tension cannot buckle.
    #   "compression" : a factor is reported
    #   "tension"     : suppressed; buckling does not apply
    #   "no_axial"    : no axial load given
    buckling_status: str

    # Likewise for bending: "ok" (a factor is reported), "no_yield_strength"
    # (the material has none, so the factor is undefined), or "no_stress"
    # (the load case produces zero bending moment, so the factor is
    # infinite). The last two both used to surface as a bare null.
    safety_factor_status: str

    # --- unsymmetric bending (see the module docstring) ---
    # The out-of-plane response. Identically zero on a section whose
    # centroidal axes are principal (Ixy = 0), which is every singly or
    # doubly symmetric profile drawn square to the sketch axes.
    max_deflection_transverse: float  # mm, signed, perpendicular to the load
    max_deflection_resultant: float  # mm, magnitude of the vector sum
    max_deflection_resultant_position: float  # mm
    # Where on the cross-section the peak fibre stress acts, relative to
    # the centroid (mm). Under unsymmetric bending this is not simply the
    # extreme fibre in the load direction.
    max_bending_stress_point: tuple[float, float]
    principal_angle_deg: float  # rotation from the sketch axes to axis 1
    i11: float  # mm^4, major principal second moment
    i22: float  # mm^4, minor principal second moment
    # |Ixy| / sqrt(Ixx*Iyy): 0 when the sketch axes are already principal,
    # approaching 1 as the section gets more skewed. A quick read on how
    # far the unsymmetric treatment is from the symmetric one.
    asymmetry: float

    # Sampled curves for charting (e.g. the frontend's SVG plots), all
    # evaluated from the same exact closed-form functions as the max-value
    # fields above -- not a separate/approximate computation. diagram_x is
    # shared across the other parallel arrays.
    diagram_x: list[float]  # mm
    moment_diagram: list[float]  # N.mm, same length as diagram_x
    bending_stress_diagram: list[float]  # MPa (magnitude), same length as diagram_x
    deflection_diagram: list[float]  # mm, along the load axis
    deflection_diagram_transverse: list[float]  # mm, perpendicular to it

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
            f"Max bending stress  = {self.max_bending_stress:,.6g} MPa  at x = {self.max_bending_stress_position:,.4f} mm"
            f"  (fibre at {self.max_bending_stress_point[0]:,.2f}, {self.max_bending_stress_point[1]:,.2f} mm from the centroid)",
            f"Safety factor       = {_SAFETY_NOTES[self.safety_factor_status](self.safety_factor)}",
            f"Max deflection      = {self.max_deflection:,.6g} mm  at x = {self.max_deflection_position:,.4f} mm",
        ]
        if self.asymmetry > 1e-9:
            lines += [
                f"  ...transverse     = {self.max_deflection_transverse:,.6g} mm (out of the load plane)",
                f"  ...resultant      = {self.max_deflection_resultant:,.6g} mm  at x = {self.max_deflection_resultant_position:,.4f} mm",
                f"Principal axes      = {self.principal_angle_deg:,.2f} deg from the sketch axes; "
                f"I11 = {self.i11:,.6g}, I22 = {self.i22:,.6g} mm^4 (asymmetry {self.asymmetry:.3f})",
            ]
        lines += [
            f"Effective length K  = {self.effective_length_factor}",
            f"Euler buckling load = {self.euler_buckling_load:,.6g} N",
            f"Axial load (Z, along length, +ve compression) = "
            + (f"{self.axial_load:,.6g} N" if self.axial_load is not None else "n/a"),
            f"Buckling safety fac.= {_BUCKLING_NOTES[self.buckling_status](self.buckling_safety_factor)}",
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


def principal_axes(section: SectionResult) -> tuple[float, float, float]:
    """The section's principal second moments and their orientation.

    Returns `(i11, i22, theta)` with i11 >= i22 and theta the angle in
    RADIANS from the sketch x-axis to the first principal axis, positive
    counter-clockwise.

    Rotating the centroidal axes by theta gives

        I_x'x' = (Ixx+Iyy)/2 + (Ixx-Iyy)/2 cos(2t) - Ixy sin(2t)
        I_x'y' = (Ixx-Iyy)/2 sin(2t) + Ixy cos(2t)

    and the principal directions are where the product of area vanishes,
    I_x'y' = 0, i.e. tan(2t) = -2 Ixy / (Ixx - Iyy). `atan2` is used rather
    than `atan` so the quadrant is right when Ixx < Iyy, and it degenerates
    safely to theta = 0 for an already-principal section (Ixy = 0 and
    Ixx >= Iyy), leaving symmetric profiles on exactly the axes they were
    drawn on.
    """
    average = (section.ixx + section.iyy) / 2.0
    radius = math.hypot((section.ixx - section.iyy) / 2.0, section.ixy)
    i11, i22 = average + radius, average - radius
    theta = 0.5 * math.atan2(-2.0 * section.ixy, section.ixx - section.iyy)
    return i11, i22, theta


def bending_stress_at(
    section: SectionResult, moment_x: float, moment_y: float, x: float, y: float
) -> float:
    """Fibre stress at a centroid-relative point under unsymmetric bending.

    The general no-axial-force linear distribution; see the module
    docstring. `moment_x` acts about the centroidal x-axis, `moment_y`
    about the centroidal y-axis, both N.mm; `x`/`y` are measured from the
    centroid, mm. Reduces to the familiar M*y/Ixx when Ixy = 0.
    """
    denom = section.ixx * section.iyy - section.ixy**2
    if denom <= 0:
        # Degenerate section (a real area always has Ixx*Iyy > Ixy^2 by
        # Cauchy-Schwarz); fall back to the symmetric form rather than
        # dividing by ~zero.
        return moment_x * y / section.ixx if section.ixx > 0 else 0.0
    return (
        (moment_y * section.ixx - moment_x * section.ixy) * x
        + (moment_x * section.iyy - moment_y * section.ixy) * y
    ) / denom


def _peak_fibre(section: SectionResult, moment_x: float, moment_y: float):
    """Worst-magnitude fibre stress over the section, and where it acts.

    Exact, not sampled: the stress is linear in (x, y), so its extreme
    over the material is attained at an extreme point of the convex hull
    of the outer boundary (see `SectionResult.hull`)."""
    hull = section.hull or []
    if not hull:
        # Pre-hull history entry replayed through a fresh analysis; fall
        # back to the section moduli, which is the old behaviour.
        z = min(section.zxx_plus, section.zxx_minus) if moment_y == 0 else min(
            section.zyy_plus, section.zyy_minus
        )
        m = moment_x if moment_y == 0 else moment_y
        return (abs(m) / z if z else 0.0), (0.0, 0.0)
    best_value, best_point = 0.0, hull[0]
    for px, py in hull:
        value = abs(bending_stress_at(section, moment_x, moment_y, px, py))
        if value >= best_value:
            best_value, best_point = value, (px, py)
    return best_value, best_point


def _minimum_principal_i(section: SectionResult) -> float:
    """The section's LEAST principal second moment of area, mm^4.

    A column buckles about its weak PRINCIPAL axis, which is only the
    weaker of Ixx/Iyy when the centroidal axes happen to be principal
    (Ixy = 0) -- true for any singly or doubly symmetric profile, and
    false for an angle, a Z, or any profile that simply isn't drawn
    square to the sketch axes. Mohr's circle gives the principal values
    exactly from the three centroidal moments:

        I_1,2 = (Ixx + Iyy)/2 +/- sqrt( ((Ixx - Iyy)/2)^2 + Ixy^2 )

    and the minus root is the weak axis. This reduces exactly to
    min(Ixx, Iyy) when Ixy = 0, so symmetric sections are unaffected;
    on a 50x60x10 L-angle min(Ixx, Iyy) overstates it by 1.92x, which
    would have overstated Pcr by the same factor.
    """
    average = (section.ixx + section.iyy) / 2.0
    radius = math.hypot((section.ixx - section.iyy) / 2.0, section.ixy)
    return average - radius


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
    sets the direction the load acts in, which is then resolved onto the
    section's principal axes (see the module docstring on unsymmetric
    bending). `axial_load`, if given, is independent of this choice: it
    always acts along the section's long axis (Z, the beam's length
    direction), positive in compression.
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

    # --- resolve the load onto the section's principal axes -------------
    # A transverse load bends the section about its principal axes, not
    # about the axes it happens to have been drawn on. Splitting the load
    # into principal components lets each component be solved by the same
    # closed-form solvers the symmetric case already uses, then recombined.
    i11, i22, theta = principal_axes(section)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    e1 = (cos_t, sin_t)  # first principal direction, in sketch coords
    e2 = (-sin_t, cos_t)  # second

    load_dir = (1.0, 0.0) if load_axis == LoadAxis.X else (0.0, 1.0)
    c1 = load_dir[0] * e1[0] + load_dir[1] * e1[1]  # share of the load along e1
    c2 = load_dir[0] * e2[0] + load_dir[1] * e2[1]  # ...and along e2
    # A load along e1 bends the section in the e1-z plane, which is
    # resisted by the second moment about the OTHER principal axis.
    EI_1 = material.E * i22
    EI_2 = material.E * i11

    total_R_left = total_M_left = total_R_right = total_M_right = 0.0
    m_funcs = []
    v1_funcs = []  # deflection along e1
    v2_funcs = []  # deflection along e2
    load_positions = []

    for load in point_loads:
        if not 0.0 <= load.position_fraction <= 1.0:
            raise ValueError(
                f"point load position_fraction must be in [0, 1], got {load.position_fraction}"
            )
        a = load.position_fraction * length
        load_positions.append(a)
        # Reactions and the internal moment come from the FULL load -- for a
        # prismatic beam they don't depend on EI at all (the redundants in
        # the indeterminate cases cancel it), so this solve's EI is
        # immaterial and only its statics are used.
        R_l, M_l, R_r, M_r, m_of_x, _ = solver(load.magnitude, a, length, EI_2)
        total_R_left += R_l
        total_M_left += M_l
        total_R_right += R_r
        total_M_right += M_r
        m_funcs.append(m_of_x)
        # ...while each principal component gets its own EI.
        v1_funcs.append(solver(load.magnitude * c1, a, length, EI_1)[5])
        v2_funcs.append(solver(load.magnitude * c2, a, length, EI_2)[5])

    def total_m(x):
        return sum(f(x) for f in m_funcs)

    def _deflection_components(x):
        """(along the load, perpendicular to it), both in mm."""
        u1 = sum(f(x) for f in v1_funcs)
        u2 = sum(f(x) for f in v2_funcs)
        # back into sketch coordinates, then project onto the load axis
        ux = u1 * e1[0] + u2 * e2[0]
        uy = u1 * e1[1] + u2 * e2[1]
        if load_axis == LoadAxis.X:
            return ux, uy
        return uy, -ux

    def total_v(x):
        return _deflection_components(x)[0]

    def total_v_transverse(x):
        return _deflection_components(x)[1]

    # Moment diagram is piecewise linear -> its extremum is exactly at a
    # support or a load position; no search needed.
    candidate_x = np.array(sorted({0.0, length, *load_positions}))
    m_candidates = total_m(candidate_x)
    peak_idx = int(np.argmax(np.abs(m_candidates)))
    max_moment = float(m_candidates[peak_idx])
    max_moment_position = float(candidate_x[peak_idx])

    # Peak fibre stress per unit bending moment. The stress distribution is
    # linear in the moment, so one scan of the section's convex hull gives
    # both the worst fibre's location and a factor that turns any M into
    # the peak stress at that station -- exactly, for the whole diagram.
    unit_mx, unit_my = (0.0, 1.0) if load_axis == LoadAxis.X else (1.0, 0.0)
    stress_per_moment, max_bending_stress_point = _peak_fibre(section, unit_mx, unit_my)
    max_bending_stress = abs(max_moment) * stress_per_moment
    max_bending_stress_position = max_moment_position

    if material.yield_strength is None:
        safety_factor, safety_factor_status = None, "no_yield_strength"
    elif max_bending_stress == 0:
        safety_factor, safety_factor_status = float("inf"), "no_stress"
    else:
        safety_factor = material.yield_strength / max_bending_stress
        safety_factor_status = "ok"

    # Deflection is piecewise cubic/quartic -> locate its extremum by a
    # dense grid search over the exact closed-form curve, refined locally.
    x_grid = np.union1d(np.linspace(0.0, length, deflection_grid_points), np.array(load_positions))
    v_grid, v_trans_grid = _deflection_components(x_grid)
    max_deflection_position, max_deflection = _refine_extremum(x_grid, v_grid, total_v, length)
    # Quote the out-of-plane movement at the same station, so the pair
    # describes one cross-section rather than two unrelated maxima.
    max_deflection_transverse = float(
        np.asarray(total_v_transverse(np.array([max_deflection_position])))[0]
    )
    # The resultant has its own worst station, which need not be either.
    def _resultant(x):
        along, across = _deflection_components(x)
        return np.hypot(along, across)

    max_deflection_resultant_position, max_deflection_resultant = _refine_extremum(
        x_grid, _resultant(x_grid), _resultant, length
    )

    denom = section.ixx * section.iyy
    asymmetry = abs(section.ixy) / math.sqrt(denom) if denom > 0 else 0.0

    I_min = _minimum_principal_i(section)
    K = K_FACTOR[bc]
    euler_buckling_load = math.pi**2 * material.E * I_min / (K * length) ** 2
    # Buckling is a COMPRESSION failure mode. A tensile axial load used to
    # divide through anyway and report a negative "safety factor"; a member
    # in tension cannot buckle, so the check is suppressed and says why.
    if not axial_load:
        buckling_safety_factor, buckling_status = None, "no_axial"
    elif axial_load < 0:
        buckling_safety_factor, buckling_status = None, "tension"
    else:
        buckling_safety_factor = euler_buckling_load / axial_load
        buckling_status = "compression"

    label_left, label_right = _REACTION_LABELS[bc]
    reactions = [
        Reaction(label_left, 0.0, total_R_left, total_M_left),
        Reaction(label_right, length, total_R_right, total_M_right),
    ]

    diagram_x = np.union1d(np.linspace(0.0, length, diagram_points), np.array(load_positions))
    diagram_m = total_m(diagram_x)
    diagram_stress = np.abs(diagram_m) * stress_per_moment
    diagram_v, diagram_v_transverse = _deflection_components(diagram_x)

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
        safety_factor_status=safety_factor_status,
        max_deflection=max_deflection,
        max_deflection_position=max_deflection_position,
        effective_length_factor=K,
        euler_buckling_load=euler_buckling_load,
        axial_load=axial_load,
        buckling_safety_factor=buckling_safety_factor,
        buckling_status=buckling_status,
        max_deflection_transverse=max_deflection_transverse,
        max_deflection_resultant=max_deflection_resultant,
        max_deflection_resultant_position=max_deflection_resultant_position,
        max_bending_stress_point=(
            float(max_bending_stress_point[0]),
            float(max_bending_stress_point[1]),
        ),
        principal_angle_deg=math.degrees(theta),
        i11=i11,
        i22=i22,
        asymmetry=asymmetry,
        diagram_x=diagram_x.tolist(),
        moment_diagram=diagram_m.tolist(),
        bending_stress_diagram=diagram_stress.tolist(),
        deflection_diagram=diagram_v.tolist(),
        deflection_diagram_transverse=np.asarray(diagram_v_transverse).tolist(),
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
