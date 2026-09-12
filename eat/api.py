"""
EAT FastAPI layer — wires together the section engine (step 1), material
list (step 2), beam engine (step 3), DXF I/O (step 4), run history
(step 9), and baseline comparison (step 10) as an HTTP API.

No frontend yet: this is the API layer only, meant to be exercised via
the auto-generated docs at /docs (Swagger UI) or curl/httpie. See
eat/verify_api.py for a scripted exercise of every endpoint.

Run with `python -m eat.api` or `uvicorn eat.api:app --reload`, or via
run.sh / run.bat at the project root.

Units follow UNITS.md (mm-N-MPa) throughout; nothing here does unit
conversion beyond what eat.materials already does at the JSON boundary.

A computed section can be referenced by id in a later POST /beam call
(`section_id`, returned by POST /section and /section/from-dxf) instead
of resending its vertices. This cache is in-memory and per-process — it
resets on server restart and is not shared across workers; that's fine
for this tool's single-user, single-process local-server use case. It
also retains the section's original vertices/holes (`_CachedSection`),
needed to log a full profile snapshot to history when a later POST /beam
references it by id rather than resending geometry.

Every successful POST /section, /section/from-dxf, and /beam call also
appends a full snapshot (profile, material, inputs, and the
already-computed result) to eat.history's JSON-backed log -- see that
module's docstring for why a JSON file over SQLite here.

GET/POST /baseline wrap eat.baseline, letting the frontend compare a
section's structural efficiency (stiffness/strength-to-weight) against a
reference "baseline" (a built-in extrusion profile, or any history entry
set as one) -- see that module's docstring. Neither route logs to
history: they're derived lookups (the comparison ratios are trivial
client-side math), not analysis runs in their own right.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from eat import baseline, history
from eat.beam import BeamResult, BoundaryCondition, PointLoad, analyze_beam
from eat.dxf_io import DxfImportError, export_polygon_to_text, import_polygon_from_bytes
from eat.materials import (
    add_material,
    delete_material,
    get_material,
    load_materials,
    update_material,
)
from eat.section import Material, SectionResult, analyze_section

Vertex = tuple[float, float]

app = FastAPI(
    title="Extrusion Analysis Tool API",
    description="Section properties, material list, beam mechanics, and DXF I/O for extruded profiles.",
    version="0.1.0",
)


@dataclass
class _CachedSection:
    result: SectionResult
    vertices: list[Vertex]
    holes: list[list[Vertex]]


# In-memory cache: section_id -> _CachedSection, populated by POST /section
# and /section/from-dxf, consumed by POST /beam's optional section_id input.
_SECTION_CACHE: dict[str, _CachedSection] = {}


def _error_message(exc: Exception) -> str:
    """KeyError's __str__ wraps its message in repr() quotes; unwrap that
    so 4xx responses carry the plain underlying message, not '"message"'."""
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


# --- Shared material models -------------------------------------------------


class MaterialSpec(BaseModel):
    """Inline material definition (mirrors eat.section.Material)."""

    name: str
    E: float = Field(..., description="Elastic modulus, MPa")
    nu: float | None = Field(None, description="Poisson's ratio (supply this or G)")
    G: float | None = Field(None, description="Shear modulus, MPa (supply this or nu)")
    yield_strength: float | None = Field(None, description="MPa")
    ultimate_strength: float | None = Field(None, description="MPa")
    shear_strength: float | None = Field(None, description="MPa")
    shear_strength_approximate: bool = False
    density: float | None = Field(None, description="kg/m^3")

    @model_validator(mode="after")
    def _check_nu_or_g(self) -> "MaterialSpec":
        if self.nu is None and self.G is None:
            raise ValueError("material needs either 'nu' or 'G'")
        return self

    def to_material(self) -> Material:
        return Material(**self.model_dump())


class MaterialResponse(BaseModel):
    name: str
    E: float
    nu: float
    G: float
    yield_strength: float | None
    ultimate_strength: float | None
    shear_strength: float | None
    shear_strength_approximate: bool
    density: float | None

    @classmethod
    def from_material(cls, material: Material) -> "MaterialResponse":
        return cls(**asdict(material))


class MaterialUpdateRequest(BaseModel):
    """PUT /materials/{name}: only fields provided (non-null) are changed."""

    name: str | None = None
    E: float | None = None
    nu: float | None = None
    G: float | None = None
    yield_strength: float | None = None
    ultimate_strength: float | None = None
    shear_strength: float | None = None
    shear_strength_approximate: bool | None = None
    density: float | None = None


def _resolve_material(material_name: str | None, material: MaterialSpec | None) -> Material:
    if (material_name is None) == (material is None):
        raise HTTPException(400, "Provide exactly one of 'material_name' or 'material'.")
    if material_name is not None:
        try:
            return get_material(material_name)
        except KeyError as exc:
            raise HTTPException(404, _error_message(exc)) from exc
    assert material is not None
    return material.to_material()


# --- Section models & endpoints ---------------------------------------------


class SectionRequest(BaseModel):
    vertices: list[Vertex]
    holes: list[list[Vertex]] | None = Field(
        None, description="Interior loops (mm) fully enclosed in `vertices`, subtracted from the section"
    )
    material_name: str | None = Field(None, description="Look up a material from materials.json")
    material: MaterialSpec | None = Field(None, description="...or supply one inline")
    mesh_size: float | None = None
    save_history: bool = Field(
        True,
        description="Log this analysis to run history. Set False for internal/derived lookups "
        "that aren't a user-facing analysis run in their own right (e.g. the frontend's "
        "solid-fill comparison, or refetching a section_id for a profile reloaded from history).",
    )


class DxfLoopInfo(BaseModel):
    vertex_count: int
    area: float = Field(description="mm^2")
    bbox: tuple[float, float, float, float] = Field(description="(minx, miny, maxx, maxy), mm")


class SectionResponse(BaseModel):
    section_id: str = Field(description="Pass this as section_id in a later POST /beam call")
    vertices: list[Vertex] = Field(description="Echoed back so the frontend can redraw the profile (e.g. after a DXF import)")
    holes: list[list[Vertex]] = Field(
        default_factory=list,
        description="Interior loops (mm) subtracted from the section, echoed back so the "
        "frontend can redraw them distinctly from the outer profile",
    )
    dxf_warnings: list[str] = Field(
        default_factory=list,
        description="Repairs ezdxf.recover made while loading a DXF file (empty for non-DXF input, or a clean file)",
    )
    dxf_loops: list[DxfLoopInfo] | None = Field(
        None,
        description="Every closed loop found in an imported DXF, outer and interior alike "
        "(null for non-DXF input). The largest becomes `vertices`; loops fully "
        "contained within it become `holes` -- see dxf_outer_loop_index.",
    )
    dxf_outer_loop_index: int | None = Field(
        None, description="Index into dxf_loops that became `vertices` (null for non-DXF input)"
    )
    material: str
    area: float
    perimeter: float
    cx: float
    cy: float
    ixx: float
    iyy: float
    ixy: float
    izz: float = Field(description="Polar moment about centroidal z-axis (= ixx + iyy), mm^4")
    j: float
    iw: float
    x_sc: float
    y_sc: float
    zxx_plus: float
    zxx_minus: float
    zyy_plus: float
    zyy_minus: float
    sxx: float
    syy: float
    ea: float
    ei_xx: float
    ei_yy: float
    gj: float
    mass_per_length: float | None = Field(description="kg/m; null if the material has no density")

    @classmethod
    def from_result(
        cls,
        section_id: str,
        vertices: list[Vertex],
        result: SectionResult,
        holes: list[list[Vertex]] | None = None,
        dxf_warnings: list[str] | None = None,
        dxf_loops: list[DxfLoopInfo] | None = None,
        dxf_outer_loop_index: int | None = None,
    ) -> "SectionResponse":
        return cls(
            section_id=section_id,
            vertices=vertices,
            holes=holes or [],
            dxf_warnings=dxf_warnings or [],
            dxf_loops=dxf_loops,
            dxf_outer_loop_index=dxf_outer_loop_index,
            **asdict(result),
        )


def _store_section(result: SectionResult, vertices: list[Vertex], holes: list[list[Vertex]] | None) -> str:
    section_id = uuid.uuid4().hex
    _SECTION_CACHE[section_id] = _CachedSection(result=result, vertices=vertices, holes=holes or [])
    return section_id


def _run_section_analysis(
    vertices: list[Vertex],
    material: Material,
    mesh_size: float | None,
    holes: list[list[Vertex]] | None = None,
    dxf_warnings: list[str] | None = None,
    dxf_loops: list[DxfLoopInfo] | None = None,
    dxf_outer_loop_index: int | None = None,
    save_history: bool = True,
) -> SectionResponse:
    try:
        result = analyze_section(vertices, material, mesh_size=mesh_size, holes=holes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if save_history:
        history.add_entry(
            material=result.material,
            vertices=vertices,
            holes=holes,
            section_result=result.as_dict(),
        )
    return SectionResponse.from_result(
        _store_section(result, vertices, holes),
        vertices,
        result,
        holes,
        dxf_warnings,
        dxf_loops,
        dxf_outer_loop_index,
    )


@app.post("/section", response_model=SectionResponse)
def post_section(req: SectionRequest) -> SectionResponse:
    material = _resolve_material(req.material_name, req.material)
    return _run_section_analysis(
        req.vertices, material, req.mesh_size, holes=req.holes, save_history=req.save_history
    )


@app.post("/section/from-dxf", response_model=SectionResponse)
async def post_section_from_dxf(
    file: UploadFile = File(..., description="A .dxf file with a single closed polygon profile"),
    material_name: str | None = Form(None),
    material_json: str | None = Form(
        None, description="JSON-encoded MaterialSpec, as an alternative to material_name"
    ),
    mesh_size: float | None = Form(None),
) -> SectionResponse:
    material_spec = MaterialSpec.model_validate_json(material_json) if material_json else None
    material = _resolve_material(material_name, material_spec)

    raw = await file.read()
    try:
        dxf_result = import_polygon_from_bytes(raw, source_label=file.filename or "<uploaded file>")
    except DxfImportError as exc:
        raise HTTPException(400, str(exc)) from exc

    dxf_loops = [
        DxfLoopInfo(vertex_count=l.vertex_count, area=l.area, bbox=l.bbox) for l in dxf_result.loops
    ]
    return _run_section_analysis(
        dxf_result.vertices,
        material,
        mesh_size,
        holes=dxf_result.holes,
        dxf_warnings=dxf_result.warnings,
        dxf_loops=dxf_loops,
        dxf_outer_loop_index=dxf_result.outer_loop_index,
    )


class ExportDxfRequest(BaseModel):
    vertices: list[Vertex]


@app.post("/section/to-dxf")
def post_section_to_dxf(req: ExportDxfRequest) -> Response:
    try:
        text = export_polygon_to_text(req.vertices)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(
        content=text,
        media_type="application/dxf",
        headers={"Content-Disposition": 'attachment; filename="profile.dxf"'},
    )


# --- Material list endpoints -------------------------------------------------


@app.get("/materials", response_model=list[MaterialResponse])
def get_materials() -> list[MaterialResponse]:
    return [MaterialResponse.from_material(m) for m in load_materials()]


@app.post("/materials", response_model=MaterialResponse, status_code=201)
def post_material(spec: MaterialSpec) -> MaterialResponse:
    material = spec.to_material()
    try:
        add_material(material)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return MaterialResponse.from_material(material)


@app.put("/materials/{name}", response_model=MaterialResponse)
def put_material(name: str, req: MaterialUpdateRequest) -> MaterialResponse:
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(400, "No fields given to update.")
    try:
        updated = update_material(name, **changes)
    except KeyError as exc:
        raise HTTPException(404, _error_message(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return MaterialResponse.from_material(updated)


@app.delete("/materials/{name}", status_code=204)
def delete_material_endpoint(name: str) -> None:
    try:
        delete_material(name)
    except KeyError as exc:
        raise HTTPException(404, _error_message(exc)) from exc


# --- Beam endpoint ------------------------------------------------------------


class PointLoadModel(BaseModel):
    position_fraction: float = Field(..., ge=0.0, le=1.0)
    magnitude: float = Field(..., description="N, positive in the +axis direction (see UNITS.md)")
    axis: Literal["x", "y"] = Field(
        "y",
        description="Section axis the load acts along: 'y' (default) bends about Ixx, "
        "'x' bends about Iyy. All point loads in one request must share the same axis.",
    )


class SectionInput(BaseModel):
    vertices: list[Vertex]
    holes: list[list[Vertex]] | None = None
    mesh_size: float | None = None


class BeamRequest(BaseModel):
    section: SectionInput | None = Field(None, description="Vertices to analyze fresh...")
    section_id: str | None = Field(None, description="...or a section_id from a prior POST /section")
    material_name: str | None = None
    material: MaterialSpec | None = None
    length: float = Field(..., gt=0, description="mm")
    boundary_condition: Literal[
        "fixed_fixed", "fixed_free", "simply_supported", "fixed_pinned"
    ]
    point_loads: list[PointLoadModel] = Field(..., min_length=1)
    axial_load: float | None = Field(
        None, description="N, along the section's Z (long) axis; optional, for buckling safety factor"
    )

    @model_validator(mode="after")
    def _check_section_source(self) -> "BeamRequest":
        if (self.section is None) == (self.section_id is None):
            raise ValueError("Provide exactly one of 'section' or 'section_id'.")
        return self


class ReactionResponse(BaseModel):
    label: str
    x: float
    force: float
    moment: float


class BeamResponse(BaseModel):
    boundary_condition: str
    length: float
    material: str
    reactions: list[ReactionResponse]
    load_axis: str = Field(description="'x' or 'y' -- which section axis the point loads bent about")
    max_moment: float
    max_moment_position: float
    max_bending_stress: float
    max_bending_stress_position: float
    safety_factor: float | None
    max_deflection: float
    max_deflection_position: float
    effective_length_factor: float
    euler_buckling_load: float
    axial_load: float | None
    buckling_safety_factor: float | None
    diagram_x: list[float]
    moment_diagram: list[float]
    bending_stress_diagram: list[float]
    deflection_diagram: list[float]

    @classmethod
    def from_result(cls, result: BeamResult) -> "BeamResponse":
        data = asdict(result)
        data["reactions"] = [ReactionResponse(**r) for r in data["reactions"]]
        return cls(**data)


@app.post("/beam", response_model=BeamResponse)
def post_beam(req: BeamRequest) -> BeamResponse:
    material = _resolve_material(req.material_name, req.material)

    if req.section_id is not None:
        cached = _SECTION_CACHE.get(req.section_id)
        if cached is None:
            raise HTTPException(
                404,
                f"No cached section with id '{req.section_id}'. It may have expired "
                "(server restarted) or never existed — POST /section first.",
            )
        section = cached.result
        vertices = cached.vertices
        holes = cached.holes
    else:
        assert req.section is not None
        try:
            section = analyze_section(
                req.section.vertices,
                material,
                mesh_size=req.section.mesh_size,
                holes=req.section.holes,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        vertices = req.section.vertices
        holes = req.section.holes or []

    point_loads = [PointLoad(pl.position_fraction, pl.magnitude, axis=pl.axis) for pl in req.point_loads]

    try:
        result = analyze_beam(
            section,
            material,
            length=req.length,
            boundary_condition=BoundaryCondition(req.boundary_condition),
            point_loads=point_loads,
            axial_load=req.axial_load,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    response = BeamResponse.from_result(result)

    history.add_entry(
        material=section.material,
        vertices=vertices,
        holes=holes,
        section_result=section.as_dict(),
        beam_request={
            "length": req.length,
            "boundary_condition": req.boundary_condition,
            "point_loads": [pl.model_dump() for pl in req.point_loads],
            "axial_load": req.axial_load,
        },
        beam_result=response.model_dump(),
    )

    return response


# --- History endpoints --------------------------------------------------------


class HistorySummaryResponse(BaseModel):
    id: str
    created_at: str
    material: str
    area: float | None
    has_holes: bool
    has_beam: bool


class HistoryEntryResponse(BaseModel):
    id: str
    created_at: str
    material: str
    vertices: list[Vertex]
    holes: list[list[Vertex]]
    section_result: dict[str, Any]
    beam_request: dict[str, Any] | None
    beam_result: dict[str, Any] | None


@app.get("/history", response_model=list[HistorySummaryResponse])
def get_history() -> list[HistorySummaryResponse]:
    """Most-recent-first summaries -- enough to identify each run at a
    glance (timestamp, material, area, whether it has holes/a beam run)
    without shipping every entry's full diagram arrays over the wire."""
    return [HistorySummaryResponse(**s) for s in history.list_summaries()]


