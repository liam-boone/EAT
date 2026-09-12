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

## Where conversion happens

`eat/materials.json` stores E, G, and all strengths in **Pa** and density
in **kg/m³** — the natural units for a material datasheet. `eat/materials.py`
converts Pa → MPa (divide by 1e6) when loading into an `eat.section.Material`,
and MPa → Pa when saving back. This is the *only* place unit conversion
happens; everything downstream (`eat.section`, `eat.beam`) assumes the
mm-N-MPa system above and does no further conversion.

## Load direction convention (`eat.beam`)

Point loads act along the section's local **y-axis** — the same y used in
the (x, y) polygon vertices passed to `eat.section.analyze_section` — and
bend the beam about the section's centroidal x-axis (`Ixx`). A positive
load magnitude acts in the +y direction; by convention (matching how
beam-deflection tables are normally presented) it produces deflection in
that same +y direction. Bending moment uses the standard sagging-positive
statics sign (positive under a simply-supported span's point load; fixed
supports come out with a negative/hogging reaction moment).

Loads are assumed to act through the section's shear centre, so no torsion
is induced — biaxial bending, torsion, and shear-centre offset effects are
out of scope for v1 (see spec v1 boundaries).
