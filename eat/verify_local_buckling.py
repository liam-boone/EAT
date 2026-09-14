"""
Verification harness for the local (plate) buckling engine.

This is the one part of EAT where a wrong METHOD, not a wrong line of
code, would be a real engineering error -- the tool would report a
confident safety factor computed from the wrong physics. So the method is
checked before the plumbing, against sources outside this project:

  1. THE BUCKLING COEFFICIENTS, FROM FIRST PRINCIPLES. k = 4.0 and
     k = 0.425 are not taken from a table here. The plate buckling
     eigenvalue problem is discretized and solved directly, and has to
     return them. See `run_eigenvalue_checks` for the derivation.

  2. THE CLOSED FORM, against the textbook constants. With nu = 0.3 the
     classical result for a long plate simply supported on both edges is
     sigma_cr = 3.615 E (t/b)^2, and 0.384 E (t/b)^2 for an outstand.

  3. THE CODE CLASSIFICATION, against a published worked example.
     Published limits for a 7A04-T6 aluminium SHS (a ~500 MPa alloy,
     essentially 7075-T6) are Class 1 at b/t <= 7.75, Class 2 <= 11.28,
     Class 3 <= 15.50. Running EN 1999-1-1 Table 6.2 through this module
     with the 7075-T6 entry from the project's own material list has to
     reproduce those three numbers.

  4. THE GEOMETRY, against sections whose plate widths are known by hand.
     A 60x40 tube with 4mm walls has mid-line widths of exactly 56 and 36
     and both edges of every wall supported; an L-angle's two legs are
     outstands of 45 and 55; an I-beam's web is an internal element 94
     deep and its four flange halves are outstands. Getting the EDGE
     CONDITION wrong is a factor of 9.4 on the answer, so each of these
     checks the classification as well as the width.

Run with: python -m eat.verify_local_buckling
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from eat.local_buckling import (
    EC9_INTERNAL_LIMITS,
    K_INTERNAL,
    K_OUTSTAND,
    analyze_local_buckling,
    ec9_class,
    elastic_critical_stress,
    epsilon,
)
from eat.materials import get_material
from eat.section import analyze_section

NU_TEXTBOOK = 0.3


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _close(actual: float, expected: float, tol: float) -> bool:
    return abs(actual - expected) <= tol * abs(expected)


def rounded_rect(x0, y0, x1, y1, radius, ccw=True, segments=16):
    points = []
    for cx, cy, start in (
        (x1 - radius, y0 + radius, -90),
        (x1 - radius, y1 - radius, 0),
        (x0 + radius, y1 - radius, 90),
        (x0 + radius, y0 + radius, 180),
    ):
        for k in range(segments + 1):
            angle = math.radians(start + 90 * k / segments)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points if ccw else points[::-1]


I_BEAM = [
    (0, 0), (60, 0), (60, 6), (32.5, 6), (32.5, 94), (60, 94),
    (60, 100), (0, 100), (0, 94), (27.5, 94), (27.5, 6), (0, 6),
]
L_ANGLE = [(0, 0), (50, 0), (50, 10), (10, 10), (10, 60), (0, 60)]


# --- 1. The buckling coefficients, from first principles --------------------


def _plate_k(free_edge: bool, n: int, ab: float, nu: float = NU_TEXTBOOK) -> float:
    """Smallest eigenvalue of the plate buckling problem, as k.

    For a long plate under uniform axial compression, take
    w(x, y) = f(y) sin(A x). The plate equation D grad^4 w + N_x w,xx = 0
    then reduces to the one-dimensional eigenvalue problem

        f'''' - 2 A^2 f'' + A^4 f = (N_x / D) A^2 f

    on 0 <= y <= b, with A the half-wave number along the load direction.
    Discretize f by finite differences and impose the edge conditions
    through ghost points:

      simply supported (y = 0):  f = 0,  f'' = 0
      free (y = b):              f'' = nu A^2 f       (M_y = 0)
                                 f''' = (2 - nu) A^2 f'  (V_y = 0)

    Then k = N_cr b^2 / (pi^2 D), minimized over A."""
    b = 1.0
    h = b / n
    A = ab * math.pi / b
    size = n if free_edge else n - 1
    M = np.zeros((size, size))

    if free_edge:
        # Ghost points past the free edge, as combinations of unknowns.
        g1 = {n: nu * A**2 * h**2 + 2.0, n - 1: -1.0}
        c = (2.0 - nu) * A**2 * h**2
        g2 = {n - 2: 1.0, n - 1: -2.0 - c}
        for key, value in g1.items():
            g2[key] = g2.get(key, 0.0) + (2.0 + c) * value

    last = n if free_edge else n - 1
    for i in range(1, last + 1):
        row = i - 1

        def add(j, coefficient):
            if j == 0 or (not free_edge and j == n):
                return  # f = 0 at a simple support
            if j == -1:  # f_{-1} = -f_1
                M[row, 0] += -coefficient
                return
            if not free_edge and j == n + 1:  # f_{n+1} = -f_{n-1}
                M[row, n - 2] += -coefficient
                return
            if free_edge and j == n + 1:
                for key, value in g1.items():
                    M[row, key - 1] += coefficient * value
                return
            if free_edge and j == n + 2:
                for key, value in g2.items():
                    M[row, key - 1] += coefficient * value
                return
            M[row, j - 1] += coefficient

        for j, coefficient in ((i - 2, 1.0), (i - 1, -4.0), (i, 6.0), (i + 1, -4.0), (i + 2, 1.0)):
            add(j, coefficient / h**4)
        for j, coefficient in ((i - 1, 1.0), (i, -2.0), (i + 1, 1.0)):
            add(j, -2.0 * A**2 * coefficient / h**2)
        add(i, A**4)

    values = np.linalg.eigvals(M / A**2)
    values = np.real(values[np.abs(np.imag(values)) < 1e-6 * np.abs(values).max()])
    values = values[values > 0]
    return float(values.min()) * b**2 / math.pi**2


def run_eigenvalue_checks() -> list[Check]:
    """k = 4.0 and k = 0.425, solved rather than looked up."""
    checks: list[Check] = []

    internal = min(_plate_k(False, 160, ab) for ab in np.linspace(0.7, 1.4, 25))
    checks.append(
        Check(
            "Plate eigenvalue solve: both edges simply supported gives k = 4.0",
            _close(internal, 4.0, 2e-3),
            f"solved k = {internal:.6f}, module uses {K_INTERNAL}",
        )
    )
    # The minimum for a simply supported plate is reached when it buckles
    # into square panels, a = m*b -- a separate, sharper statement than
    # the value of k itself.
    square = _plate_k(False, 160, 1.0)
    off = _plate_k(False, 160, 1.6)
    checks.append(
        Check(
            "Plate eigenvalue solve: that minimum is at a = m*b (square panels)",
            _close(square, 4.0, 2e-3) and off > square,
            f"k(a=b) = {square:.5f}, k(a=b/1.6) = {off:.5f}",
        )
    )

    # The outstand's k falls monotonically towards 0.425 as the plate
    # lengthens -- the closed form is 0.425 + (b/a)^2, so the residual at
    # any finite length is that term, and it has to be taken long enough
    # for the term to vanish before comparing with the module's constant.
    aspect = 0.02
    outstand = _plate_k(True, 160, aspect)
    checks.append(
        Check(
            "Plate eigenvalue solve: one edge free gives k = 0.425 as the plate lengthens",
            _close(outstand, 0.425, 5e-3),
            f"solved k = {outstand:.6f} at b/a = {aspect}, where the closed form is "
            f"{0.425 + aspect**2:.6f}; module uses {K_OUTSTAND}",
        )
    )
    longer = _plate_k(True, 160, 0.30)
    checks.append(
        Check(
            "Plate eigenvalue solve: outstand k follows 0.425 + (b/a)^2",
            _close(longer, 0.425 + 0.30**2, 2e-2),
            f"k at b/a = 0.30 is {longer:.5f}, closed form {0.425 + 0.09:.5f}",
        )
    )
    return checks


# --- 2. The closed form, against textbook constants -------------------------


def run_closed_form_checks() -> list[Check]:
    """sigma_cr = k pi^2 E / (12(1-nu^2)) (t/b)^2 collapses, at nu = 0.3,
    to the constants quoted in every stability text."""
    E = 70_000.0
    checks: list[Check] = []
    for k, constant, label in (
        (K_INTERNAL, 3.6152, "both edges supported"),
        (K_OUTSTAND, 0.38412, "outstand"),
    ):
        got = elastic_critical_stress(k, E, NU_TEXTBOOK, thickness=1.0, width=10.0)
        want = constant * E * (1.0 / 10.0) ** 2
        checks.append(
            Check(
                f"Closed form matches the textbook constant, {label}: "
                f"sigma_cr = {constant:.4f} E (t/b)^2",
                _close(got, want, 1e-4),
                f"got {got:.6f} MPa, textbook {want:.6f} MPa",
            )
        )
    # ...and scales as (t/b)^2, which is the whole point of slenderness.
    a = elastic_critical_stress(K_INTERNAL, E, 0.33, 2.0, 40.0)
    b = elastic_critical_stress(K_INTERNAL, E, 0.33, 1.0, 40.0)
    checks.append(
        Check(
            "Critical stress scales with (t/b)^2: halving t quarters it",
            _close(a / b, 4.0, 1e-12),
            f"ratio {a / b}",
        )
    )
    return checks


# --- 3. The code classification, against a published worked example ---------


def run_eurocode_checks() -> list[Check]:
    """Published classification limits for a 7A04-T6 aluminium SHS -- a
    ~500 MPa alloy, essentially the 7075-T6 in this project's material
    list -- are b/t <= 7.75 (Class 1), <= 11.28 (Class 2), <= 15.50
    (Class 3). Those are the class A unwelded internal limits of
    EN 1999-1-1 Table 6.2 scaled by epsilon, and the module has to
    reproduce them from the material list's own numbers."""
    material = get_material("7075-T6 Aluminum (Extruded)")
    eps = epsilon(material.yield_strength)
    published = (7.75, 11.28, 15.50)
    checks = [
        Check(
            "Eurocode 9: epsilon for a ~500 MPa alloy",
            _close(eps, math.sqrt(250.0 / 503.0), 1e-12),
            f"eps = {eps:.5f} from f_o = {material.yield_strength} MPa",
        )
    ]
    for i, (limit, want) in enumerate(zip(EC9_INTERNAL_LIMITS, published), start=1):
        got = limit * eps
        checks.append(
            Check(
                f"Eurocode 9: Class {i} internal limit reproduces the published b/t = {want}",
                _close(got, want, 3e-3),
                f"computed {got:.3f}, published {want}",
            )
        )
    # ...and the classifier itself lands on the right side of each.
    boundaries_ok = (
        ec9_class(7.70, eps, EC9_INTERNAL_LIMITS) == 1
        and ec9_class(7.80, eps, EC9_INTERNAL_LIMITS) == 2
        and ec9_class(11.20, eps, EC9_INTERNAL_LIMITS) == 2
        and ec9_class(11.35, eps, EC9_INTERNAL_LIMITS) == 3
        and ec9_class(15.45, eps, EC9_INTERNAL_LIMITS) == 3
        and ec9_class(15.60, eps, EC9_INTERNAL_LIMITS) == 4
    )
    checks.append(
        Check(
            "Eurocode 9: the classifier steps class at each published boundary",
            boundaries_ok,
            "classes at b/t = 7.70/7.80/11.20/11.35/15.45/15.60: "
            + str([ec9_class(v, eps, EC9_INTERNAL_LIMITS) for v in (7.70, 7.80, 11.20, 11.35, 15.45, 15.60)]),
        )
    )
    return checks


