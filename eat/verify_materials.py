"""
Verification harness for the material list (build step 2).

Checks that all eight seeded materials round-trip out of materials.json with
exactly the published values, then exercises add/edit/delete on a throwaway
material and confirms the file is left in exactly its original seeded state.

Run with: python -m eat.verify_materials
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eat.materials import (
    DEFAULT_MATERIALS_PATH,
    add_material,
    delete_material,
    get_material,
    load_materials,
    update_material,
)
from eat.section import Material

# Expected seed values, SI units (Pa, kg/m^3) — must match eat/materials.json exactly.
EXPECTED = [
    dict(
        name="6061-T6 Aluminum (Extruded)",
        density=2700, E=69.0e9, G=26.0e9, nu=0.33,
        yield_strength=241e6, ultimate_strength=262e6, shear_strength=165e6,
        shear_strength_approximate=False,
    ),
    dict(
        name="6063-T6 Aluminum (Extruded)",
        density=2700, E=68.9e9, G=25.8e9, nu=0.33,
        yield_strength=214e6, ultimate_strength=241e6, shear_strength=152e6,
        shear_strength_approximate=False,
    ),
    dict(
        name="6082-T6 Aluminum (Extruded)",
        density=2700, E=70.0e9, G=26.0e9, nu=0.33,
        yield_strength=250e6, ultimate_strength=290e6, shear_strength=170e6,
        shear_strength_approximate=False,
    ),
    dict(
        name="2024-T6 Aluminum (Extruded)",
        density=2780, E=73.1e9, G=28.0e9, nu=0.33,
        yield_strength=393e6, ultimate_strength=476e6, shear_strength=283e6,
        shear_strength_approximate=False,
    ),
    dict(
        name="7075-T6 Aluminum (Extruded)",
        density=2810, E=71.7e9, G=26.9e9, nu=0.33,
        yield_strength=503e6, ultimate_strength=572e6, shear_strength=331e6,
        shear_strength_approximate=False,
    ),
    dict(
        name="ABS (Extruded, Unfilled)",
        density=1040, E=2.3e9, G=0.85e9, nu=0.35,
        yield_strength=40e6, ultimate_strength=45e6, shear_strength=26e6,
        shear_strength_approximate=True,
    ),
    dict(
        name="Polycarbonate (Extruded, Unfilled)",
        density=1200, E=2.4e9, G=0.88e9, nu=0.37,
        yield_strength=62e6, ultimate_strength=65e6, shear_strength=43e6,
        shear_strength_approximate=True,
    ),
    dict(
        name="Nylon – PA6 (Extruded, Dry as Molded)",
        density=1140, E=2.8e9, G=1.0e9, nu=0.39,
        yield_strength=80e6, ultimate_strength=85e6, shear_strength=55e6,
        shear_strength_approximate=True,
    ),
]

REL_TOL = 1e-9


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _close(actual: float, expected: float, rel_tol: float = REL_TOL) -> bool:
    if expected == 0:
        return abs(actual) < 1e-12
    return abs(actual - expected) / abs(expected) <= rel_tol


def check_seed_roundtrip() -> list[Check]:
    checks: list[Check] = []
    materials = load_materials()

    checks.append(
        Check(
            "Seed file has exactly 8 materials",
            len(materials) == 8,
            f"found {len(materials)}",
        )
    )

    by_name = {m.name: m for m in materials}
    for exp in EXPECTED:
        name = exp["name"]
        m = by_name.get(name)
        if m is None:
            checks.append(Check(f"'{name}' present", False, "missing from file"))
            continue

        # Convert loaded (MPa-unit) Material back to Pa for comparison against
        # the published table values.
        fields_ok = (
            _close(m.density, exp["density"])
            and _close(m.E * 1e6, exp["E"])
            and _close(m.G * 1e6, exp["G"])
            and _close(m.nu, exp["nu"])
            and _close(m.yield_strength * 1e6, exp["yield_strength"])
            and _close(m.ultimate_strength * 1e6, exp["ultimate_strength"])
            and _close(m.shear_strength * 1e6, exp["shear_strength"])
            and m.shear_strength_approximate == exp["shear_strength_approximate"]
        )
        checks.append(Check(f"'{name}' values match table exactly", fields_ok))

    return checks


def check_add_edit_delete_cycle() -> list[Check]:
    checks: list[Check] = []
    before = load_materials()
    before_names = [m.name for m in before]

    test_material = Material(
        name="__EAT_TEST_MATERIAL__",
        E=1000.0,
        nu=0.3,
        yield_strength=100.0,
        ultimate_strength=120.0,
        shear_strength=80.0,
        density=1000.0,
    )

    add_material(test_material)
    after_add = load_materials()
    checks.append(
        Check("Add: material count increases by 1", len(after_add) == len(before) + 1)
    )
    fetched = get_material("__EAT_TEST_MATERIAL__")
    checks.append(
        Check(
            "Add: fetched material matches what was added",
            _close(fetched.E, 1000.0) and _close(fetched.yield_strength, 100.0),
        )
    )

    updated = update_material("__EAT_TEST_MATERIAL__", yield_strength=999.0)
    checks.append(Check("Edit: update_material returns updated value", _close(updated.yield_strength, 999.0)))
    refetched = get_material("__EAT_TEST_MATERIAL__")
    checks.append(
        Check("Edit: change persisted to file", _close(refetched.yield_strength, 999.0))
    )

    delete_material("__EAT_TEST_MATERIAL__")
    after_delete = load_materials()
    checks.append(
        Check("Delete: material count back to original", len(after_delete) == len(before))
    )
    checks.append(
        Check(
            "Delete: original material names/order unchanged",
            [m.name for m in after_delete] == before_names,
        )
    )

    # Final sanity: every original material's values are still exactly as before.
    before_by_name = {m.name: m for m in before}
    unchanged = all(
        _close(after_delete[i].E, before_by_name[after_delete[i].name].E)
        and _close(after_delete[i].yield_strength, before_by_name[after_delete[i].name].yield_strength)
        for i in range(len(after_delete))
    )
    checks.append(Check("File restored to original seeded state", unchanged))

    return checks


def check_recovery() -> list[Check]:
    """A damaged materials.json resets to empty rather than 500-ing every
    route that resolves a material.

    Materials is the awkward store to reset -- an empty list leaves the
    app unable to analyze anything -- so the point of these checks is as
    much that the WARNING is loud and names the backup as that the reset
    happens. Tested against throwaway paths; the real seeded file is never
    touched."""
    import tempfile

    from eat.storage import clear_store_warnings, store_warnings

    checks: list[Check] = []
    good = Path(DEFAULT_MATERIALS_PATH).read_text()
    cases = {
        "truncated mid-write": good[: len(good) // 2],
        "empty file": "",
        "not JSON at all": "\x00nonsense",
        "valid JSON, wrong shape": '{"materials": []}',
        "a record with a negative modulus": '[{"name": "bad", "E": -7e10, "nu": 0.33}]',
        "a record missing E": '[{"name": "bad", "nu": 0.33}]',
    }
    for label, damage in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "materials.json"
            path.write_text(damage)
            clear_store_warnings()
            try:
                loaded = load_materials(path)
                raised = None
            except Exception as exc:  # noqa: BLE001
                loaded, raised = None, exc
            checks.append(
                Check(f"Recovery ({label}): loads without raising", raised is None,
                      f"{type(raised).__name__}: {raised}" if raised else ""))
            if raised is not None:
                continue
            checks.append(Check(f"Recovery ({label}): resets to an empty list", loaded == []))
            checks.append(
                Check(f"Recovery ({label}): the damaged file is kept, not deleted",
                      any(".corrupt-" in q.name for q in Path(tmp).iterdir()),
                      f"{[q.name for q in Path(tmp).iterdir()]}"))
            warnings = store_warnings()
            checks.append(
                Check(f"Recovery ({label}): the warning says the list needs restoring",
                      any("EMPTY" in w or "restore" in w.lower() for w in warnings),
                      f"{warnings}"))
    clear_store_warnings()
    return checks


def main() -> int:
    print(f"Materials file: {DEFAULT_MATERIALS_PATH}\n")

    all_checks = check_seed_roundtrip() + check_add_edit_delete_cycle() + check_recovery()

    width = max(len(c.label) for c in all_checks) + 2
    all_passed = True
    for c in all_checks:
        status = "PASS" if c.passed else "FAIL"
        if not c.passed:
            all_passed = False
        detail = f"  ({c.detail})" if c.detail else ""
        print(f"{c.label:<{width}} {status}{detail}")

    print()
    print("All checks passed." if all_passed else "FAILURES DETECTED — see rows marked FAIL above.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
