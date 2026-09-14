"""EAT local state storage — the JSON-file store shared by eat.history,
eat.materials and eat.baseline.

All three keep their state as a plain JSON file (see eat/history.py's
docstring for why a file over SQLite in this single-user, local-only
tool). This module owns the two properties all three need and none of
them had: writes that cannot be half-done, and a read that survives a
file that is already damaged.

WRITING
-------
`Path.write_text` truncates first and writes after, so anything that
interrupts it leaves a truncated file -- and README.md tells people to
stop the server with Ctrl+C. `write_json_atomically` writes to a temp
file in the same directory and then `os.replace`s it into position
(atomic on POSIX and on Windows), so a reader sees either the whole old
file or the whole new one, never a partial write.

READING
-------
Before `read_json_or_recover`, a damaged file meant a bare HTTP 500 on
every route that touched it -- /history, /section, /beam and /baseline
all at once -- with nothing in the UI to say why and no way back short of
finding and deleting the file by hand.

Now a store that fails to decode is QUARANTINED, not deleted: the damaged
file is renamed alongside itself with a `.corrupt-<timestamp>` suffix, the
store resets to its default, and a warning naming the backup path is
recorded for the UI to show. Two things about that are deliberate:

- **The reset is scoped to the one file that failed.** A corrupt history
  must not cost the user their material list.
- **The damaged bytes are kept.** They are the user's data; a hand-edit
  that broke the syntax is usually recoverable by eye, and silently
  deleting it would be the worse failure.

`eat.materials` is the awkward case: its "empty" default leaves the app
with no materials to analyze anything with. It still resets, because the
alternative is a dead server, but its warning says plainly that the list
needs restoring from the backup or re-entering -- see `store_warnings`.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Warnings raised while loading local state, newest last. Process-local
# and deliberately not persisted: they describe what happened to this
# server's files, and the frontend shows them once per session. Exposed
# over the API by GET /warnings.
_WARNINGS: list[str] = []


def store_warnings() -> list[str]:
    """Every store-recovery warning raised since the process started."""
    return list(_WARNINGS)


def clear_store_warnings() -> None:
    """Drop the recorded warnings — used by the verification suites so one
    test's induced corruption doesn't leak into the next."""
    _WARNINGS.clear()


def _warn(message: str) -> None:
    if message not in _WARNINGS:
        _WARNINGS.append(message)


def write_json_atomically(path: Path | str, text: str) -> None:
    """Replace a file's contents in one step that cannot be half-done.

    See the module docstring. Writing to a temp file in the same directory
    and then `os.replace` means a reader sees either the whole old file or
    the whole new one."""
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave the stray temp file behind on a failed write.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def quarantine(path: Path | str, reason: str, recovery_hint: str) -> str:
    """Move a damaged store aside and record a warning naming where it went.

    Returns the backup path. Separated from `read_json_or_recover` because
    a store can also be structurally wrong rather than syntactically
    invalid -- decodable JSON that isn't the shape the store expects --
    and its loader needs to quarantine it the same way."""
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, backup)
        moved = str(backup)
    except OSError:
        # Couldn't move it (permissions, vanished). Say so rather than
        # claiming a backup exists.
        moved = ""
    where = f" The damaged file was kept as '{moved}'." if moved else ""
    _warn(f"{reason}{where} {recovery_hint}".strip())
    return moved


def read_json_or_recover(
    path: Path | str,
    default: Any,
    label: str,
    recovery_hint: str,
) -> Any:
    """Load JSON from `path`, or quarantine it and return `default`.

    `label` names the store in the warning ("run history", "material
    list"); `recovery_hint` says what the user should do about it. A
    missing file is not an error -- a brand-new install has no history --
    and returns the default silently."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        quarantine(
            path,
            f"The {label} file ({path.name}) could not be read and has been reset: {exc}.",
            recovery_hint,
        )
        return default