@app.get("/history/{entry_id}", response_model=HistoryEntryResponse)
def get_history_entry(entry_id: str) -> HistoryEntryResponse:
    try:
        entry = history.get_entry(entry_id)
    except KeyError as exc:
        raise HTTPException(404, _error_message(exc)) from exc
    return HistoryEntryResponse(**asdict(entry))


@app.delete("/history/{entry_id}", status_code=204)
def delete_history_entry(entry_id: str) -> None:
    try:
        history.delete_entry(entry_id)
    except KeyError as exc:
        raise HTTPException(404, _error_message(exc)) from exc


# --- Baseline comparison endpoints -------------------------------------------


class BaselineResponse(BaseModel):
    source: Literal["builtin", "history"]
    name: str
    material: str
    area: float
    ixx: float
    iyy: float
    ea: float
    ei_xx: float
    ei_yy: float
    zxx_plus: float
    zxx_minus: float
    zyy_plus: float
    zyy_minus: float
    mass_per_length: float | None
    yield_strength: float | None = Field(
        None, description="MPa; null if the baseline's material has none, or no longer exists"
    )
    history_entry_id: str | None = Field(
        None, description="Set when source='history': the backing entry's id"
    )


def _baseline_response(info: baseline.BaselineInfo) -> BaselineResponse:
    sr = info.section_result
    try:
        yield_strength = get_material(info.material).yield_strength
    except KeyError:
        yield_strength = None
    return BaselineResponse(
        source=info.source,
        name=info.name,
        material=info.material,
        area=sr["area"],
        ixx=sr["ixx"],
        iyy=sr["iyy"],
        ea=sr["ea"],
        ei_xx=sr["ei_xx"],
        ei_yy=sr["ei_yy"],
        zxx_plus=sr["zxx_plus"],
        zxx_minus=sr["zxx_minus"],
        zyy_plus=sr["zyy_plus"],
        zyy_minus=sr["zyy_minus"],
        mass_per_length=sr["mass_per_length"],
        yield_strength=yield_strength,
        history_entry_id=info.history_entry_id,
    )


