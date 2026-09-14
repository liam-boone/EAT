"""
EAT baseline selection — persisted "compare against" setting.

A section/beam analysis can be compared against a reference "baseline":
either a fixed built-in profile (the 20x40 KJN-series extrusion, computed
live from its already-committed DXF fixture and its real material -- not
hardcoded numbers) or any entry from eat.history, chosen via "Set as
Baseline" in the frontend's History panel. The *choice itself* is a tiny
piece of local settings state, persisted the same JSON-file way
eat.materials/eat.history already persist their own state -- see
eat/history.py's docstring for why a JSON file over SQLite in this
single-user, local-only tool; the reasoning is identical, and this file
holds a single small object rather than even a list.

If the currently-selected history entry is later deleted, resolving the
baseline falls back to the built-in profile rather than erroring -- a
stale reference shouldn't silently break every subsequent comparison.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eat import history
from eat.dxf_io import import_polygon_from_bytes
from eat.materials import get_material
from eat.section import analyze_section
from eat.storage import quarantine, read_json_or_recover, write_json_atomically

DEFAULT_BASELINE_SETTING_PATH = Path(__file__).parent / "baseline.json"
BUILTIN_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "20X40_KJN992891.dxf"
BUILTIN_MATERIAL_NAME = "6063-T6 Aluminum (Extruded)"
BUILTIN_NAME = "20x40 KJN-series (built-in)"

Vertex = tuple[float, float]


@dataclass
class BaselineInfo:
    source: str  # "builtin" or "history"
    name: str
    material: str
    vertices: list[Vertex]
    holes: list[list[Vertex]]
    section_result: dict[str, Any]
    history_entry_id: str | None = None


_DEFAULT_SETTING = {"type": "builtin"}
_RECOVERY_HINT = (
    "The baseline has been reset to the built-in 20x40 KJN profile; pick another from "
    "the History panel if you had one selected."
)


def get_baseline_setting(path: Path | str = DEFAULT_BASELINE_SETTING_PATH) -> dict[str, Any]:
    """The persisted selection, defaulting to the built-in profile if
    nothing has been chosen yet (including on a brand-new install).

    A damaged file resets to the built-in rather than raising -- this is
    a one-line setting, and 500-ing every comparison over it would be
    absurd. Anything that isn't a JSON object counts as damaged: the
    setting is read with `.get`, so a list or a bare number would
    otherwise raise an AttributeError deeper in."""
    setting = read_json_or_recover(path, dict(_DEFAULT_SETTING), "baseline selection", _RECOVERY_HINT)
    if not isinstance(setting, dict):
        quarantine(
            path,
            f"The baseline selection file ({Path(path).name}) is not a settings object "
            f"and has been reset.",
            _RECOVERY_HINT,
        )
        return dict(_DEFAULT_SETTING)
    return setting


def set_baseline_setting(setting: dict[str, Any], path: Path | str = DEFAULT_BASELINE_SETTING_PATH) -> None:
    """Atomically, so an interrupted write can't leave a file that makes
    every subsequent baseline lookup raise. See
    `eat.storage.write_json_atomically`."""
    write_json_atomically(path, json.dumps(setting, indent=2, ensure_ascii=False) + "\n")


_builtin_cache: BaselineInfo | None = None


def _compute_builtin_baseline() -> BaselineInfo:
    """Computed once per process from the real DXF fixture + material
    (not hardcoded figures), then cached -- the fixture and material are
    fixed/shipped, so nothing about this result can change mid-process."""
    global _builtin_cache
    if _builtin_cache is not None:
        return _builtin_cache
    raw = BUILTIN_FIXTURE_PATH.read_bytes()
    dxf_result = import_polygon_from_bytes(raw, source_label=BUILTIN_FIXTURE_PATH.name)
    material = get_material(BUILTIN_MATERIAL_NAME)
    result = analyze_section(dxf_result.vertices, material, holes=dxf_result.holes)
    _builtin_cache = BaselineInfo(
        source="builtin",
        name=BUILTIN_NAME,
        material=material.name,
        vertices=dxf_result.vertices,
        holes=dxf_result.holes,
        section_result=result.as_dict(),
    )
    return _builtin_cache


def resolve_baseline(
    setting_path: Path | str = DEFAULT_BASELINE_SETTING_PATH,
    history_path: Path | str = history.DEFAULT_HISTORY_PATH,
) -> BaselineInfo:
    """The currently-active baseline, ready to compare against."""
    setting = get_baseline_setting(setting_path)
    if setting.get("type") == "history":
        entry_id = setting.get("entry_id")
        try:
            entry = history.get_entry(entry_id, history_path)
            return BaselineInfo(
                source="history",
                name=f"{entry.material} — {entry.created_at}",
                material=entry.material,
                vertices=entry.vertices,
                holes=entry.holes,
                section_result=entry.section_result,
                history_entry_id=entry.id,
            )
        except KeyError:
            pass  # referenced entry was deleted -- fall back to the built-in
    return _compute_builtin_baseline()
