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

The one other place a value legitimately leaves the mm-N-MPa/kg system is
`eat.pdf_io`'s advisory mass check (extracted area × length × density vs.
a drawing's title-block weight): its `MassCheck.note` reports an implied
density in **g/cm³**, the unit a material datasheet's density figure is
usually quoted in and the one that keeps that one comparison's numbers in
a readable range. It is explicitly labeled in the note text and is never
fed back into any calculation — a self-contained, clearly-labeled
exception, not a second convention.

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

Loads are assumed to act through the section's shear centre, so no torsion
is induced — true biaxial bending, torsion, and shear-centre offset
effects are out of scope for v1 (see spec v1 boundaries).
