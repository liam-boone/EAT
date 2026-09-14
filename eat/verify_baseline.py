"""
Verification harness for the baseline-comparison store (build step 10).

Exercises eat.baseline directly (setting persistence, resolution,
history-reference fallback, and the built-in KJN profile) against
isolated temp files -- same isolation approach as eat.verify_history.py,
for the same reason (every function here accepts an explicit path, so
tests never touch the real eat/baseline.json or eat/history.json a
user's actual runs/settings live in).

The six comparison metrics (dual-bar stiffness/strength-to-weight, plus
mass per length) themselves live client-side in app.js -- plain division,
not duplicated here or in the API; what this file verifies is that the
*inputs* to that arithmetic are correct: the built-in baseline's
Area/Ixx/Iyy/Z-moduli/Mass-per-length are cross-checked against the KJN
figures already independently established in eat.verify_dxf.py's
real-catalog-file check (its own geometry is too complex to hand-derive
from scratch), and a plain 50x100mm rectangle's properties are confirmed
against textbook composite-shape formulas. With both sides independently
confirmed correct, the six metrics are reproducible with a calculator --
see run_hand_checkable_comparison_check()'s docstring for the actual
numbers.

Also verifies the algebraic sanity check the axial metrics must satisfy:
EA/mass-per-length reduces to E/density, and (yield*Area)/mass-per-length
reduces to yield/density -- both pure material properties, independent of
the cross-section's shape or area. run_axial_material_property_check()
confirms this holds in the actual computed numbers (not just assumed):
two differently-shaped sections in the *same* material give equal axial
stiffness/strength-to-weight, and the same shape in two *different*
materials gives different values.

The HTTP-level wiring (GET/POST /baseline, and the frontend's rendered
dual bars) is covered separately in eat/verify_api.py and
eat/verify_frontend.py.

Run with: python -m eat.verify_baseline
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from eat import history
from eat.baseline import (
    BUILTIN_NAME,
    _compute_builtin_baseline,
    get_baseline_setting,
    resolve_baseline,
    set_baseline_setting,
)
from eat.section import Material, analyze_section


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _rel_close(actual: float, expected: float, tol: float = 1e-6) -> bool:
    if expected == 0:
        return abs(actual) < 1e-9
    return abs(actual - expected) / abs(expected) <= tol


def run_setting_persistence_checks() -> list[Check]:
    checks: list[Check] = []

    with tempfile.TemporaryDirectory() as tmp:
        setting_path = Path(tmp) / "baseline.json"

        # --- Default, before anything is ever set ---
        default = get_baseline_setting(setting_path)
        checks.append(Check("No setting file yet -> defaults to builtin", default == {"type": "builtin"}, f"{default}"))

        # --- Explicit set + persistence ---
        set_baseline_setting({"type": "history", "entry_id": "abc123"}, setting_path)
        reloaded = get_baseline_setting(setting_path)
        checks.append(
            Check(
                "Setting persists across a fresh load (simulated restart)",
                reloaded == {"type": "history", "entry_id": "abc123"},
                f"{reloaded}",
            )
        )

        set_baseline_setting({"type": "builtin"}, setting_path)
        checks.append(Check("Setting can be changed back to builtin", get_baseline_setting(setting_path) == {"type": "builtin"}))

    return checks


def run_resolution_checks() -> list[Check]:
    checks: list[Check] = []

    with tempfile.TemporaryDirectory() as tmp:
        setting_path = Path(tmp) / "baseline.json"
        history_path = Path(tmp) / "history.json"

        # --- Default resolves to the builtin KJN profile ---
        info = resolve_baseline(setting_path, history_path)
        checks.append(Check("Default resolution is the builtin baseline", info.source == "builtin" and info.name == BUILTIN_NAME))

        # --- A history entry, once set, resolves to that entry's data ---
        rect_material = Material(name="Test Steel", E=200_000, nu=0.3, density=7850)
        rect = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], rect_material, mesh_size=1.0)
        entry = history.add_entry(
            material="Test Steel",
            vertices=[(0, 0), (50, 0), (50, 100), (0, 100)],
            holes=None,
            section_result=rect.as_dict(),
            path=history_path,
        )
        set_baseline_setting({"type": "history", "entry_id": entry.id}, setting_path)
        info2 = resolve_baseline(setting_path, history_path)
        checks.append(
            Check(
                "History-type setting resolves to that entry's stored section_result",
                info2.source == "history"
                and info2.history_entry_id == entry.id
                and _rel_close(info2.section_result["area"], 5000.0),
                f"source={info2.source}, id={info2.history_entry_id}, area={info2.section_result.get('area')}",
            )
        )

        # --- Deleting the referenced entry falls back to builtin, not an error ---
        history.delete_entry(entry.id, history_path)
        info3 = resolve_baseline(setting_path, history_path)
        checks.append(
            Check(
                "A dangling history reference (entry since deleted) falls back to builtin",
                info3.source == "builtin",
                f"source={info3.source}",
            )
        )

    return checks


def _z_worst(plus: float, minus: float) -> float:
    """Governing (worst-case, smaller) section modulus -- same convention
    eat.beam uses for max_bending_stress."""
    return min(plus, minus)


def run_hand_checkable_comparison_check() -> list[Check]:
    """Cross-checks the two sections' properties a real "rectangle vs. KJN
    baseline" comparison would combine, each independently, then confirms
    the six stiffness/strength-to-weight metrics (plus mass per length)
    app.js's buildBaselineMetricGroups() would compute from them.

    Baseline (KJN, 6063-T6, yield=214 MPa): Area/Ixx/Iyy/Z-moduli/Mass-per-
    length are checked against the exact figures eat.verify_dxf.py's
    real-catalog-file check already independently established (287.6550
    mm^2, 11994.9 mm^4, 46266.1 mm^4, 0.7767 kg/m) -- not re-derived here,
    since a 246-vertex T-slot profile's moments of inertia aren't
    hand-calculable from scratch.

    Comparison profile: a plain 50x100mm rectangle in "Test Steel"
    (E=200,000 MPa, yield=250 MPa, density=7850 kg/m^3 --
    eat.verify_beam.py/eat.verify_section.py's own hand-calc convention),
    whose properties are exact textbook values:
      Ixx = 50*100^3/12 = 4,166,666.667 mm^4;  Iyy = 100*50^3/12 = 1,041,666.667 mm^4
      Zxx = Ixx/50 = 83,333.33 mm^3 (both faces, symmetric); Zyy = Iyy/25 = 41,666.67 mm^3
      mass/length = 7850 * (50*100*1e-6) = 39.25 kg/m

    With both sides independently confirmed, each metric is plain
    division/multiplication, reproducible with a calculator:
      Mass per length:            rect=39.25            base=0.776669 kg/m
      Stiffness-to-Weight Axial:  rect=200000*5000/39.25=25,477,707      base=EA/mass=25,518,519 N/(kg/m)
      Stiffness-to-Weight Bend X: rect=200000*1041666.667/39.25=5,307,856k  base=EIyy/mass=4,104,365k N.mm^2/(kg/m)
      Stiffness-to-Weight Bend Y: rect=200000*4166666.667/39.25=21,231,423k base=EIxx/mass=1,064,094k N.mm^2/(kg/m)
      Strength-to-Weight Axial:   rect=250*5000/39.25=31,847              base=yield*Area/mass=79,259 N/(kg/m)
      Strength-to-Weight Bend X:  rect=250*41666.67/39.25=265,393         base=yield*Zyy/mass=637,397 N.mm/(kg/m)
      Strength-to-Weight Bend Y:  rect=250*83333.33/39.25=530,786         base=yield*Zxx/mass=330,502 N.mm/(kg/m)
    (The steel rectangle dwarfs the thin-walled aluminum T-slot extrusion
    on most axes, as expected for a solid steel bar vs. a hollow aluminum
    profile a fraction of its size -- except strength-to-weight, where the
    much lighter, higher-yield-per-mass KJN profile actually wins on
    bending, illustrating exactly why a strength-to-weight comparison is
    useful and not just a restatement of the stiffness one.)
    """
    checks: list[Check] = []

    info = _compute_builtin_baseline()
    sr = info.section_result
    checks.append(Check("Builtin baseline: Area matches eat.verify_dxf.py's established figure", _rel_close(sr["area"], 287.6550146754686), f"{sr['area']}"))
    checks.append(Check("Builtin baseline: Ixx matches eat.verify_dxf.py's established figure", _rel_close(sr["ixx"], 11994.901146218466), f"{sr['ixx']}"))
    checks.append(Check("Builtin baseline: Iyy matches eat.verify_dxf.py's established figure", _rel_close(sr["iyy"], 46266.05703211167), f"{sr['iyy']}"))
    checks.append(
        Check(
            "Builtin baseline: Mass/length matches eat.verify_dxf.py's established figure",
            _rel_close(sr["mass_per_length"], 0.7766685396237651),
            f"{sr['mass_per_length']}",
        )
    )
    b_zxx = _z_worst(sr["zxx_plus"], sr["zxx_minus"])
    b_zyy = _z_worst(sr["zyy_plus"], sr["zyy_minus"])
    checks.append(Check("Builtin baseline: governing Zxx matches its own zxx_plus/minus", _rel_close(b_zxx, 1199.4901146218467), f"{b_zxx}"))
    checks.append(Check("Builtin baseline: governing Zyy matches its own zyy_plus/minus", _rel_close(b_zyy, 2313.300042821097), f"{b_zyy}"))
    b_yield = 214.0  # 6063-T6, materials.json

    rect_material = Material(name="Test Steel", E=200_000, nu=0.3, yield_strength=250, density=7850)
    rect = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], rect_material, mesh_size=1.0)
    ixx_exp = 50 * 100**3 / 12
    iyy_exp = 100 * 50**3 / 12
    mass_exp = 7850 * (50 * 100) * 1e-6
    checks.append(Check("Rectangle: Ixx matches the textbook formula", _rel_close(rect.ixx, ixx_exp), f"{rect.ixx}"))
    checks.append(Check("Rectangle: Iyy matches the textbook formula", _rel_close(rect.iyy, iyy_exp), f"{rect.iyy}"))
    checks.append(Check("Rectangle: Mass/length matches the textbook formula", _rel_close(rect.mass_per_length, mass_exp), f"{rect.mass_per_length}"))
    r_zxx = _z_worst(rect.zxx_plus, rect.zxx_minus)
    r_zyy = _z_worst(rect.zyy_plus, rect.zyy_minus)
    checks.append(Check("Rectangle: governing Zxx matches Ixx/50 (symmetric section)", _rel_close(r_zxx, ixx_exp / 50), f"{r_zxx}"))
    checks.append(Check("Rectangle: governing Zyy matches Iyy/25 (symmetric section)", _rel_close(r_zyy, iyy_exp / 25), f"{r_zyy}"))

    metrics = {
        "Mass per length": (rect.mass_per_length, sr["mass_per_length"], 39.24999999999991, 0.7766685396237651),
        "Stiffness-to-Weight Axial": (rect.ea / rect.mass_per_length, sr["ea"] / sr["mass_per_length"], 25477707.006369427, 25518518.51851852),
        "Stiffness-to-Weight Bending (X, uses Iyy)": (rect.ei_yy / rect.mass_per_length, sr["ei_yy"] / sr["mass_per_length"], 5307855626.326859, 4104365204.5655146),
        "Stiffness-to-Weight Bending (Y, uses Ixx)": (rect.ei_xx / rect.mass_per_length, sr["ei_xx"] / sr["mass_per_length"], 21231422505.308517, 1064094458.3319955),
        "Strength-to-Weight Axial": (
            rect_material.yield_strength * rect.area / rect.mass_per_length,
            b_yield * sr["area"] / sr["mass_per_length"],
            31847.133757961783,
            79259.25925925926,
        ),
        "Strength-to-Weight Bending (X, uses Zyy)": (
            rect_material.yield_strength * r_zyy / rect.mass_per_length,
            b_yield * b_zyy / sr["mass_per_length"],
            265392.78131634195,
            637397.0154675323,
        ),
        "Strength-to-Weight Bending (Y, uses Zxx)": (
            rect_material.yield_strength * r_zxx / rect.mass_per_length,
            b_yield * b_zxx / sr["mass_per_length"],
            530785.5626327129,
            330502.48778381286,
        ),
    }
    for label, (rect_val, base_val, expected_rect, expected_base) in metrics.items():
        checks.append(
            Check(
                f"{label}: profile value matches hand calc",
                _rel_close(rect_val, expected_rect, 1e-3),
                f"{rect_val} vs expected {expected_rect}",
            )
        )
        checks.append(
            Check(
                f"{label}: baseline value matches hand calc",
                _rel_close(base_val, expected_base, 1e-3),
                f"{base_val} vs expected {expected_base}",
            )
        )

    return checks


def run_axial_material_property_check() -> list[Check]:
    """The axial metrics algebraically reduce to pure material properties:
    EA/mass = E/density, and (yield*Area)/mass = yield/density -- both
    independent of the cross-section's shape or area (Area cancels: mass =
    density*Area*1e-6, so EA/mass = E/(density*1e-6) regardless of Area).

    Verified in the actual computed numbers, not just asserted from the
    algebra: a rectangle and an L-angle (very different shapes) in the
    *same* material must give equal axial stiffness/strength-to-weight,
    while the same rectangle in two *different* materials must not.
    """
    checks: list[Check] = []

    steel = Material(name="Test Steel", E=200_000, nu=0.3, yield_strength=250, density=7850)
    alum = Material(name="Test Aluminum", E=69_000, nu=0.33, yield_strength=241, density=2700)

    rect_steel = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], steel, mesh_size=1.0)
    angle_steel = analyze_section([(0, 0), (50, 0), (50, 10), (10, 10), (10, 60), (0, 60)], steel, mesh_size=0.5)
    rect_alum = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], alum, mesh_size=1.0)

    def axial_stiffness(section, material):
        return section.ea / section.mass_per_length

    def axial_strength(section, material):
        return material.yield_strength * section.area / section.mass_per_length

    checks.append(
        Check(
            "Same material, different shape: axial stiffness-to-weight is equal (EA/mass = E/density)",
            _rel_close(axial_stiffness(rect_steel, steel), axial_stiffness(angle_steel, steel), 1e-6),
            f"rect={axial_stiffness(rect_steel, steel)}, angle={axial_stiffness(angle_steel, steel)}",
        )
    )
    checks.append(
        Check(
            "Same material, different shape: axial strength-to-weight is equal (yield*Area/mass = yield/density)",
            _rel_close(axial_strength(rect_steel, steel), axial_strength(angle_steel, steel), 1e-6),
            f"rect={axial_strength(rect_steel, steel)}, angle={axial_strength(angle_steel, steel)}",
        )
    )

    expected_stiffness = steel.E / steel.density * 1e6
    expected_strength = steel.yield_strength / steel.density * 1e6
    checks.append(
        Check(
            "Same-material axial stiffness-to-weight equals E/density (unit-converted)",
            _rel_close(axial_stiffness(rect_steel, steel), expected_stiffness, 1e-6),
            f"{axial_stiffness(rect_steel, steel)} vs E/density={expected_stiffness}",
        )
    )
    checks.append(
        Check(
            "Same-material axial strength-to-weight equals yield/density (unit-converted)",
            _rel_close(axial_strength(rect_steel, steel), expected_strength, 1e-6),
            f"{axial_strength(rect_steel, steel)} vs yield/density={expected_strength}",
        )
    )

    checks.append(
        Check(
            "Different materials, same shape: axial stiffness-to-weight correctly diverges",
            not _rel_close(axial_stiffness(rect_steel, steel), axial_stiffness(rect_alum, alum), 1e-3),
            f"steel={axial_stiffness(rect_steel, steel)}, alum={axial_stiffness(rect_alum, alum)}",
        )
    )
    checks.append(
        Check(
            "Different materials, same shape: axial strength-to-weight correctly diverges",
            not _rel_close(axial_strength(rect_steel, steel), axial_strength(rect_alum, alum), 1e-3),
            f"steel={axial_strength(rect_steel, steel)}, alum={axial_strength(rect_alum, alum)}",
        )
    )

    return checks


def run_recovery_checks() -> list[Check]:
    """A damaged baseline.json resets to the built-in rather than raising.

    This is a one-line setting; 500-ing every comparison over it would be
    absurd. Anything that isn't a JSON object counts as damaged, because
    the setting is read with `.get` and a list or a bare number would
    otherwise raise an AttributeError deeper in."""
    import tempfile

    from eat.storage import clear_store_warnings, store_warnings

    checks: list[Check] = []
    cases = {
        "truncated": '{"type": "bui',
        "empty file": "",
        "a JSON array, not an object": "[]",
        "a bare number": "42",
    }
    for label, damage in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(damage)
            clear_store_warnings()
            try:
                setting = get_baseline_setting(path)
                raised = None
            except Exception as exc:  # noqa: BLE001
                setting, raised = None, exc
            checks.append(
                Check(f"Recovery ({label}): loads without raising", raised is None,
                      f"{type(raised).__name__}: {raised}" if raised else ""))
            if raised is not None:
                continue
            checks.append(
                Check(f"Recovery ({label}): resets to the built-in baseline",
                      setting == {"type": "builtin"}, f"{setting}"))
            checks.append(
                Check(f"Recovery ({label}): the damaged file is kept, not deleted",
                      any(".corrupt-" in q.name for q in Path(tmp).iterdir()),
                      f"{[q.name for q in Path(tmp).iterdir()]}"))
            checks.append(
                Check(f"Recovery ({label}): a warning is recorded", bool(store_warnings())))
    clear_store_warnings()
    return checks


def main() -> int:
    checks = (
        run_setting_persistence_checks()
        + run_resolution_checks()
        + run_hand_checkable_comparison_check()
        + run_axial_material_property_check()
        + run_recovery_checks()
    )
    return _report(checks)


def _report(checks: list[Check]) -> int:
    width = max(len(c.label) for c in checks) + 2
    all_passed = True
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        detail = f"  ({c.detail})" if c.detail and not c.passed else ""
        print(f"{c.label:<{width}} {status}{detail}")
    print()
    print(f"{len(checks)} checks. " + ("All passed." if all_passed else "FAILURES DETECTED — see rows marked FAIL above."))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
