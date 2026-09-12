"""
EAT run history — JSON-backed log of completed section/beam analyses.

Every POST /section, /section/from-dxf, and /beam call (see eat.api)
appends a full, self-contained snapshot of that analysis -- profile,
material, inputs, and the *already-computed* result -- to
`eat/history.json`, a plain JSON file holding a list of entries. That's
the same storage pattern already used for `eat/materials.json`
(eat.materials), and deliberately so: this is a single-user, local-only
tool where a session's history tops out at a few hundred entries at
most, with no concurrent writers and (per the current spec) no
server-side search/filter/query requirement. A JSON file needs no
schema/migration machinery, is trivially inspectable or hand-edited, and
its "rewrite the whole list on every write" cost is irrelevant at this
scale. A real database (SQLite) would only start paying for itself with
concurrent writers or a need to query/filter/paginate server-side --
neither applies here; if this ever needs list-side filtering, sorting by
several fields, or many thousands of entries, that's the point to
reconsider, not before.

Reloading a history entry (eat.api's GET /history/{id}) replays its
*stored* result rather than recomputing it, so it stays accurate even if
the referenced material or section geometry is edited/deleted afterward.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_PATH = Path(__file__).parent / "history.json"

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


def load_history(path: Path | str = DEFAULT_HISTORY_PATH) -> list[HistoryEntry]:
    """Oldest first (append order). Returns [] if the file doesn't exist
    yet -- a brand-new install has no history, not an error."""
    p = Path(path)
    if not p.exists():
        return []
    records = json.loads(p.read_text())
    return [HistoryEntry(**r) for r in records]


def save_history(entries: list[HistoryEntry], path: Path | str = DEFAULT_HISTORY_PATH) -> None:
    Path(path).write_text(
        json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False) + "\n"
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
