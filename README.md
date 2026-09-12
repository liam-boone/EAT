# EAT — Extrusion Analysis Tool

A quick-turnaround tool for sizing extruded (aluminum, plastic) profiles.
Sketch or import a cross-section and get its section properties instantly;
add a length, boundary condition, and point loads to get bending stress,
deflection, safety factor, and Euler buckling. It's a fast engineering-
formula tool built on real section geometry — not a full FEA solver.

## Requirements

- Python 3.12 (recommended — this is what the dependencies, notably
  `sectionproperties`' compiled wheels, are verified against). A newer
  Python may not yet have prebuilt wheels for the scientific stack, in
  which case `run.sh`/`run.bat` may fail during dependency install.
- Mac or Windows. No OS-specific code — the same backend and browser
  frontend run identically on both.

## Install & run

```
git clone https://github.com/liam-boone/EAT.git
cd EAT
./run.sh          # Mac
run.bat           # Windows
```

First run creates a virtual environment (`.venv`) and installs
dependencies — this takes a minute or two. It then starts the local
server and opens the app in your browser at `http://127.0.0.1:8000/`.
Every run after that is fast; just re-run the same script.

To stop the server, close the terminal window it's running in (or Ctrl+C).

## How to use it

1. **Get a profile onto the sketch canvas** — either:
   - Click on the grid to place vertices one at a time, then click near
     the first point (or press **Close loop**) to finish the polygon, or
   - Click **Import DXF** and pick a `.dxf` file containing a single
     closed profile.
2. **Pick a material** from the dropdown (loaded from the material list —
   see below).
3. **Section results appear immediately** once the profile is closed:
   area, centroid, moments of inertia, torsion constant, section moduli,
   stiffness per unit length, etc.
4. **Optionally add length & loads** — beam length, boundary condition,
   one or more point loads, and (optionally) an axial load — to unlock
   bending stress, deflection, safety factor, and Euler buckling results,
   including two charts (stress and deflection along the beam's length).
   Click **Analyze beam** to run it.
5. **Export DXF** at any point to save your sketched (or edited) profile
   back out as a `.dxf` file.

## v1 scope

- Single closed polygon profiles only — no multi-cell or composite
  cross-sections.
- DXF import/export only — no DWG (proprietary format, no viable open
  library).
- Point loads only — no distributed loads.
- Global (Euler) buckling only — no local/plate buckling.

## The material list

Materials live in [`eat/materials.json`](eat/materials.json), seeded with
8 common extrusion materials (5 aluminum alloys, ABS, polycarbonate, PA6
nylon). You can add, edit, or delete materials two ways:

- **From the app** — `POST /materials`, `PUT /materials/{name}`, and
  `DELETE /materials/{name}` on the running server (see `/docs` for the
  full interactive API reference).
- **From the command line**:
  ```
  python -m eat.materials list
  python -m eat.materials add --name "..." --density 2700 --e-gpa 69 \
      --nu 0.33 --yield-mpa 241 --uts-mpa 262 --shear-mpa 165
  python -m eat.materials edit "<name>" --yield-mpa 250
  python -m eat.materials delete "<name>"
  ```
  `add` prompts interactively for any value not passed as a flag. Run
  these with the project's venv Python, e.g.
  `.venv/bin/python -m eat.materials list` (Mac) or
  `.venv\Scripts\python -m eat.materials list` (Windows).

## Known gaps

- **`run.bat` (Windows) has been logic-reviewed but not run end-to-end on
  a real Windows machine** — no Windows machine was available while
  building it. It's commented step-by-step (venv creation, dependency
  install, browser open, server start) so a failure should point at
  which step broke; please report back if you hit an issue running it.
- Units, conventions, and the mm-N-MPa system used throughout are
  documented in [`UNITS.md`](UNITS.md).
- Full build background and v1 boundaries are in
  [`extrusion-fea-tool-spec.md`](extrusion-fea-tool-spec.md).
