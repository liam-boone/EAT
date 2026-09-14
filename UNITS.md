# EAT unit convention

One consistent unit system is used everywhere in the Python engine
(`eat.section`, `eat.materials`, `eat.beam`), matching extrusion drawing
conventions (mm geometry) and chosen so E * I, E * A, and G * J all come
out directly in consistent force/moment units with no scaling factors
needed at call sites:

| Quantity                          | Unit     |
|------------------------------------|----------|
| Length / position (geometry, beam length, deflection) | mm |
| Force (point loads, reactions, axial load) | N |
| Moment (bending moment, reaction moment)   | N·mm |
| Stress, modulus (E, G, yield/ultimate/shear strength) | MPa (N/mm²) |
| Second moment of area (Ixx, Iyy, Ixy)      | mm⁴ |
| Torsion constant (J)                       | mm⁴ |
| Warping constant (Iw)                      | mm⁶ |
| Section modulus (elastic Z, plastic S)     | mm³ |
| EA (axial stiffness)                       | N |
| EI, GJ (bending/torsional stiffness)       | N·mm² |
| Density                                    | kg/m³ (metadata only — not used in stress/stiffness calcs, kept in its natural SI unit) |
| Mesh size (`analyze_section`'s `mesh_size`)| mm² (target FE mesh triangle *area*, not a length — easy to misread) |

## Where conversion happens

`eat/materials.json` stores E, G, and all strengths in **Pa** and density
in **kg/m³** — the natural units for a material datasheet. `eat/materials.py`
converts Pa → MPa (divide by 1e6) when loading into an `eat.section.Material`,
and MPa → Pa when saving back. This is the *only* place unit conversion
happens; everything downstream (`eat.section`, `eat.beam`) assumes the
mm-N-MPa system above and does no further conversion.

`eat/materials.py`'s CLI (`python -m eat.materials add/edit`) is the one
other deliberate exception: it accepts `--e-gpa`/`--g-gpa` in **GPa**, since
that is the natural unit to type a modulus in at a keyboard, and its
`list` table prints `E(GPa)`/`G(GPa)` columns for the same reason.
Both the flag names and the table header say so explicitly; internally the
values are converted to MPa immediately (`* 1000`) and stored as MPa/Pa
like everything else — GPa never leaks past that CLI boundary.

Two other places let a value leave the mm-N-MPa/kg system, both at a
display boundary and both explicitly labeled.

The first is `eat.pdf_io`'s advisory mass check (extracted area × length × density vs.
a drawing's title-block weight): its `MassCheck.note` reports an implied
density in **g/cm³**, the unit a material datasheet's density figure is
usually quoted in and the one that keeps that one comparison's numbers in
a readable range. It is explicitly labeled in the note text and is never
fed back into any calculation — a self-contained, clearly-labeled
exception, not a second convention.

The second is the **baseline comparison's specific
stiffness/strength metrics** (`frontend/app.js`,
`buildBaselineMetricGroups`). Each divides an engine quantity by a mass
per length in kg/m, which in the raw mm-N-MPa system produces a unit with
a fraction inside it — `N/(kg/m)`, `N·mm²/(kg/m)`. Those are displayed in
the equivalent simple SI form instead, rescaled once at the display
boundary (`SPECIFIC_*_SCALE`) and never fed back into any calculation:

| Metric                        | Raw            | Displayed  |
|-------------------------------|----------------|------------|
| Stiffness-to-weight, axial    | N/(kg/m)       | MN·m/kg    |
| Stiffness-to-weight, bending  | N·mm²/(kg/m)   | N·m³/kg    |
| Strength-to-weight, axial     | N/(kg/m)       | kN·m/kg    |
| Strength-to-weight, bending   | N·mm/(kg/m)    | N·m²/kg    |

The M/k prefixes are chosen to keep aluminium extrusions out of exponent
notation. Note that `EA`, `EIxx`, `EIyy` and `GJ` in the Section Results
panel are **not** converted: `N·mm²` is a product, not a nested fraction,
and it is the form in which the reader can reproduce the number from the
`mm⁴` and `MPa` rows sitting next to it.

Every numeric field in the FastAPI response models (`eat/api.py`) carries
its unit in the field's `description` (visible at `/docs`), even where the
unit is "obviously" mm/N/MPa by the convention above — a schema is read on
its own, without this file open next to it.

## Load direction convention (`eat.beam`)

Each point load acts along one of the section's local in-plane axes — the
same x/y used in the (x, y) polygon vertices passed to
`eat.section.analyze_section` — selected via `axis` (`LoadAxis`, default
**Y**, matching the tool's original convention):

- **Y**: load acts along local y, bends the beam about the centroidal
  x-axis (`Ixx`), stress governed by `Zxx`.
- **X**: load acts along local x, bends the beam about the centroidal
  y-axis (`Iyy`), stress governed by `Zyy`.

All point loads passed to one `analyze_beam` call must share the same
axis — mixed X/Y (biaxial) bending in a single analysis is out of scope
for now. A positive load magnitude acts in the +axis direction; by
convention (matching how beam-deflection tables are normally presented)
it produces deflection in that same direction. Bending moment uses the
standard sagging-positive statics sign (positive under a simply-supported
span's point load; fixed supports come out with a negative/hogging
reaction moment).

**Axial load** is independent of the transverse load axis above: it
always acts along the section's long axis (**Z**, the beam's length
direction), used only for the Euler buckling safety factor.

**Axial load sign: positive is COMPRESSION, negative is tension.** This
is the one place the sign convention does not follow the "+ve in the
+axis direction" rule the transverse loads use, because the axis in
question is the length and there is no canvas direction to refer to.
Compression is positive because it is the case the field exists to
serve — the buckling check has no meaning in tension.

That matters for reading the result, not just for entering the load:

- **Compression (positive)** — the Euler safety factor is reported,
  `Pcr / axial_load`.
- **Tension (negative)** — the buckling check is **suppressed**, and the
  UI reads "N/A — tension". A member in tension cannot buckle; dividing
  through anyway would report a negative "safety factor", which is
  meaningless and was doing so before this was pinned down.
- **Zero or blank** — no axial load, so no factor. The Euler load itself
  is still reported, since it is a property of the profile and length.

`BeamResult.buckling_status` carries which of those three applies
(`"compression"` / `"tension"` / `"no_axial"`), so the frontend never has
to infer the reason from a bare `null`.

## Unsymmetric bending

Bending is solved about the section's **principal** axes, not the sketch
axes, so a profile whose `Ixy ≠ 0` (an angle, a Z, or simply a profile
drawn at an angle on the canvas) gets its true peak stress and deflects
out of the load plane as it really does. Units are unchanged — this is a
change of method, not of convention — but two reported quantities are new
and worth naming here:

| Quantity | Unit | Meaning |
|---|---|---|
| `max_deflection` | mm | Component **along** the load axis (what the deflection chart plots, and what this field has always meant) |
| `max_deflection_transverse` | mm | Component **perpendicular** to it, at the same station. Identically zero when `Ixy = 0` |
| `max_deflection_resultant` | mm | Magnitude of the vector sum, at its own worst station |
| `max_bending_stress_point` | mm | `(x, y)` of the worst fibre, measured **from the centroid** |
| `principal_angle_deg` | ° | Rotation from the sketch axes to the first principal axis |
| `i11` / `i22` | mm⁴ | Major / minor principal second moments. `i22` is the weak axis the Euler check uses |
| `asymmetry` | — | `\|Ixy\| / √(Ixx·Iyy)`, dimensionless. Zero when the sketch axes are already principal |

Loads are assumed to act through the section's shear centre, so no torsion
is induced — true biaxial bending, torsion, and shear-centre offset
effects are out of scope for v1 (see spec v1 boundaries).
