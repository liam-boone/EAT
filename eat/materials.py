"""
EAT material list — JSON-backed library of extrusion materials.

`materials.json` stores each entry in plain SI units (Pa for moduli and
strengths, kg/m^3 for density), which is the natural unit system for a
material datasheet. `eat.section.Material` (and the rest of the section
engine) works in the mm-N-MPa unit system, so loading/saving here converts
Pa <-> MPa; density and Poisson's ratio pass through unchanged.

Library use:

    from eat.materials import load_materials, get_material
    materials = load_materials()
    alu = get_material("6061-T6 Aluminum (Extruded)")

Command line:

    python -m eat.materials list
    python -m eat.materials add --name "..." --density 2700 --e-gpa 69 \
        --nu 0.33 --yield-mpa 241 --uts-mpa 262 --shear-mpa 165
    python -m eat.materials edit "<name>" --yield-mpa 250
    python -m eat.materials delete "<name>"

`add` prompts interactively for any required value not passed as a flag.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from eat.section import Material
from eat.storage import quarantine, read_json_or_recover, write_json_atomically

DEFAULT_MATERIALS_PATH = Path(__file__).parent / "materials.json"

# Materials is the awkward store to reset: unlike history (empty is the
# correct default) an empty material list leaves the app unable to
# analyze anything. It still resets rather than raising, because the
# alternative is a server that 500s on every route -- but the warning has
# to say plainly that the list needs restoring, not just that something
# went wrong.
_RECOVERY_HINT = (
    "The material list is now EMPTY, so no analysis can run until it is restored: "
    "recover the backup file, or re-add materials with `python -m eat.materials add` "
    "or POST /materials."
)

_PA_PER_MPA = 1.0e6
_PA_PER_GPA = 1.0e9


def _mpa(pascals: float | None) -> float | None:
    return None if pascals is None else pascals / _PA_PER_MPA


def _pa(megapascals: float | None) -> float | None:
    return None if megapascals is None else megapascals * _PA_PER_MPA


def _record_to_material(record: dict) -> Material:
    return Material(
        name=record["name"],
        E=_mpa(record.get("E")),
        nu=record.get("nu"),
        G=_mpa(record.get("G")),
        yield_strength=_mpa(record.get("yield_strength")),
        ultimate_strength=_mpa(record.get("ultimate_strength")),
        shear_strength=_mpa(record.get("shear_strength")),
        shear_strength_approximate=record.get("shear_strength_approximate", False),
        density=record.get("density"),
    )


def _material_to_record(material: Material) -> dict:
    return {
        "name": material.name,
        "density": material.density,
        "E": _pa(material.E),
        "G": _pa(material.G),
        "nu": material.nu,
        "yield_strength": _pa(material.yield_strength),
        "ultimate_strength": _pa(material.ultimate_strength),
        "shear_strength": _pa(material.shear_strength),
        "shear_strength_approximate": material.shear_strength_approximate,
    }


def load_materials(path: Path | str = DEFAULT_MATERIALS_PATH) -> list[Material]:
    """Load every material from the JSON file, converted to MPa units.

    A file that can't be read, or whose records aren't materials, is
    quarantined and the list resets to empty rather than raising -- see
    `_RECOVERY_HINT` and eat.storage. A record that fails `Material`'s own
    validation (a negative modulus, say) counts as unreadable here: one
    bad row would otherwise take down every route that resolves a
    material, with no indication of which row."""
    records = read_json_or_recover(path, [], "material list", _RECOVERY_HINT)
    try:
        return [_record_to_material(r) for r in records]
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        quarantine(
            path,
            f"The material list file ({Path(path).name}) could not be read as a list of "
            f"materials and has been reset: {exc}.",
            _RECOVERY_HINT,
        )
        return []


def save_materials(materials: list[Material], path: Path | str = DEFAULT_MATERIALS_PATH) -> None:
    """Write the full material list back to the JSON file, converted to Pa.

    Atomically -- a half-written materials.json leaves the material
    dropdown empty and every analysis route erroring. See
    `eat.history.write_json_atomically`."""
    records = [_material_to_record(m) for m in materials]
    write_json_atomically(path, json.dumps(records, indent=2, ensure_ascii=False) + "\n")


def get_material(name: str, path: Path | str = DEFAULT_MATERIALS_PATH) -> Material:
    """Look up a single material by exact name."""
    for material in load_materials(path):
        if material.name == name:
            return material
    raise KeyError(f"No material named '{name}' in {path}")


def add_material(material: Material, path: Path | str = DEFAULT_MATERIALS_PATH) -> None:
    """Append a new material and persist. Raises if the name already exists."""
    materials = load_materials(path)
    if any(m.name == material.name for m in materials):
        raise ValueError(f"Material '{material.name}' already exists in {path}")
    materials.append(material)
    save_materials(materials, path)


def update_material(
    name: str, path: Path | str = DEFAULT_MATERIALS_PATH, **changes
) -> Material:
    """Update one or more fields of an existing material and persist."""
    materials = load_materials(path)
    for i, m in enumerate(materials):
        if m.name == name:
            updated = replace(m, **changes)
            materials[i] = updated
            save_materials(materials, path)
            return updated
    raise KeyError(f"No material named '{name}' in {path}")


def delete_material(name: str, path: Path | str = DEFAULT_MATERIALS_PATH) -> None:
    """Remove a material by name and persist. Raises if not found."""
    materials = load_materials(path)
    remaining = [m for m in materials if m.name != name]
    if len(remaining) == len(materials):
        raise KeyError(f"No material named '{name}' in {path}")
    save_materials(remaining, path)


def _print_table(materials: list[Material]) -> None:
    header = (
        f"{'Name':<40}{'rho(kg/m3)':>11}{'E(GPa)':>9}{'G(GPa)':>9}"
        f"{'nu':>6}{'Yield(MPa)':>12}{'UTS(MPa)':>10}{'Shear(MPa)':>12}"
    )
    print(header)
    print("-" * len(header))
    for m in materials:
        shear = "-" if m.shear_strength is None else f"{m.shear_strength:.0f}"
        if m.shear_strength is not None and m.shear_strength_approximate:
            shear += "~"
        print(
            f"{m.name:<40}"
            f"{(m.density if m.density is not None else float('nan')):>11.0f}"
            f"{(m.E / 1000):>9.2f}"
            f"{(m.G / 1000):>9.2f}"
            f"{m.nu:>6.2f}"
            f"{(m.yield_strength if m.yield_strength is not None else float('nan')):>12.0f}"
            f"{(m.ultimate_strength if m.ultimate_strength is not None else float('nan')):>10.0f}"
            f"{shear:>12}"
        )
    print("\n~ = approximate published figure")


def _add_material_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name")
    parser.add_argument("--density", type=float, help="kg/m^3")
    parser.add_argument("--e-gpa", type=float, help="elastic modulus, GPa")
    parser.add_argument("--g-gpa", type=float, help="shear modulus, GPa (derived from nu if omitted)")
    parser.add_argument("--nu", type=float, help="Poisson's ratio")
    parser.add_argument("--yield-mpa", type=float, help="yield strength, MPa")
    parser.add_argument("--uts-mpa", type=float, help="ultimate tensile strength, MPa")
    parser.add_argument("--shear-mpa", type=float, help="shear strength, MPa")
    parser.add_argument(
        "--shear-approximate", action="store_true", help="flag shear strength as approximate"
    )


def _prompt_float(label: str) -> float:
    while True:
        raw = input(f"{label}: ").strip()
        try:
            return float(raw)
        except ValueError:
            print("Please enter a number.")


def _material_from_add_args(args: argparse.Namespace) -> Material:
    name = args.name or input("Name: ").strip()
    density = args.density if args.density is not None else _prompt_float("Density (kg/m^3)")
    e_gpa = args.e_gpa if args.e_gpa is not None else _prompt_float("E (GPa)")
    nu = args.nu if args.nu is not None else _prompt_float("Poisson's ratio (nu)")
    yield_mpa = (
        args.yield_mpa if args.yield_mpa is not None else _prompt_float("Yield strength (MPa)")
    )
    uts_mpa = (
        args.uts_mpa
        if args.uts_mpa is not None
        else _prompt_float("Ultimate tensile strength (MPa)")
    )
    shear_mpa = (
        args.shear_mpa if args.shear_mpa is not None else _prompt_float("Shear strength (MPa)")
    )
    g_mpa = args.g_gpa * 1000 if args.g_gpa is not None else None

    return Material(
        name=name,
        E=e_gpa * 1000,
        nu=nu,
        G=g_mpa,
        yield_strength=yield_mpa,
        ultimate_strength=uts_mpa,
        shear_strength=shear_mpa,
        shear_strength_approximate=bool(args.shear_approximate),
        density=density,
    )


def _changes_from_edit_args(args: argparse.Namespace) -> dict:
    changes: dict = {}
    if args.name is not None:
        changes["name"] = args.name
    if args.density is not None:
        changes["density"] = args.density
    if args.e_gpa is not None:
        changes["E"] = args.e_gpa * 1000
    if args.g_gpa is not None:
        changes["G"] = args.g_gpa * 1000
    if args.nu is not None:
        changes["nu"] = args.nu
    if args.yield_mpa is not None:
        changes["yield_strength"] = args.yield_mpa
    if args.uts_mpa is not None:
        changes["ultimate_strength"] = args.uts_mpa
    if args.shear_mpa is not None:
        changes["shear_strength"] = args.shear_mpa
    if args.shear_approximate:
        changes["shear_strength_approximate"] = True
    return changes


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eat.materials", description="EAT material list."
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all materials.")

    p_add = sub.add_parser("add", help="Add a new material (prompts for anything not passed).")
    _add_material_args(p_add)

    p_edit = sub.add_parser("edit", help="Edit fields of an existing material.")
    p_edit.add_argument("current_name")
    _add_material_args(p_edit)

    p_del = sub.add_parser("delete", help="Delete a material by name.")
    p_del.add_argument("name")

    args = parser.parse_args(argv)
    command = args.command or "list"

    if command == "list":
        _print_table(load_materials())
    elif command == "add":
        material = _material_from_add_args(args)
        add_material(material)
        print(f"Added '{material.name}'.")
    elif command == "edit":
        changes = _changes_from_edit_args(args)
        if not changes:
            print("No fields given to change (pass e.g. --yield-mpa 250).")
            return 1
        updated = update_material(args.current_name, **changes)
        print(f"Updated '{updated.name}'.")
    elif command == "delete":
        delete_material(args.name)
        print(f"Deleted '{args.name}'.")

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
