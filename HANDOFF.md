# Handoff — Extrusion Analysis Tool (EAT)

Read this, then `README.md` and `UNITS.md` for the rest. Repo is a
FastAPI backend (`eat/`) + vanilla JS frontend (`frontend/`), no build step.

## Keeping this file current (instructions for the assistant)

This file exists so Liam can clear the chat at any point and a fresh
session can resume without re-reading the whole repo. Keep it that way:

- **Update it as work lands, not at the end of a session.** After each
  finished unit of work (a built + verified feature, a decision made, a
  new constraint), edit the affected section here in the same turn.
- **Facts that aren't in the code or git history only.** Conventions,
  decisions and their reasons, in-flight state, things not to do. Never
  duplicate what a module docstring already says — point at it instead.
- **Stay short.** This is a map, not a record. Prefer replacing a stale
  line over appending a new one; delete anything that has stopped being
  true. If it's growing past ~100 lines, something in it belongs in a
  module docstring or `UNITS.md` instead.
- **Uncommitted/untracked state is the most valuable thing here**, since
  git won't show it to the next session. Keep that section exact.

## State as of this handoff

All 12 spec build steps are done, plus every backlog item: run history,
baseline comparison, DFM/stiffness suggestions, PDF engineering-drawing
import, medial-axis thickness, narrow-notch detection, local (plate)
buckling, and the frontend layout/clarity sweep — backend and frontend
both shipped, verified, committed, pushed to `origin/main`.

A **full QA sweep** (accuracy / reliability / performance / cold-start
onboarding) then ran over the whole project. It re-derived every core
engineering claim by a method *different* from the one that originally
verified it — an independent direct-stiffness FEM for the beam engine,
exact Green's-theorem integrals for section properties, Rayleigh-Ritz
energy minimisation for the plate-buckling coefficients, and bisection
for the medial-axis thickness. Those four engines came back clean.

Seven unambiguous bugs it *did* find are fixed and committed (F1–F7 in
the commit message). The two worth carrying forward as knowledge:

- **`min(Ixx, Iyy)` is not the weak axis.** It only coincides with the
  least principal second moment when `Ixy = 0`. Euler buckling used it
  and overstated Pcr by 1.92x on the project's own L-angle fixture. The
  existing test missed it because it only compared Pcr *ratios* between
  boundary conditions, which divides I out entirely. Any new check on a
  section property needs at least one asymmetric fixture.
- **`sectionproperties` meshes through a C library that does not
  validate its input** — handed a self-intersecting ring, a NaN vertex or
  a hairline sliver it segfaults or hangs rather than raising, and in the
  server that kills the worker. `eat.section._validate_profile` is a hard
  prerequisite, not a nicety; see its docstring for why it deliberately
  is not a bare `polygon.is_valid` test.

Two things from the frontend sweep worth knowing without re-reading the
commit:

- **Design review (DFM/Structural) is split by what you *do* about a
  finding**, not by the physics the check used — sharp corners are DFM
  despite being a stress riser, because the fix is a manufacturing change
  either way. See `suggestionCategory` in `frontend/app.js`.
- **`verify_frontend.py`'s history/baseline restore runs in a `finally`**
  now, closing the hole that let a crashed test run leave fixtures in
  Liam's real `eat/history.json` (see "Working tree" below — that
  incident is why this file's restore discipline exists at all). Any
  throwaway driver script still needs to snapshot/restore both files
  itself; the suite script does not protect scripts that bypass it.

## Working tree right now

- `extrusion-fea-tool-spec.md` has uncommitted local edits that are
  **Liam's own notes** — left out of every commit, per standing
  instruction. Liam commits that file himself when ready, or asks for it
  to be split out.
- 8 untracked PDF files in the project root (`B18 - Tower - Extrusion -
  *.pdf`, `RDEX05120940 ...pdf`) — real supplier drawings, confidential
  per their title blocks. **Do not commit these.** Required for
  `eat/verify_pdf.py` and parts of `verify_api.py`/`verify_frontend.py`
  (those skip/fail gracefully if missing) — keep them in the working
  directory.
- `eat/history.json` carries 44 test-fixture entries from an earlier
  crashed debug run (not Liam's real usage — all same-day, all match
  automated-test geometry/materials). Left untouched since it's Liam's
  runtime data; wipe from the History panel or delete the file if he
  wants it cleared. `eat/baseline.json` is at its correct default
  (`{"type": "builtin"}`).
- `EAT-audit-sheet.html` in the project root — the QA sweep's findings
  as a standalone page, written for Liam to read. Untracked on purpose;
  delete it once read.

## Conventions (don't relitigate these)

- **Units**: mm / N / MPa / mm⁴ / N·mm² throughout the engine and every
  user-facing display; materials.json stores Pa/kg-m³ natively, converted
  at the `eat/materials.py` boundary only. Full table, plus the display-
  only specific-stiffness/strength rescale in the baseline panel, in
  `UNITS.md`.
- **Verification convention**: every backend module has a companion
  `eat/verify_X.py` with hand-calculable/cross-checked assertions, not
  "does it run." There is exactly ONE frontend Playwright script,
  `eat/verify_frontend.py` — new frontend behavior gets a new flow (or
  checks appended to an existing flow) inside that file, not a new
  script. It restores `eat/history.json` and `eat/baseline.json` to their
  pre-run state (from a `finally`, so a crash mid-flow doesn't skip it)
  and writes two screenshots (a 16:9 one at the given path, `_tablet`
  beside it). Run the full suite before any commit:
  ```
  for m in materials section dxf beam history baseline thickness \
           suggestions local_buckling pdf api; do
    python -m eat.verify_$m
  done
  python -m eat.verify_frontend   # Playwright, slower, needs `playwright install chromium`
  ```
  A throwaway Playwright script outside this suite must snapshot/restore
  `eat/history.json` and `eat/baseline.json` itself — the suite's own
  discipline doesn't cover scripts that bypass it.
- **"Frozen, not recomputed"**: a reloaded history entry always shows the
  exact stored result, never a live recompute — guarantees reproducibility.
- **"Always fresh"**: derived comparisons (solid-fill, baseline,
  suggestions) always recompute live, even for a reloaded history entry.
- Work style: implement → verify rigorously against known/hand-calculated
  values → run full suite → commit with a detailed message → push. Don't
  commit until the user has confirmed correctness for anything
  interpretation-heavy (visual/layout changes, PDF geometry reading).

## Known open items (not yet built)

- **Notches that leave a manufacturable wall** — the part of the
  narrow-notch backlog item that is *not* solved. A slot leaving 1.5mm in
  a 4mm wall is a real stress riser and is not reported. Not solvable by
  tuning: needs a signal that distinguishes a designed slot from an
  accidental one, which local geometry does not carry. Candidates if ever
  worth revisiting: user-tagged functional features, or
  symmetry/repetition detection.
- **Release checklist** (do at actual v1 release, not before): rewrite
  `README.md`'s "v1 scope" framing into a real feature list.
- **Right-column order** — "Analyze Beam" sits at the bottom of the right
  column, below the baseline comparison, so on a 16:9 screen it's below
  the fold (the click scrolls Beam Results into view, which covers the
  "nothing happened" symptom but not the fold itself). Fixing it properly
  means putting Length & Loads above the baseline comparison, which
  wasn't done this pass since Liam asked for that block's position to
  stay put. Worth raising with him.
