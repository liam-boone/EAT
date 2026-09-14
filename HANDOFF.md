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
commit `1f6fca6`). A second batch then acted on the judgment calls the
sweep had deliberately left alone — unsymmetric bending, the local-
buckling yield cap, history retention, corrupt-store recovery, a busy
indicator, shared profile validation, an LRU on the section cache, the
tensile-axial and zero-load reporting states, and the PDF `page=0` bug.
See that commit's message (`3b5f5ad`); the conventions it established
are below.

`README.md` was then rewritten from scratch to close the onboarding gaps
that sweep found: a real step-by-step install section (separate Mac/
Windows tracks, aimed at someone with no git/terminal/Python experience —
including which python.org button NOT to click, since it defaults to the
newest release rather than the 3.12 this project needs), the walkthrough
now covers PDF import, Design Review, History and Baseline comparison
(previously missing entirely), the "Section results appear immediately"
claim now matches reality (Area + Mass Per Length up front, the rest
under More Info), and the old "v1 scope" section — which read as a claim
that PDF import wasn't supported — is replaced by a "Current scope"
section listing what's actually supported today vs. genuinely out of
scope. Every claim in it (button labels, the 25-entry history cap, the
builtin baseline's name, CLI flag names, python.org's current default
download, git-scm.com's Windows URL) was checked against the live source
or the live page, not carried over from memory. Performance work
(mesh density) was explicitly left out of this pass.

Two findings worth carrying forward, both already fully written up where
the code lives rather than restated here:

- `min(Ixx, Iyy)` is **not** the weak axis unless `Ixy = 0` — see
  `eat/beam.py`'s module docstring and `_minimum_principal_i`. Any new
  check on a section property needs at least one asymmetric fixture; the
  existing Pcr-ratio test couldn't have caught this because it divides I
  out entirely.
- `sectionproperties`'s mesher doesn't validate its input (segfaults or
  hangs on bad geometry rather than raising) — see `eat/profile.py`'s
  module docstring for what that means for any new profile-consuming code.

Same treatment for two things from the frontend sweep:

- Design Review's DFM/Structural split is by what you *do* about a
  finding, not the physics — see the comment above `SUGGESTION_CATEGORIES`
  in `frontend/app.js`.
- `verify_frontend.py`'s history/baseline restore runs in a `finally`
  (see `state_snapshotted` in that file), closing the hole that once let
  a crashed run leave fixtures in Liam's real `eat/history.json`. Any
  throwaway driver script still needs its own snapshot/restore — the
  suite doesn't protect scripts that bypass it.

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
- `eat/history.json` has already settled at its `HISTORY_MAX_ENTRIES`
  cap of 25 (something wrote to it since the last handoff — likely a
  verify run). Those 25 are still leftover test fixtures from an earlier
  crashed debug run, not Liam's real usage; Liam confirmed they're not
  worth preserving, so no action needed. `eat/baseline.json` is at its
  correct default (`{"type": "builtin"}`).
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
- **One profile-validity contract**: `eat/profile.py`'s `validate_profile`
  is shared by the section, suggestions and local-buckling engines. Don't
  add a fourth definition, and don't "simplify" it to `polygon.is_valid` —
  its docstring says which real case that would break.
- **Local state lives behind `eat/storage.py`**: atomic writes, and a
  store that can't be decoded is quarantined (renamed aside, never
  deleted) and reset with a warning on `GET /warnings`, rather than
  500-ing every route. New JSON-backed state should go through it.
- **Verification scripts snapshot the history FILE, not its entry ids.**
  With `HISTORY_MAX_ENTRIES` in force, entries a test adds *evict* the
  user's oldest runs, and deleting the new ones afterwards cannot bring
  those back. `verify_api.py` and `verify_frontend.py` both restore bytes.
- **"Frozen, not recomputed"**: a reloaded history entry always shows the
  exact stored result, never a live recompute — guarantees reproducibility.
  It is also why `HISTORY_MAX_ENTRIES` drops whole entries rather than
  trimming old ones to summaries.
- **"Always fresh"**: derived comparisons (solid-fill, baseline,
  suggestions) always recompute live, even for a reloaded history entry.
- Work style: implement → verify rigorously against known/hand-calculated
  values → run full suite → commit with a detailed message → push. Don't
  commit until the user has confirmed correctness for anything
  interpretation-heavy (visual/layout changes, PDF geometry reading).

## Known open items (not yet built)

- **Mesh density tracks the bounding box, not feature size** — deferred
  from the QA sweep's batch as a performance-engineering task in its own
  right. `analyze_section` picks `mesh_size = bbox_area / 2000`, which
  fixes the element count rather than scaling with the features that need
  resolving. Measured consequence: a plain 4-vertex 50x100 rectangle takes
  1.15 s while the 246-vertex KJN profile takes 0.96 s, and a real
  929-vertex supplier profile takes 3.35 s — the dominant term in the ~5 s
  a full pass costs. A busy indicator now covers the symptom; the fix is
  to size elements from local wall thickness instead. Re-measure before
  and after, on the supplier PDFs in the project root.
- **Repo visibility** — `git clone https://github.com/liam-boone/EAT.git`
  still 404s unauthenticated (confirmed again while rewriting the README;
  `git ls-remote` succeeds with Liam's own credentials, so the repo just
  isn't public). README.md's install steps are written as if a reader can
  reach that URL — they're correct once the repo is public or the reader
  has collaborator access, but not before. This is Liam's call and he's
  deciding it separately from the doc work; not something to act on here.

- **Notches that leave a manufacturable wall** — the part of the
  narrow-notch backlog item that is *not* solved. A slot leaving 1.5mm in
  a 4mm wall is a real stress riser and is not reported. Not solvable by
  tuning: needs a signal that distinguishes a designed slot from an
  accidental one, which local geometry does not carry. Candidates if ever
  worth revisiting: user-tagged functional features, or
  symmetry/repetition detection.
- **Right-column order** — "Analyze Beam" sits at the bottom of the right
  column, below the baseline comparison, so on a 16:9 screen it's below
  the fold (the click scrolls Beam Results into view, which covers the
  "nothing happened" symptom but not the fold itself). Fixing it properly
  means putting Length & Loads above the baseline comparison, which
  wasn't done this pass since Liam asked for that block's position to
  stay put. Worth raising with him.
