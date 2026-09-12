"""
Verification harness for the run-history store (build step 9).

Exercises eat.history directly (add/list/get/delete) against an isolated
temp file -- unlike eat.verify_materials, which tests eat.materials
against the real seeded materials.json and restores it afterward, this
uses a throwaway path (every eat.history function accepts one) so it
never touches the real eat/history.json a user's actual runs would be
in. The HTTP-level wiring (POST /section, /section/from-dxf, and /beam
each appending a history entry; GET/DELETE /history) is covered
separately in eat/verify_api.py, against the real file with cleanup.

Run with: python -m eat.verify_history
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from eat.history import add_entry, delete_entry, get_entry, list_summaries, load_history


@dataclass
class Check:
    label: str
    passed: bool
    detail: str = ""


def _norm(vertices) -> list[tuple]:
    """Normalize a vertices/holes structure to tuples for comparison --
    a JSON round-trip turns tuples into lists, and list([0, 0]) != (0, 0)
    in Python even though the content is identical."""
    return [tuple(v) for v in vertices]


def main() -> int:
    checks: list[Check] = []

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "history.json"

        # --- Empty store ---
        checks.append(Check("Nonexistent history file loads as empty list", load_history(path) == []))
        checks.append(Check("Nonexistent history file summarizes as empty list", list_summaries(path) == []))

        # --- Add a section-only entry ---
        rect = [(0, 0), (50, 0), (50, 100), (0, 100)]
        section_result = {
            "material": "6061-T6 Aluminum (Extruded)",
            "area": 5000.0,
            "ixx": 4166666.6666666665,
            "iyy": 1041666.6666666666,
            "mass_per_length": 13.5,
        }
        entry1 = add_entry(
            material="6061-T6 Aluminum (Extruded)",
            vertices=rect,
            holes=None,
            section_result=section_result,
            path=path,
        )
        checks.append(Check("add_entry returns an entry with a non-empty id", bool(entry1.id)))
        checks.append(
            Check(
                "add_entry returns an entry with an ISO-ish timestamp",
                entry1.created_at.endswith("Z") and "T" in entry1.created_at,
                entry1.created_at,
            )
        )

        # --- Add a section+beam entry with holes ---
        hole = [(15, 40), (35, 40), (35, 60), (15, 60)]
        beam_request = {
            "length": 1000.0,
            "boundary_condition": "simply_supported",
            "point_loads": [{"position_fraction": 0.5, "magnitude": -1000.0, "axis": "y"}],
            "axial_load": None,
        }
        beam_result = {"max_moment": -250000.0, "max_deflection": -25.17, "load_axis": "y"}
        entry2 = add_entry(
            material="Test Steel",
            vertices=rect,
            holes=[hole],
            section_result={**section_result, "material": "Test Steel", "area": 4600.0},
            beam_request=beam_request,
            beam_result=beam_result,
            path=path,
        )

        # --- Ordering: list_summaries is most-recent-first ---
        summaries = list_summaries(path)
        checks.append(Check("list_summaries has both entries", len(summaries) == 2, f"{len(summaries)} entries"))
        checks.append(
            Check(
                "list_summaries is most-recent-first",
                summaries[0]["id"] == entry2.id and summaries[1]["id"] == entry1.id,
                f"order={[s['id'] for s in summaries]}",
            )
        )

        # --- Summary fields ---
        s1 = next(s for s in summaries if s["id"] == entry1.id)
        s2 = next(s for s in summaries if s["id"] == entry2.id)
        checks.append(
            Check(
                "Section-only entry's summary: no holes, no beam",
                s1["has_holes"] is False and s1["has_beam"] is False and s1["area"] == 5000.0,
                f"{s1}",
            )
        )
        checks.append(
            Check(
                "Section+beam entry's summary: has holes, has beam",
                s2["has_holes"] is True and s2["has_beam"] is True and s2["area"] == 4600.0,
                f"{s2}",
            )
        )
        checks.append(
            Check(
                "Summaries carry the material name for at-a-glance identification",
                s1["material"] == "6061-T6 Aluminum (Extruded)" and s2["material"] == "Test Steel",
            )
        )

        # --- get_entry: full round trip, not just the summary ---
        fetched1 = get_entry(entry1.id, path)
        checks.append(
            Check(
                "get_entry round-trips vertices exactly",
                _norm(fetched1.vertices) == _norm(rect),
                f"{fetched1.vertices}",
            )
        )
        checks.append(
            Check(
                "get_entry round-trips the full section_result dict exactly (not recomputed)",
                fetched1.section_result == section_result,
                f"{fetched1.section_result}",
            )
        )
        checks.append(Check("Section-only entry has no beam_request/beam_result", fetched1.beam_request is None and fetched1.beam_result is None))

        fetched2 = get_entry(entry2.id, path)
        checks.append(
            Check(
                "get_entry round-trips holes exactly",
                [_norm(h) for h in fetched2.holes] == [_norm(hole)],
                f"{fetched2.holes}",
            )
        )
        checks.append(
            Check(
                "get_entry round-trips beam_request/beam_result exactly (not recomputed)",
                fetched2.beam_request == beam_request and fetched2.beam_result == beam_result,
                f"beam_request={fetched2.beam_request}, beam_result={fetched2.beam_result}",
            )
        )

        # --- get_entry: unknown id ---
        try:
            get_entry("does-not-exist", path)
            checks.append(Check("get_entry: unknown id raises KeyError", False))
        except KeyError:
            checks.append(Check("get_entry: unknown id raises KeyError", True))

        # --- delete_entry ---
        delete_entry(entry1.id, path)
        remaining = list_summaries(path)
        checks.append(
            Check(
                "delete_entry removes exactly the targeted entry",
                len(remaining) == 1 and remaining[0]["id"] == entry2.id,
                f"remaining={remaining}",
            )
        )

        try:
            delete_entry(entry1.id, path)  # already deleted
            checks.append(Check("delete_entry: unknown id raises KeyError", False))
        except KeyError:
            checks.append(Check("delete_entry: unknown id raises KeyError", True))

        try:
            get_entry(entry1.id, path)
            checks.append(Check("get_entry: deleted id raises KeyError", False))
        except KeyError:
            checks.append(Check("get_entry: deleted id raises KeyError", True))

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
