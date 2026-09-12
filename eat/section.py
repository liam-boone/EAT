"""
EAT section engine — thin wrapper around `sectionproperties`.

Takes a simple closed polygon (list of (x, y) vertices, mm) plus a material,
runs a finite-element section analysis, and returns the geometric and
material-weighted section properties needed by the rest of the tool.

Units (per spec): mm for geometry, MPa (N/mm^2) for modulus/stress, N for
force. Torsion/warping constants and section moduli follow from those (J in
mm^4, warping constant in mm^6, section moduli in mm^3).

Usable as a library:

    from eat.section import Material, analyze_section
    mat = Material(name="6061-T6", E=68900, nu=0.33, yield_strength=276)
    result = analyze_section([(0, 0), (50, 0), (50, 100), (0, 100)], mat)
    print(result.area, result.ixx, result.j)

Or from the command line against a JSON spec file:

    python -m eat.section path/to/section.json

See `_example_json()` below for the expected JSON shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from sectionproperties.analysis import Section
from sectionproperties.pre import Geometry
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient


@dataclass
class Material:
    """Material definition for section analysis.

    Supply either `nu` (Poisson's ratio) or `G` (shear modulus) — the other
    is derived from the isotropic relation G = E / (2 * (1 + nu)). If both
    are supplied (e.g. from a datasheet where they weren't derived from each
    other), both are kept as given rather than one overwriting the other.

    `ultimate_strength`, `shear_strength`, `shear_strength_approximate`, and
    `density` back the JSON-backed material list (build step 2, see
    `eat.materials`); all are optional so existing callers that only pass
    name/E/nu(or G)/yield_strength are unaffected.
    """

    name: str
    E: float  # elastic modulus, MPa
    nu: float | None = None  # Poisson's ratio, dimensionless
    G: float | None = None  # shear modulus, MPa
    yield_strength: float | None = None  # MPa
    ultimate_strength: float | None = None  # MPa
    shear_strength: float | None = None  # MPa
    shear_strength_approximate: bool = False  # True if shear_strength is a rough published figure
    density: float | None = None  # kg/m^3 (not used by the section engine itself)

    def __post_init__(self) -> None:
        if self.nu is None and self.G is None:
            raise ValueError(f"Material '{self.name}' needs either nu or G")
        if self.G is None:
            self.G = self.E / (2.0 * (1.0 + self.nu))
        elif self.nu is None:
            self.nu = self.E / (2.0 * self.G) - 1.0


@dataclass
class SectionResult:
    """Section properties for one polygon + material combination."""

    material: str

    # Pure geometric properties
    area: float
    perimeter: float
    cx: float
    cy: float
    ixx: float  # second moment of area about centroidal x axis, mm^4
    iyy: float
    ixy: float
    j: float  # St. Venant torsion constant, mm^4
    iw: float  # warping constant, mm^6
    x_sc: float  # shear centre, global coords
    y_sc: float
    zxx_plus: float  # elastic section modulus, mm^3
    zxx_minus: float
    zyy_plus: float
    zyy_minus: float
    sxx: float  # plastic section modulus, mm^3
    syy: float

    # Material-weighted stiffness per unit length
    ea: float  # N (axial stiffness)
    ei_xx: float  # N.mm^2
    ei_yy: float  # N.mm^2
    gj: float  # N.mm^2 (torsional stiffness)

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"Material: {self.material}",
            f"Area            = {self.area:,.4f} mm^2",
            f"Perimeter       = {self.perimeter:,.4f} mm",
            f"Centroid        = ({self.cx:,.4f}, {self.cy:,.4f}) mm",
            f"Ixx             = {self.ixx:,.6g} mm^4",
            f"Iyy             = {self.iyy:,.6g} mm^4",
            f"Ixy             = {self.ixy:,.6g} mm^4",
            f"J (torsion)     = {self.j:,.6g} mm^4",
            f"Iw (warping)    = {self.iw:,.6g} mm^6",
            f"Shear centre    = ({self.x_sc:,.4f}, {self.y_sc:,.4f}) mm",
            f"Zxx (+/-)       = {self.zxx_plus:,.6g} / {self.zxx_minus:,.6g} mm^3",
            f"Zyy (+/-)       = {self.zyy_plus:,.6g} / {self.zyy_minus:,.6g} mm^3",
            f"Sxx (plastic)   = {self.sxx:,.6g} mm^3",
            f"Syy (plastic)   = {self.syy:,.6g} mm^3",
            f"EA              = {self.ea:,.6g} N",
            f"EIxx            = {self.ei_xx:,.6g} N.mm^2",
            f"EIyy            = {self.ei_yy:,.6g} N.mm^2",
            f"GJ              = {self.gj:,.6g} N.mm^2",
        ]
        return "\n".join(lines)


def _build_geometry(vertices: list[tuple[float, float]]) -> Geometry:
    """Build a sectionproperties Geometry from a simple closed polygon.

    Vertices should describe a single simple (non-self-intersecting) ring;
    the last point does not need to repeat the first. Winding order is
    normalised to counter-clockwise, which is what sectionproperties expects.
    """
    if len(vertices) < 3:
        raise ValueError("A polygon needs at least 3 vertices")
    polygon = orient(Polygon(vertices), sign=1.0)
    # Use the pure geometric default material (E=1, nu=0) so the raw
    # geometric getters (get_ic, get_j, ...) stay available; the caller's
    # Material is applied afterwards to derive stiffness values.
    return Geometry(polygon)


def analyze_section(
    vertices: list[tuple[float, float]],
    material: Material,
    mesh_size: float | None = None,
) -> SectionResult:
    """Run a full section analysis on a polygon and return its properties.

    `mesh_size` is the target triangle area for the FE mesh (mm^2). If not
    given, it's picked as a fraction of the section's bounding-box area,
    which is fine enough for typical extrusion profiles.
    """
    geom = _build_geometry(vertices)

    if mesh_size is None:
        minx, miny, maxx, maxy = geom.geom.bounds
        bbox_area = max((maxx - minx) * (maxy - miny), 1.0)
        mesh_size = bbox_area / 2000.0

    geom.create_mesh(mesh_sizes=[mesh_size])
    sec = Section(geom)

    sec.calculate_geometric_properties()
    sec.calculate_warping_properties()
    sec.calculate_plastic_properties()

    cx, cy = sec.get_c()
    ixx, iyy, ixy = sec.get_ic()
    x_sc, y_sc = sec.get_sc()
    zxx_plus, zxx_minus, zyy_plus, zyy_minus = sec.get_z()
    sxx, syy = sec.get_s()

    return SectionResult(
        material=material.name,
        area=sec.get_area(),
        perimeter=sec.get_perimeter(),
        cx=cx,
        cy=cy,
        ixx=ixx,
        iyy=iyy,
        ixy=ixy,
        j=sec.get_j(),
        iw=sec.get_gamma(),
        x_sc=x_sc,
        y_sc=y_sc,
        zxx_plus=zxx_plus,
        zxx_minus=zxx_minus,
        zyy_plus=zyy_plus,
        zyy_minus=zyy_minus,
        sxx=sxx,
        syy=syy,
        ea=material.E * sec.get_area(),
        ei_xx=material.E * ixx,
        ei_yy=material.E * iyy,
        gj=material.G * sec.get_j(),
    )


def _example_json() -> dict:
    return {
        "material": {"name": "6061-T6", "E": 68900, "nu": 0.33, "yield_strength": 276},
        "mesh_size": None,
        "vertices": [[0, 0], [50, 0], [50, 100], [0, 100]],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eat.section",
        description="Analyze a closed polygon section with sectionproperties.",
    )
    parser.add_argument(
        "spec",
        nargs="?",
        help="Path to a JSON file with 'vertices', 'material', and optional 'mesh_size'. "
        "Omit to print an example spec.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the result as JSON instead of a summary."
    )
    args = parser.parse_args(argv)

    if args.spec is None:
        print(json.dumps(_example_json(), indent=2))
        print("\nSave this as a file and pass it as an argument to analyze it.", file=sys.stderr)
        return 0

    data = json.loads(Path(args.spec).read_text())
    material = Material(**data["material"])
    vertices = [tuple(p) for p in data["vertices"]]
    mesh_size = data.get("mesh_size")

    result = analyze_section(vertices, material, mesh_size=mesh_size)

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(result.summary())

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
