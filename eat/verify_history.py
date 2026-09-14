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

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from eat.history import (
    HISTORY_MAX_ENTRIES,
    add_entry,
    delete_entry,
    get_entry,
    list_summaries,
    load_history,
)
from eat.storage import clear_store_warnings, store_warnings, write_json_atomically


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

    checks += _atomic_write_checks()
    checks += _retention_cap_checks()
    checks += _recovery_checks()
    return _report(checks)


def _retention_cap_checks() -> list[Check]:
    """The log keeps only the most recent HISTORY_MAX_ENTRIES runs.

    Uncapped, every write rewrote the whole file, so the cost of an
    analysis grew with the log behind it -- 0.39 ms per stored entry,
    which reached 392 ms added to every POST /section and /beam at 1,000
    entries, with the file at 15 MB. The cap drops WHOLE entries rather
    than trimming old ones to summaries, because a partial entry could
    not honour the "frozen, not recomputed" guarantee."""
    checks: list[Check] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "history.json"
        made = [
            add_entry("6061", [(0, 0), (i + 3, 0), (i + 3, 5)], None, {"area": float(i)}, path=path)
            for i in range(HISTORY_MAX_ENTRIES + 12)
        ]
        entries = load_history(path)
        checks.append(
            Check(
                f"Retention: the log stops growing at HISTORY_MAX_ENTRIES ({HISTORY_MAX_ENTRIES})",
                len(entries) == HISTORY_MAX_ENTRIES,
                f"{len(entries)} entries after {len(made)} adds",
            )
        )
        checks.append(
            Check(
                "Retention: it is the OLDEST entries that are dropped",
                [e.id for e in entries] == [m.id for m in made[-HISTORY_MAX_ENTRIES:]],
            )
        )
        checks.append(
            Check(
                "Retention: the newest run is always kept",
                entries[-1].id == made[-1].id,
            )
        )
        # Survivors must be whole -- not stripped-down summaries.
        oldest_kept = made[-HISTORY_MAX_ENTRIES]
        fetched = get_entry(oldest_kept.id, path)
        checks.append(
            Check(
                "Retention: surviving entries are complete, not summarised",
                fetched.section_result == oldest_kept.section_result
                and _norm(fetched.vertices) == _norm(oldest_kept.vertices),
            )
        )
        checks.append(
            Check(
                "Retention: an evicted entry is genuinely gone",
                not any(e.id == made[0].id for e in entries),
            )
        )
    return checks