class BaselineSelectionRequest(BaseModel):
    type: Literal["builtin", "history"]
    entry_id: str | None = Field(None, description="Required when type='history'")

    @model_validator(mode="after")
    def _check_entry_id(self) -> "BaselineSelectionRequest":
        if self.type == "history" and not self.entry_id:
            raise ValueError("entry_id is required when type='history'")
        return self


@app.get("/baseline", response_model=BaselineResponse)
def get_baseline() -> BaselineResponse:
    """The currently-selected baseline's section-level properties (plus a
    fresh lookup of its material's yield strength), for the frontend's
    dual-bar stiffness-to-weight / strength-to-weight comparison -- the
    ratio arithmetic itself is trivial client-side math against whatever
    section is currently displayed."""
    return _baseline_response(baseline.resolve_baseline())


@app.post("/baseline", response_model=BaselineResponse)
def post_baseline(req: BaselineSelectionRequest) -> BaselineResponse:
    """Change which baseline is active. Persists until changed again --
    see eat.baseline's docstring for the JSON-file storage rationale."""
    if req.type == "history":
        assert req.entry_id is not None
        try:
            history.get_entry(req.entry_id)
        except KeyError as exc:
            raise HTTPException(404, _error_message(exc)) from exc
        baseline.set_baseline_setting({"type": "history", "entry_id": req.entry_id})
    else:
        baseline.set_baseline_setting({"type": "builtin"})
    return _baseline_response(baseline.resolve_baseline())


# Frontend static files (build step 6). Mounted last and at "/" so it acts
# as a catch-all: requests to routes declared above (e.g. /section,
# /materials, /docs) still match those first -- only otherwise-unmatched
# paths fall through to serving frontend/index.html or its assets.
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("eat.api:app", host="127.0.0.1", port=8000)