# --- 4. The geometry, against sections measurable by hand -------------------


def run_geometry_checks() -> list[Check]:
    material = get_material("6063-T6 Aluminum (Extruded)")
    checks: list[Check] = []

    # A 60x40 tube with 4mm walls: four internal plates, mid-line widths
    # exactly 60-4 = 56 and 40-4 = 36.
    tube = analyze_local_buckling(
        rounded_rect(0, 0, 60, 40, 3.0), [rounded_rect(4, 4, 56, 36, 3.0, ccw=False)], material
    )
    widths = sorted(round(s.width, 1) for s in tube.segments)
    checks.append(
        Check(
            "60x40 tube, 4mm walls: four walls, all with both edges supported",
            len(tube.segments) == 4 and all(s.support == "internal" for s in tube.segments),
            f"got {[(round(s.width, 2), s.support) for s in tube.segments]}",
        )
    )
    checks.append(
        Check(
            "60x40 tube: mid-line plate widths are 36.0 and 56.0 mm",
            len(widths) == 4
            and all(_close(w, e, 3e-3) for w, e in zip(widths, [36.0, 36.0, 56.0, 56.0])),
            f"got {widths}",
        )
    )
    checks.append(
        Check(
            "60x40 tube: every wall uses the internal k = 4.0",
            all(s.k == K_INTERNAL for s in tube.segments),
            f"got {[s.k for s in tube.segments]}",
        )
    )

    # The same tube at 1.2mm wall is Class 4 -- local buckling before yield.
    thin = analyze_local_buckling(
        rounded_rect(0, 0, 60, 40, 3.0),
        [rounded_rect(1.2, 1.2, 58.8, 38.8, 3.0, ccw=False)],
        material,
    )
    widest = max(thin.segments, key=lambda s: s.slenderness)
    checks.append(
        Check(
            "Same tube at 1.2mm wall: b = 58.8mm, b/t = 49, Class 4 (slender)",
            _close(widest.width, 58.8, 3e-3)
            and _close(widest.slenderness, 49.0, 3e-3)
            and widest.section_class == 4,
            f"b = {widest.width:.2f}, b/t = {widest.slenderness:.2f}, class {widest.section_class}",
        )
    )
    # Its critical stress has to be below the material's proof stress --
    # that is what Class 4 MEANS, and the two criteria are independent.
    checks.append(
        Check(
            "Same tube: elastic critical stress is below f_o, agreeing with Class 4",
            widest.elastic_critical_stress < material.yield_strength,
            f"sigma_cr = {widest.elastic_critical_stress:.1f} MPa vs f_o = "
            f"{material.yield_strength} MPa",
        )
    )
    checks.append(
        Check(
            "4mm tube is NOT Class 4, on the same geometry and material",
            all(s.section_class < 4 for s in tube.segments),
            f"classes {[s.section_class for s in tube.segments]}",
        )
    )

    # An L-angle: BOTH legs are outstands. This is the check that the edge
    # classification is real -- calling these internal would overstate the
    # critical stress by 4.0/0.425 = 9.4x.
    angle = analyze_local_buckling(L_ANGLE, None, material)
    checks.append(
        Check(
            "L-angle: both legs classified as outstands (one edge free)",
            len(angle.segments) == 2 and all(s.support == "outstand" for s in angle.segments),
            f"got {[(round(s.width, 2), s.support) for s in angle.segments]}",
        )
    )
    leg_widths = sorted(s.width for s in angle.segments)
    checks.append(
        Check(
            "L-angle: outstand widths are the mid-line 45 and 55 mm",
            _close(leg_widths[0], 45.0, 1e-2) and _close(leg_widths[1], 55.0, 1.5e-2),
            f"got {[round(w, 2) for w in leg_widths]}",
        )
    )

    # An I-beam: the web is internal (supported by both flanges), the four
    # flange halves are outstands. Both edge conditions in one section.
    beam = analyze_local_buckling(I_BEAM, None, material)
    web = [s for s in beam.segments if s.support == "internal"]
    flanges = [s for s in beam.segments if s.support == "outstand"]
    checks.append(
        Check(
            "I-beam: one internal web and four outstand flange halves",
            len(web) == 1 and len(flanges) == 4,
            f"got {[(round(s.width, 2), s.support) for s in beam.segments]}",
        )
    )
    if web:
        checks.append(
            Check(
                "I-beam: the web is 94mm deep between the flanges, 5mm thick",
                _close(web[0].width, 94.0, 1e-2) and _close(web[0].thickness, 5.0, 1e-6),
                f"b = {web[0].width:.2f}, t = {web[0].thickness:.2f}",
            )
        )
    if flanges:
        # Between the clear half-flange (60-5)/2 = 27.5 and the mid-line 30.
        checks.append(
            Check(
                "I-beam: flange outstands lie between the clear 27.5 and mid-line 30 mm",
                all(27.0 <= s.width <= 30.2 for s in flanges),
                f"got {[round(s.width, 2) for s in flanges]}",
            )
        )

    # A solid bar has no plate elements at all, and must not invent any.
    bar = analyze_local_buckling([(0, 0), (50, 0), (50, 100), (0, 100)], None, material)
    checks.append(
        Check(
            "Solid 50x100 bar: no plate elements claimed (it is a column, not a plate assembly)",
            all(s.support == "uncertain" for s in bar.segments),
            f"got {[(round(s.width, 2), s.support) for s in bar.segments]}",
        )
    )
    return checks


