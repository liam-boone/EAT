"""
EAT run history — JSON-backed log of completed section/beam analyses.

Every POST /section, /section/from-dxf, and /beam call (see eat.api)
appends a full, self-contained snapshot of that analysis -- profile,
material, inputs, and the *already-computed* result -- to
`eat/history.json`, a plain JSON file holding a list of entries. That's
the same storage pattern already used for `eat/materials.json`
(eat.materials), and deliberately so: this is a single-user, local-only
tool with no concurrent writers and (per the current spec) no
server-side search/filter/query requirement. A JSON file needs no
schema/migration machinery and is trivially inspectable or hand-edited.
A real database (SQLite) would only start paying for itself with
concurrent writers or a need to query/filter/paginate server-side --
neither applies here; if this ever needs list-side filtering, sorting by
several fields, or many thousands of entries, that's the point to
reconsider, not before.

The one thing that shape does cost is real, and it is why
`HISTORY_MAX_ENTRIES` exists: every write rewrites the whole list, so the
cost of an analysis grows with the log behind it. Measured at 0.39 ms per
stored entry, an uncapped log added 99 ms to every POST /section and
/beam at 250 entries and 392 ms at 1,000, with the file reaching 15 MB --
so "the rewrite cost is irrelevant at this scale" was only true while
the log stayed small, and nothing was keeping it small. The cap does.

Reloading a history entry (eat.api's GET /history/{id}) replays its
*stored* result rather than recomputing it, so it stays accurate even if
the referenced material or section geometry is edited/deleted afterward.
That guarantee is also why the cap drops whole entries rather than
trimming old ones down to summaries: a partial entry could not honour it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eat.storage import quarantine, read_json_or_recover, write_json_atomically

DEFAULT_HISTORY_PATH = Path(__file__).parent / "history.json"

# How many complete runs to keep. Older entries are DROPPED, not reduced
# to summaries -- a half-entry would break the "frozen, not recomputed"
# guarantee, since a reloaded run has to show its exact stored result.
#
# This is the one number to change if that trade-off should move, and
# nothing else needs touching. The cost it controls is real and linear:
# every POST /section and /beam rewrites the whole file, measured at
# 0.39 ms per stored entry, so an uncapped log added 99 ms to every
# analysis at 250 entries and 392 ms at 1,000, with the file reaching
# 15 MB. At 25 the overhead is about 10 ms and the file stays near 1 MB.
HISTORY_MAX_ENTRIES = 25

Vertex = tuple[float, float]


@dataclass
class HistoryEntry:
    """One completed analysis. `beam_request`/`beam_result` are None for a
    section-only entry (no beam analysis was run against this profile at
    the time this entry was saved)."""

    id: str
    created_at: str  # ISO 8601 UTC, e.g. "2026-09-12T18:04:33.512Z"
    material: str
    vertices: list[Vertex]
    holes: list[list[Vertex]] = field(default_factory=list)
    section_result: dict[str, Any] = field(default_factory=dict)
    beam_request: dict[str, Any] | None = None
    beam_result: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "material": self.material,
            "area": self.section_result.get("area"),
            "has_holes": len(self.holes) > 0,
            "has_beam": self.beam_result is not None,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


_RECOVERY_HINT = (
    "Run history has been reset to empty; nothing else is affected. Past runs in the "
    "backup can be recovered by hand if the file is repairable."
)


def load_history(path: Path | str = DEFAULT_HISTORY_PATH) -> list[HistoryEntry]:
    """Oldest first (append order). Returns [] if the file doesn't exist
    yet -- a brand-new install has no history, not an error.

    A file that exists but can't be read is quarantined and the store
    resets to empty rather than raising: see eat.storage. That covers
    both a file that isn't valid JSON and one that is, but whose records
    aren't entries (hand-edited into the wrong shape, or written by a
    future version)."""
    records = read_json_or_recover(path, [], "run history", _RECOVERY_HINT)
    try:
        return [HistoryEntry(**r) for r in records]
    except (TypeError, AttributeError) as exc:
        quarantine(
            path,
            f"The run history file ({Path(path).name}) is valid JSON but not a list of "
            f"history entries, and has been reset: {exc}.",
            _RECOVERY_HINT,
        )
        return []


def save_history(entries: list[HistoryEntry], path: Path | str = DEFAULT_HISTORY_PATH) -> None:
    write_json_atomically(
        path, json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False) + "\n"
    )


def add_entry(
    material: str,
    vertices: list[Vertex],
    holes: list[list[Vertex]] | None,
    section_result: dict[str, Any],
    beam_request: dict[str, Any] | None = None,
    beam_result: dict[str, Any] | None = None,
    path: Path | str = DEFAULT_HISTORY_PATH,
) -> HistoryEntry:
    entries = load_history(path)
    entry = HistoryEntry(
        id=uuid.uuid4().hex,
        created_at=_now_iso(),
        material=material,
        vertices=[tuple(v) for v in vertices],
        holes=[[tuple(v) for v in loop] for loop in (holes or [])],
        section_result=section_result,
        beam_request=beam_request,
        beam_result=beam_result,
    )
    entries.append(entry)
    # Keep only the most recent HISTORY_MAX_ENTRIES complete runs.
    if len(entries) > HISTORY_MAX_ENTRIES:
        entries = entries[-HISTORY_MAX_ENTRIES:]
    save_history(entries, path)
    return entry


def list_summaries(path: Path | str = DEFAULT_HISTORY_PATH) -> list[dict[str, Any]]:
    """Most-recent-first summaries for GET /history."""
    entries = load_history(path)
    return [e.summary() for e in reversed(entries)]


def get_entry(entry_id: str, path: Path | str = DEFAULT_HISTORY_PATH) -> HistoryEntry:
    for e in load_history(path):
        if e.id == entry_id:
            return e
    raise KeyError(f"No history entry with id '{entry_id}'")


def delete_entry(entry_id: str, path: Path | str = DEFAULT_HISTORY_PATH) -> None:
    entries = load_history(path)
    remaining = [e for e in entries if e.id != entry_id]
    if len(remaining) == len(entries):
        raise KeyError(f"No history entry with id '{entry_id}'")
    save_history(remaining, path)