def _recovery_checks() -> list[Check]:
    """A damaged store resets instead of 500-ing every endpoint.

    Before this, a history.json that failed to decode raised out of
    /history, /section, /beam and /baseline alike, with nothing in the UI
    to say why and no way back short of deleting the file by hand. Now the
    file is QUARANTINED -- renamed aside, never deleted, since it is the
    user's data -- the store resets, and a warning naming the backup is
    recorded for the UI."""
    checks: list[Check] = []
    cases = {
        "truncated mid-write": lambda good: good[: len(good) // 2],
        "empty file": lambda good: "",
        "not JSON at all": lambda good: "\x00\x00garbage",
        "valid JSON, wrong shape (an object)": lambda good: '{"entries": []}',
        "valid JSON, wrong shape (list of numbers)": lambda good: "[1, 2, 3]",
        "records missing a required field": lambda good: '[{"id": "x"}]',
    }
    for label, damage in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            add_entry("6061", [(0, 0), (9, 0), (9, 9)], None, {"area": 40.5}, path=path)
            good = path.read_text()
            path.write_text(damage(good))
            clear_store_warnings()

            try:
                entries = load_history(path)
                raised = None
            except Exception as exc:  # noqa: BLE001
                entries, raised = None, exc

            checks.append(
                Check(
                    f"Recovery ({label}): loads without raising",
                    raised is None,
                    f"{type(raised).__name__}: {raised}" if raised else "",
                )
            )
            if raised is not None:
                continue
            checks.append(Check(f"Recovery ({label}): resets to an empty log", entries == []))
            backups = [p for p in Path(tmp).iterdir() if ".corrupt-" in p.name]
            checks.append(
                Check(
                    f"Recovery ({label}): the damaged file is kept, not deleted",
                    len(backups) == 1,
                    f"{[p.name for p in Path(tmp).iterdir()]}",
                )
            )
            warnings = store_warnings()
            checks.append(
                Check(
                    f"Recovery ({label}): a warning names the backup",
                    bool(warnings) and any(backups[0].name in w for w in warnings if backups),
                    f"{warnings}",
                )
            )
            # ...and the store is usable again immediately afterwards.
            add_entry("6063", [(0, 0), (4, 0), (4, 4)], None, {"area": 8.0}, path=path)
            checks.append(
                Check(f"Recovery ({label}): the store works again afterwards", len(load_history(path)) == 1)
            )
    clear_store_warnings()
    return checks


def _atomic_write_checks() -> list[Check]:
    """Saving must never be able to leave a half-written file behind.

    `Path.write_text` truncates first and writes after, so a Ctrl+C during
    a save -- which is exactly how README.md tells people to stop the
    server -- left a truncated history.json. Every reader then raised
    JSONDecodeError, which surfaced as a bare HTTP 500 on /history,
    /section, /beam and /baseline alike, with nothing in the UI to say why
    or how to recover, and the file reaches several megabytes after a few
    hundred runs so the window is not small.

    The test is behavioural, not a source inspection: interrupt the write
    at the moment the bytes are being produced and confirm the file on
    disk is still the intact PREVIOUS version, and that no temp file is
    left lying next to it."""
    checks: list[Check] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "history.json"
        first = add_entry("6061", [(0, 0), (10, 0), (10, 10)], None, {"area": 50.0}, path=path)
        before = path.read_text()
        new_text = before[:-1] + ',\n  {"extra": true}\n'

        # (a) While the new content is being written, the file at `path` must
        # still hold the whole OLD version -- that is what "atomic" means
        # here, and it is the property `Path.write_text` does not have.
        # os.replace is the last step, so the state observed there is the
        # state a concurrent reader (or a Ctrl+C) would have found.
        seen_midwrite: list[str] = []
        real_replace = os.replace

        def spy_replace(src, dst):
            seen_midwrite.append(Path(dst).read_text())
            return real_replace(src, dst)

        os.replace = spy_replace
        try:
            write_json_atomically(path, new_text)
        finally:
            os.replace = real_replace
        checks.append(
            Check(
                "atomic write: target still held the intact old file while the new one was written",
                seen_midwrite == [before],
                f"{len(seen_midwrite[0]) if seen_midwrite else 0} bytes mid-write, "
                f"{len(before)} before",
            )
        )
        checks.append(Check("atomic write: the new content did land", path.read_text() == new_text))

        # (b) A save interrupted before it completes must leave the previous
        # file untouched and no temp file behind -- the Ctrl+C case.
        path.write_text(before)

        def boom_replace(src, dst):
            raise KeyboardInterrupt("simulated Ctrl+C during save")

        os.replace = boom_replace
        try:
            write_json_atomically(path, new_text)
            interrupted = False
        except KeyboardInterrupt:
            interrupted = True
        finally:
            os.replace = real_replace

        checks.append(Check("atomic write: an interrupted save propagates the interrupt", interrupted))
        checks.append(
            Check(
                "atomic write: after the interrupt the file is the intact previous version",
                path.read_text() == before and _parses(path),
                f"{len(path.read_text())} bytes, parses={_parses(path)}",
            )
        )
        checks.append(
            Check(
                "atomic write: no temp file left beside it",
                sorted(p.name for p in Path(tmp).iterdir()) == ["history.json"],
                str(sorted(p.name for p in Path(tmp).iterdir())),
            )
        )
        # ...and the store still works normally afterwards
        add_entry("6063", [(0, 0), (5, 0), (5, 5)], None, {"area": 12.5}, path=path)
        entries = load_history(path)
        checks.append(
            Check(
                "atomic write: the store is still usable after the interrupted save",
                len(entries) == 2 and entries[0].id == first.id,
                f"{len(entries)} entries",
            )
        )
    return checks


def _parses(path: Path) -> bool:
    try:
        json.loads(path.read_text())
        return True
    except Exception:  # noqa: BLE001
        return False


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