# --- 5. Ordering and internal consistency -----------------------------------


def run_consistency_checks() -> list[Check]:
    """Cross-checks between the two criteria and against global buckling."""
    material = get_material("6063-T6 Aluminum (Extruded)")
    checks: list[Check] = []

    # An outstand and an internal part of identical b and t must differ by
    # exactly the ratio of their k values, and nothing else.
    internal = elastic_critical_stress(K_INTERNAL, material.E, material.nu, 2.0, 40.0)
    outstand = elastic_critical_stress(K_OUTSTAND, material.E, material.nu, 2.0, 40.0)
    checks.append(
        Check(
            "Edge condition is worth exactly k_internal/k_outstand = 9.41x",
            _close(internal / outstand, K_INTERNAL / K_OUTSTAND, 1e-12),
            f"ratio {internal / outstand:.4f}",
        )
    )

    # The Class 3/4 boundary must sit at a LOWER b/t than the elastic
    # yield-crossover: Eurocode 9 allows for imperfections, the elastic
    # formula does not. If this ever inverted, one of them is wrong.
    eps = epsilon(material.yield_strength)
    code_boundary = EC9_INTERNAL_LIMITS[2] * eps
    elastic_boundary = math.sqrt(
        K_INTERNAL * math.pi**2 * material.E
        / (12 * (1 - material.nu**2) * material.yield_strength)
    )
    checks.append(
        Check(
            "Eurocode 9's Class 3/4 boundary is more conservative than elastic theory",
            code_boundary < elastic_boundary,
            f"code b/t = {code_boundary:.2f}, elastic yield-crossover b/t = "
            f"{elastic_boundary:.2f} (ratio {elastic_boundary / code_boundary:.2f})",
        )
    )

    # The governing segment has to be the worst one actually reported.
    thin = analyze_local_buckling(
        rounded_rect(0, 0, 60, 40, 3.0),
        [rounded_rect(1.2, 1.2, 58.8, 38.8, 3.0, ccw=False)],
        material,
    )
    rated = [s for s in thin.segments if s.elastic_critical_stress is not None]
    checks.append(
        Check(
            "The reported governing wall is the one with the lowest critical stress",
            thin.governing is not None
            and thin.governing.elastic_critical_stress
            == min(s.elastic_critical_stress for s in rated),
            f"governing segment {thin.governing.index if thin.governing else None}",
        )
    )
    return checks


