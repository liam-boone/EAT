# EAT — Extrusion Analysis Tool

A quick-turnaround tool for sizing extruded (aluminum, plastic) profiles.
Sketch a cross-section, import it from a DXF file, or import it straight
from a PDF engineering drawing, and get its section properties instantly.
Add a length, boundary condition, and point loads to get bending stress,
deflection, safety factor, and both global (column) and local (per-wall
plate) buckling. It's a fast engineering-formula tool built on real
section geometry — not a full FEA solver.

This README assumes no prior experience with git, a terminal, or Python.
If you already have all three set up, skip to [Running it](#running-it).

## Installation

You need two things on your computer before you can run EAT: **Python**
(the language it's written in) and **git** (used to download the project
from GitHub). Both are one-time installs — after that, starting the app
is a single command.

Pick your operating system below and follow the steps in order.

### On Mac

**1. Open Terminal.**
Terminal is the app you'll type commands into. Press
<kbd>Cmd</kbd>+<kbd>Space</kbd> to open Spotlight, type `Terminal`, and
press Enter. A window with a text prompt will appear — that's it, that's
Terminal.

**2. Check whether Python is already installed.**
Type this and press Enter:

```
python3 --version
```

- If you see `Python 3.12.x` (any x), you're set — skip to step 3.
- If you see a *much* newer or older version, or an error, install
  Python 3.12 as described next.
- If Terminal instead offers to install "Command Line Developer Tools,"
  that's a *different* thing (see step 4) — click Install if it asks,
  but you still need Python from python.org below.

**Install Python 3.12** from
**[python.org/downloads](https://www.python.org/downloads/)**. The big
button on that page downloads whatever the newest Python release is
(3.14 at the time of writing) — don't use that one. This project is
built and tested against Python 3.12 specifically, and a newer version
may or may not work depending on whether its dependencies have caught
up yet, so instead scroll down that same page to the full list of
releases, find the newest version starting with **3.12** (e.g. 3.12.14),
click it, and download the macOS installer from that release's page.
Open the downloaded `.pkg` file and click through the installer with
the default options. Once it's done, close and reopen Terminal, then
re-run `python3 --version` to confirm.

**3. Check whether git is already installed.**
Type:

```
git --version
```

If it prints a version number, you're done — skip to step 4.

**4. Install git if needed.**
On Mac, git usually comes bundled with Apple's "Command Line Developer
Tools" rather than a separate installer. If step 3 didn't already print
a version, macOS will typically pop up a dialog offering to install
these tools the moment you type `git --version` — click **Install**,
wait a few minutes for it to finish, then run `git --version` again to
confirm. (If no dialog appears and you still get an error, install
[Xcode Command Line Tools](https://developer.apple.com/xcode/resources/)
manually, or run `xcode-select --install` in Terminal.)

**5. Download the project.**
"Cloning" a repository just means downloading it, with git keeping track
of where it came from. Decide where you want the project folder to live
(e.g. your Documents folder), navigate there in Terminal, and download it:

```
cd ~/Documents
git clone https://github.com/liam-boone/EAT.git
```

This creates a new folder named `EAT` inside Documents with the full
project in it.

**6. Run it.**

```
cd EAT
./run.sh
```

Run this from Terminal as shown above — **don't** double-click `run.sh`
in Finder. Double-clicking a `.sh` file on Mac typically opens it in a
text editor instead of running it, since Finder doesn't treat shell
scripts as programs by default. Typing `./run.sh` in Terminal always
works.

The first run takes a minute or two while it sets up (downloading and
installing the Python packages EAT depends on); every run after that is
fast. See [What "it's working" looks like](#what-its-working-looks-like)
below for what happens next.

### On Windows

**1. Open a terminal.**
On Windows this is either Command Prompt or PowerShell — either works
fine for everything below. Press the Windows key, type `cmd` (for
Command Prompt) or `powershell` (for PowerShell), and press Enter. A
window with a text prompt will appear.

**2. Check whether Python is already installed.**
Type:

```
python --version
```

- If you see `Python 3.12.x`, you're set — skip to step 3.
- If you see a very different version or an error (including a window
  that pops up offering to open the Microsoft Store), install Python as
  described next.

**Install Python 3.12** from
**[python.org/downloads](https://www.python.org/downloads/)**. The big
button on that page downloads whatever the newest Python release is
(3.14 at the time of writing) — don't use that one. This project is
built and tested against Python 3.12 specifically, and a newer version
may or may not work depending on whether its dependencies have caught
up yet, so instead scroll down that same page to the full list of
releases, find the newest version starting with **3.12** (e.g. 3.12.14),
click it, and download the Windows installer from that release's page.

Run the downloaded installer. **Before clicking Install**, look
carefully at the first screen for a checkbox mentioning adding Python
(or `python.exe`) to **PATH** — the exact wording varies slightly by
installer version, but it's the only checkbox on that screen and it's
easy to miss. Make sure it's ticked. This is the single most common
install failure: skip it, and Windows won't be able to find Python when
you type `python` in a terminal, and every command below will fail with
"not recognized." Once it's ticked, proceed with the install and accept
the defaults. When it's done, close and reopen your terminal window (so
it picks up the change) and re-run `python --version` to confirm.

**3. Check whether git is already installed.**
Type:

```
git --version
```

If it prints a version number, skip to step 4. If you get "'git' is not
recognized...", install it next.

**Install git** from
**[git-scm.com/download/win](https://git-scm.com/download/win)** — the
download should start automatically. Run the installer and click
**Next** through every screen, accepting the defaults; none of the
options matter for this project. Once it finishes, close and reopen your
terminal and re-run `git --version` to confirm.

**4. Download the project.**
Navigate to where you want the project folder (e.g. your Documents
folder) and download it:

```
cd Documents
git clone https://github.com/liam-boone/EAT.git
```

This creates a new `EAT` folder inside Documents with the full project
in it.

**5. Run it.**
Go into the folder:

```
cd EAT
```

Then either:

- **Double-click `run.bat`** in File Explorer (open the `EAT` folder you
  just created, find `run.bat`, double-click it) — this is the normal
  way to run it day to day, and a window will open and stay open while
  the server runs, or
- **Type `run.bat`** and press Enter in the terminal window you already
  have open.

Both do the same thing. If something goes wrong and a double-clicked
window flashes an error and closes too fast to read, run it from the
terminal instead (the second option) so the error stays on screen.

The first run takes a minute or two to set up; every run after that is
fast.

## Running it

Once you've done the one-time install above, starting the app later is
just:

```
cd EAT
./run.sh          # Mac — from Terminal
run.bat           # Windows — double-click, or type this in a terminal
```

### What "it's working" looks like

After the setup (first run only) and a few seconds, your default web
browser should open automatically to `http://127.0.0.1:8000/` and show
the EAT interface — a sketch canvas on the left, a Setup panel on the
right.

**If the browser doesn't open on its own:** check the terminal window —
once you see a line like `Uvicorn running on http://127.0.0.1:8000`,
open that address in any browser manually. This can happen on a slower
machine, or if antivirus software is scanning the newly-created files.

**To stop the server:** go back to the terminal/Command Prompt window
it's running in and press <kbd>Ctrl</kbd>+<kbd>C</kbd>, or just close
the window.

## How to use it

1. **Get a profile onto the sketch canvas** — three ways:
   - Click on the grid to place vertices one at a time, then click near
     the first point (or press **Close Loop**) to finish the polygon.
     This builds a single outer profile — holes aren't sketchable by
     hand yet (see below).
   - Click **Import DXF** and pick a `.dxf` file. A DXF can contain
     interior holes as well as the outer boundary; both come in
     automatically.
   - Click **Import PDF Drawing** and pick a vector PDF engineering
     drawing. EAT reads the profile view directly off the drawing and
     cross-checks the scale it used against every dimension callout on
     that view — see the **Read from Drawing** panel that appears once
     it's read, which shows exactly how it interpreted the sheet and
     whether each dimension checks out.
2. **Pick a material** from the dropdown (loaded from the material list —
   see below). One is selected by default; change it any time.
3. **Section results appear immediately** once the profile is closed:
   area and mass per length up front, with centroid, moments of inertia,
   torsion constant, warping constant, shear centre, section moduli, and
   stiffness per unit length available under **More Info** just below.
4. **Design Review** appears automatically underneath the canvas once a
   profile exists, flagging anything the geometry itself suggests is
   worth a second look — split into two columns:
   - **DFM** (can it actually be extruded): wall thickness problems,
     unfilleted internal corners, narrow notches.
   - **Structural** (is the material earning its mass): whether area is
     distributed efficiently for the bending axes, and whether there's
     solid material sitting near the neutral axis doing little for
     stiffness.

   Hovering or clicking a finding highlights the exact geometry it's
   about on the canvas above. An empty column just means those checks
   all passed — it says so rather than showing nothing.
5. **Compared to Baseline**, once a profile is loaded, shows how its
   stiffness- and strength-to-weight stack up against a reference
   profile — a built-in 20×40mm extrusion (shown as "20x40 KJN-series")
   by default, or any past run you've set as the baseline (see History
   below). Change it with the **Change…** button here, or the
   **Baseline:** button in the top bar, which is always visible.
6. **History**, in the top bar, lists every analysis you've run (the
   most recent 25). Click a row to reload that exact profile and its
   frozen results — reloading never recomputes, so it stays accurate
   even if you've since edited the material list. Click **Set as
   Baseline** on any row to compare future profiles against it.
7. **Optionally add length & loads** — beam length, boundary condition,
   one or more point loads, and (optionally) an axial load — to unlock
   bending stress, deflection, safety factor, and buckling results,
   including two charts (stress and deflection along the beam's length).
   Click **Analyze Beam** to run it. Buckling comes back twice over:
   global Euler buckling of the member as a column, and a local (plate)
   buckling check on each flat wall of the section — each wall gets both
   an elastic safety factor (pure plate-buckling theory) and a
   yield-capped effective one, since a thin-walled profile can fail by a
   wall rippling long before the column itself is in any danger, and a
   stocky wall can yield before it ever reaches its elastic buckling
   stress.
8. **Export DXF** at any point to save your sketched (or edited) profile
   back out as a `.dxf` file.

## Current scope

**Supported today:**

- Section geometry from three sources: click-to-sketch, DXF
  import/export, or direct PDF engineering-drawing import.
- A single outer profile per analysis, with any number of interior holes
  — holes arrive via DXF or PDF import (a multi-loop file), not by
  sketching them by hand on the canvas.
- Point loads — any number, at any position along the span — plus an
  optional axial load.
- Both buckling failure modes: global (Euler) column buckling for the
  member, and local (plate) buckling per wall of the section, each with
  its own safety factor. Neither replaces the other.
- A geometry-driven DFM/structural Design Review, run history (last 25
  analyses), and comparison against a baseline profile.

**Genuinely out of scope right now:**

- Multi-cell or composite (multiple materials in one section)
  cross-sections.
- Distributed loads — point loads only.
- DWG files — DXF only. DWG is Autodesk's proprietary format with no
  viable open-source reader, so there's no path to supporting it without
  a paid/proprietary dependency.
- Sketching holes by hand on the canvas — they currently only arrive via
  DXF/PDF import.

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
- Full build background is in
  [`extrusion-fea-tool-spec.md`](extrusion-fea-tool-spec.md).
