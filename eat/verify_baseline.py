"""
Verification harness for the baseline-comparison store (build step 10).

Exercises eat.baseline directly (setting persistence, resolution,
history-reference fallback, and the built-in KJN profile) against
isolated temp files -- same isolation approach as eat.verify_history.py,
for the same reason (every function here accepts an explicit path, so
tests never touch the real eat/baseline.json or eat/history.json a
user's actual runs/settings live in).

The percentage math itself lives client-side in app.js (a one-line
`(current - baseline) / baseline * 100`, not duplicated here or in the
API); what this file verifies is that the *inputs* to that arithmetic are
correct: the built-in baseline's Area/Ixx/Iyy/Mass-per-length are
cross-checked against the KJN figures already independently established
in eat.verify_dxf.py's real-catalog-file check (its own geometry is too
complex to hand-derive from scratch), and a plain 50x100mm rectangle's
properties are confirmed against textbook composite-shape formulas. With
both sides independently confirmed correct, the resulting relative
percentages are reproducible with a calculator -- see
run_hand_checkable_comparison_check()'s docstring for the actual numbers.

The HTTP-level wiring (GET/POST /baseline, POST /baseline/beam, and the
frontend's rendered "+X.X%" text) is covered separately in
eat/verify_api.py and eat/verify_frontend.py.

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


def run_hand_checkable_comparison_check() -> list[Check]:
    """Cross-checks the two numbers a real "rectangle vs. KJN baseline"
    comparison would combine, each independently, then confirms the
    resulting percentages a calculator would produce.

    Baseline (KJN, 6063-T6): Area/Ixx/Iyy/Mass-per-length are checked
    against the exact figures eat.verify_dxf.py's real-catalog-file check
    already independently established (287.6550 mm^2, 11994.9 mm^4,
    46266.1 mm^4, 0.7767 kg/m) -- not re-derived here, since a 246-vertex
    T-slot profile's moments of inertia aren't hand-calculable from
    scratch.

    Comparison profile: a plain 50x100mm rectangle in "Test Steel"
    (E=200,000 MPa, density=7850 kg/m^3 -- eat.verify_section.py's own
    hand-calc convention), whose Ixx/Iyy/mass are exact textbook values:
    Ixx = 50*100^3/12 = 4,166,666.667 mm^4
    Iyy = 100*50^3/12 = 1,041,666.667 mm^4
    mass/length = 7850 * (50*100*1e-6) = 39.25 kg/m

    With both sides independently confirmed, EIxx/EIyy/mass percentages
    ((rect - baseline) / baseline * 100) are plain division, reproducible
    with a calculator from the numbers above:
      EIxx: (200000*4166666.667 - 826448688.97) / 826448688.97 * 100 = +100733.0%
      EIyy: (200000*1041666.667 - 3187731329.51) / 3187731329.51 * 100 = +6435.5%
      mass: (39.25 - 0.7766685396) / 0.7766685396 * 100 = +4953.6%
    (The steel rectangle dwarfs the thin-walled aluminum T-slot extrusion
    on every axis, as expected -- a solid steel bar vs. a hollow aluminum
    profile a fraction of its size.)
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

    rect_material = Material(name="Test Steel", E=200_000, nu=0.3, density=7850)
    rect = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], rect_material, mesh_size=1.0)
    ixx_exp = 50 * 100**3 / 12
    iyy_exp = 100 * 50**3 / 12
    mass_exp = 7850 * (50 * 100) * 1e-6
    checks.append(Check("Rectangle: Ixx matches the textbook formula", _rel_close(rect.ixx, ixx_exp), f"{rect.ixx}"))
    checks.append(Check("Rectangle: Iyy matches the textbook formula", _rel_close(rect.iyy, iyy_exp), f"{rect.iyy}"))
    checks.append(Check("Rectangle: Mass/length matches the textbook formula", _rel_close(rect.mass_per_length, mass_exp), f"{rect.mass_per_length}"))

    eixx_pct = (rect.ei_xx - sr["ei_xx"]) / sr["ei_xx"] * 100
    eiyy_pct = (rect.ei_yy - sr["ei_yy"]) / sr["ei_yy"] * 100
    mass_pct = (rect.mass_per_length - sr["mass_per_length"]) / sr["mass_per_length"] * 100
    checks.append(Check("Rectangle vs KJN baseline: EIxx relative % matches the hand-computed ratio (+100733.0%)", _rel_close(eixx_pct, 100733.03954023428, 1e-3), f"{eixx_pct}"))
    checks.append(Check("Rectangle vs KJN baseline: EIyy relative % matches the hand-computed ratio (+6435.5%)", _rel_close(eiyy_pct, 6435.473407201778, 1e-3), f"{eiyy_pct}"))
    checks.append(Check("Rectangle vs KJN baseline: Mass relative % matches the hand-computed ratio (+4953.6%)", _rel_close(mass_pct, 4953.635881661107, 1e-3), f"{mass_pct}"))

    return checks


def main() -> int:
    checks = run_setting_persistence_checks() + run_resolution_checks() + run_hand_checkable_comparison_check()
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
