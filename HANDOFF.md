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

Last commit: medial-axis wall thickness, narrow-notch detection, and local
(plate) buckling — pushed to `origin/main`. (Update this line with the
real hash once committed.)

All 12 spec build steps are done, plus backlog items: run history,
baseline comparison (dual-bar redesign), DFM/stiffness suggestions, PDF
engineering-drawing import, medial-axis thickness, narrow-notch detection,
and local (plate) buckling.

**In flight right now:** rendering the local-buckling results (already in
the `/beam` API response) in the frontend. Backend/API for all three
approved steps is committed; this is a direct, approved follow-up, not a
new feature under review.

- `eat/thickness.py` — medial-axis (max-inscribed-circle) thickness
  primitive, verified in `eat/verify_thickness.py` (25 checks). Both
  `thin_wall` and `thick_wall` suggestion checks run off it now; the old
  ray-cast sampler is gone.
- `eat/suggestions.py` — `narrow_notch` check added (9 fixtures in
  `verify_suggestions.py`). **Deliberately scoped narrower than the
  original backlog ask**: fires only when a notch leaves less than the
  ~1.0mm practical minimum wall, not on depth-ratio alone. A functional
  T-slot lip and an accidental slit are the same geometry — five
  discriminators were tried to tell them apart and none worked (see the
  check's docstring). Verified silent against the 7 confidential supplier
  PDFs in the project root (thinnest real candidate 1.476mm). **Do not
  raise the sensitivity without re-running those PDFs.**
- `eat/local_buckling.py` — per-wall plate buckling, reported alongside
  (not replacing) the existing Euler result, as `local_buckling` on the
  `/beam` response. k=4.0 (internal) / 0.425 (outstand), **both verified
  by numerically solving the plate eigenvalue problem** in
  `eat/verify_local_buckling.py` (27 checks), not looked up. Also
  classified per EN 1999-1-1 Table 6.2, cross-checked against a published
  7A04-T6 worked example (7.75/11.28/15.50) reproduced from the project's
  own 7075-T6 entry. Edge condition (internal vs. outstand) is classified
  per wall from the actual geometry, not assumed. README.md and the
  spec's "no local/plate buckling" v1 boundary are updated to match
  (explicit ask, not scope creep).
- **Frontend panel — in progress.** `frontend/index.html` /
  `frontend/app.js` / `frontend/styles.css`: a "Local (Plate) Buckling"
  table inside the Beam Results panel, between the summary tiles and the
  charts, visually separated (its own `panel__subtitle`) from the Euler
  summary tiles above rather than merged into them. Per wall: b, t, b/t,
  edge condition, k, σcr, EN 1999-1-1 class, applied stress, SF. Governing
  wall gets an accent left-border; a wall the model can't classify shows
  "Not classified" plus its caveat text instead of blank/misleading
  fields. Verify with Playwright against the 1.2mm-walled tube reference
  case (global SF 2.74, local SF 0.77) before calling this done.

**Working tree right now:**
- `extrusion-fea-tool-spec.md` has uncommitted local edits that are
  **Liam's own backlog notes** (a "Frontend sweep" section and further
  backlog restructuring) — **left out of the commit**, per the standing
  "leave these alone" rule. The scope-claim fix Liam asked for in step 3
  is folded into his own edits to that file now (both are in the working
  tree together) and wasn't cleanly separable, so it rides along
  uncommitted too. Liam can commit that file himself when he's done
  editing it, or ask for it to be split out.
- 8 untracked PDF files in the project root (`B18 - Tower - Extrusion -
  *.pdf`, `RDEX05120940 ...pdf`) — real supplier drawings Liam added for
  testing the PDF importer. Their title blocks mark them confidential.
  **Do not commit these to git** unless Liam explicitly asks. They are
  required for `eat/verify_pdf.py` and parts of `verify_api.py`/
  `verify_frontend.py` to run — those scripts skip/fail gracefully if the
  files are missing, but keep them in the working directory.

## Conventions (don't relitigate these)

- **Units**: mm / N / MPa / mm⁴ / N·mm² throughout the engine and every
  user-facing display; materials.json stores Pa/kg-m³ natively, converted
  at the `eat/materials.py` boundary only. Two deliberate, already-labeled
  exceptions: the materials CLI's `--e-gpa` input, and `eat.pdf_io`'s
  advisory mass-check note in g/cm³. Full table in `UNITS.md`.
- **Verification convention**: every backend module has a companion
  `eat/verify_X.py` with hand-calculable/cross-checked assertions, not
  "does it run." Run the full suite before any commit:
  ```
  for m in materials section dxf beam history baseline thickness \
           suggestions local_buckling pdf api; do
    python -m eat.verify_$m
  done
  python -m eat.verify_frontend   # Playwright, slower, needs `playwright install chromium`
  ```
- **"Frozen, not recomputed"**: a reloaded history entry always shows the
  exact stored result, never a live recompute — guarantees reproducibility.
- **"Always fresh"**: derived comparisons (solid-fill, baseline,
  suggestions) always recompute live, even for a reloaded history entry.
- Work style: implement → verify rigorously against known/hand-calculated
  values → run full suite → commit with a detailed message → push. Don't
  commit until the user has confirmed correctness for anything
  interpretation-heavy (the PDF importer's geometry reading was confirmed
  via a published visual-review artifact before committing).

## Known open items (not yet built)

- **Local-buckling frontend panel** — in progress, see above.
- **Notches that leave a manufacturable wall** — the part of the
  narrow-notch backlog item that is *not* solved. A slot leaving 1.5mm in
  a 4mm wall is a real stress riser and is not reported. Not solvable by
  tuning: needs a signal that distinguishes a designed slot from an
  accidental one, which local geometry does not carry. Candidates if ever
  worth revisiting: user-tagged functional features, or
  symmetry/repetition detection.
- **Release checklist** (do at actual v1 release, not before): rewrite
  `README.md`'s "v1 scope" framing into a real feature list.
- Liam's own **"Frontend sweep"** backlog (layout, unit simplification,
  suggestions panel placement, etc.) — see his notes in
  `extrusion-fea-tool-spec.md` (uncommitted). Not started.
