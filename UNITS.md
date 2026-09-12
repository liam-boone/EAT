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