def run_yield_cap_checks() -> list[Check]:
    """The effective (yield-capped) safety factor.

    Perfect-plate theory has no upper bound, so a stocky wall's elastic
    critical stress runs far past anything the material can reach and the
    elastic safety factor built on it is reassuring about the wrong
    failure mode. Each wall therefore carries two factors; these check
    that both are right and that the right one governs.

    Arithmetic is done here from `elastic_critical_stress` and the
    material's own proof stress rather than read back from the segment,
    so the cap is checked against the definition, not against itself.
    """
    material = get_material("6063-T6 Aluminum (Extruded)")
    f_o = material.yield_strength
    checks: list[Check] = []

    # A 40x40 tube with 6mm walls: b/t ~ 5.7, far too stocky to buckle
    # elastically before it yields.
    t = 6.0
    outer = [(0, 0), (40, 0), (40, 40), (0, 40)]
    inner = [(t, t), (40 - t, t), (40 - t, 40 - t), (t, 40 - t)]
    stocky_section = analyze_section(outer, material, mesh_size=0.5, holes=[inner])
    stocky = analyze_local_buckling(
        outer, [inner], material,
        applied_axial_stress=20_000 / stocky_section.area,
        moment=0.0, bending_axis="y", section=stocky_section,
    )
    rated = [s for s in stocky.segments if s.effective_safety_factor is not None]
    checks.append(
        Check("Stocky tube: every wall is rated", len(rated) == len(stocky.segments) and bool(rated),
              f"{len(rated)}/{len(stocky.segments)}")
    )
    for s in rated:
        checks.append(
            Check(
                f"Stocky wall {s.index}: sigma_cr exceeds the proof stress",
                s.elastic_critical_stress > f_o,
                f"sigma_cr={s.elastic_critical_stress:,.0f} vs f_o={f_o:,.0f}",
            )
        )
        checks.append(
            Check(
                f"Stocky wall {s.index}: capacity capped at f_o",
                _close(s.yield_capped_stress, f_o, 1e-12),
                f"capped={s.yield_capped_stress}",
            )
        )
        checks.append(
            Check(
                f"Stocky wall {s.index}: effective SF = f_o / applied",
                _close(s.effective_safety_factor, f_o / s.applied_stress, 1e-9),
                f"{s.effective_safety_factor} vs {f_o / s.applied_stress}",
            )
        )
        checks.append(
            Check(
                f"Stocky wall {s.index}: flagged yield-governed",
                s.yield_governed is True,
            )
        )
        checks.append(
            Check(
                f"Stocky wall {s.index}: effective SF is far below the elastic one",
                s.effective_safety_factor < s.safety_factor / 10,
                f"elastic={s.safety_factor:,.1f}, effective={s.effective_safety_factor:,.2f}",
            )
        )
        checks.append(
            Check(
                f"Stocky wall {s.index}: the elastic SF itself is unchanged",
                _close(s.safety_factor, s.elastic_critical_stress / s.applied_stress, 1e-9),
            )
        )

    # A slender wall, where sigma_cr < f_o and the cap must NOT bite: the
    # two factors have to agree exactly, or the cap is changing answers it
    # has no business touching.
    t = 1.0
    outer = [(0, 0), (120, 0), (120, 120), (0, 120)]
    inner = [(t, t), (120 - t, t), (120 - t, 120 - t), (t, 120 - t)]
    slender_section = analyze_section(outer, material, mesh_size=8.0, holes=[inner])
    slender = analyze_local_buckling(
        outer, [inner], material,
        applied_axial_stress=20_000 / slender_section.area,
        moment=0.0, bending_axis="y", section=slender_section,
    )
    slender_rated = [s for s in slender.segments if s.effective_safety_factor is not None]
    checks.append(Check("Slender tube: walls are rated", bool(slender_rated), f"{len(slender_rated)}"))
    for s in slender_rated:
        checks.append(
            Check(
                f"Slender wall {s.index}: sigma_cr is below the proof stress",
                s.elastic_critical_stress < f_o,
                f"sigma_cr={s.elastic_critical_stress:,.1f} vs f_o={f_o:,.0f}",
            )
        )
        checks.append(
            Check(
                f"Slender wall {s.index}: cap does not bite — the two SFs agree",
                _close(s.effective_safety_factor, s.safety_factor, 1e-12)
                and s.yield_governed is False,
                f"elastic={s.safety_factor}, effective={s.effective_safety_factor}",
            )
        )

    # ...and the governing wall is chosen on the EFFECTIVE factor.
    if stocky.governing is not None:
        checks.append(
            Check(
                "Governing wall is the lowest EFFECTIVE safety factor",
                stocky.governing.effective_safety_factor
                == min(s.effective_safety_factor for s in rated),
            )
        )
    return checks


def main() -> int:
    checks = (
        run_eigenvalue_checks()
        + run_closed_form_checks()
        + run_eurocode_checks()
        + run_geometry_checks()
        + run_consistency_checks()
        + run_yield_cap_checks()
    )
    width = max(len(c.label) for c in checks) + 2
    all_passed = True
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        detail = f"  ({c.detail})" if c.detail and not c.passed else ""
        print(f"{c.label:<{width}} {status}{detail}")
    print()
    print(
        f"{len(checks)} checks. "
        + ("All passed." if all_passed else "FAILURES DETECTED — see rows marked FAIL above.")
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
