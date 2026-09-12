# Extrusion Analysis Tool (EAT) — v1 Spec

## Working conventions
- Project name: **Extrusion Analysis Tool**. Short form **EAT** for filenames,
  package names, and any customer-facing titles/UI text.
- **Canary rule**: at the start of every response in this project, say "Liam"
  first, before anything else. This is a check that this spec file is being
  read and followed — if a response doesn't start with it, the spec isn't
  being consulted properly.

## Purpose
Quick-turnaround tool for sizing/optimizing extruded (aluminum, plastic) profiles:
section properties, and — when length + load case are given — stress, deflection,
safety factor, and global (Euler) buckling. Not a full FEA solver; a fast
engineering-formula tool built on real section geometry.

## Stack
- **Backend**: Python
  - `sectionproperties` — section geometry FE analysis (area, Ixx, Iyy, J, warping,
    centroid, shear centre, plastic moduli, stress recovery)
  - `ezdxf` — DXF import/export for profile geometry
  - `FastAPI` — local web server / API
- **Frontend**: single-page HTML/JS app served by the backend, `<canvas>`-based
  polygon sketcher + Plotly/Matplotlib-rendered results (section plot, stress plot,
  deflection curve)
- **Distribution**: pure Python + browser, no packaging needed since Python is
  preinstalled for target users. One `requirements.txt` + one run script
  (`run.sh` / `run.bat`) that starts the server and opens the browser. Runs
  identically on Mac and Windows — no OS-specific code needed anywhere in this
  stack.

## Core workflow
1. **Define profile**
   - Click-to-place vertices to build a closed polygon (simple case: single
     solid/open profile — no multi-cell cavities in v1)
   - OR import a `.dxf` file (via `ezdxf`) and preview it before confirming
   - Export current sketch to `.dxf`
2. **Assign material**
   - Pulled from an editable list (start as a JSON/CSV file: name, E, G or ν,
     yield strength, ultimate strength, density)
   - User can add/edit entries from the GUI; changes persist back to the file
3. **Section-only output** (no length/load required)
   - Area, Ixx, Iyy, J (torsion constant), centroid, shear centre
   - EI, EA, GJ (stiffness per unit length)
   - Section modulus, yield moment, plastic moment (from `sectionproperties`)
   - All reported as "per unit length" metrics so profiles are comparable
     independent of a specific application
4. **Length + load case** (optional, unlocks structural checks)
   - Inputs: length, **boundary condition**, one or more point loads
     (position as fraction of length, magnitude, direction)
   - **Boundary condition options**: fixed–fixed (default), fixed–free
     (cantilever), simply supported, fixed–pinned
   - Outputs: max bending stress, max deflection, reaction forces, safety
     factor (yield stress / max stress), Euler buckling load + buckling
     safety factor
   - Local/plate buckling explicitly out of scope for v1

## Explicit v1 boundaries (per discussion)
- DXF only (no DWG — proprietary format, no viable open library)
- No local/plate buckling — global Euler buckling only
- Single closed/open polygon profiles — no multi-material composite sections,
  no built-up/multi-cell extrusions in v1
- Point loads only in v1 (no distributed loads yet)
- Boundary condition is user-selectable, default fixed–fixed

## Suggested build order (for Claude Code)
1. Section engine: wrap `sectionproperties` — polygon-in, properties-out,
   as a plain Python function/class, testable from the command line first
2. Material list: JSON file + loader/editor functions
3. Beam engine: given section props + L + BC + point loads → stress,
   deflection, safety factor, Euler buckling (standard closed-form beam
   equations per BC case)
4. DXF import/export wrapper around `ezdxf`
5. FastAPI endpoints wrapping 1–4
6. Frontend: canvas sketcher → results panel, wired to the API last

## Future / backlog (post-v1)
- **Run history**: since the tool is mainly for comparing iterations, keep a
  history of past analyses (profile + material + length/loads + results) that
  can be browsed back through, not just the current one-off result
- **Baseline comparison section**: a dedicated results section showing
  headline comparison figures against a reference profile — e.g. "X.X%
  stiffness vs. baseline," repeated for deflection and safety factor (and any
  other metric where a relative comparison is more useful than an absolute
  number). Baseline options:
  - A built-in reference: 20x40 KJN-series aluminum extrusion
  - Any DXF the user has analyzed, selectable as the baseline going forward,
    so all subsequent comparisons are made against it

## Open items to decide once you start building
- Sign/direction convention for loads and axes (recommend fixing this early
  and documenting it, since BC + load direction combinations are the easiest
  place to introduce silent errors)
- Units (recommend SI throughout, mm for geometry, N for force, MPa for
  stress, to match extrusion drawing conventions)
- What "load ratio of length" means exactly at the fixed-fixed vs cantilever
  case — reaction/moment equations differ meaningfully by BC, worth a quick
  hand-check against known formulas before trusting the tool's numbers
