"use strict";

/* ------------------------------------------------------------------
   State
------------------------------------------------------------------ */

const DEFAULT_VIEW_WIDTH_MM = 300; // sketching-mode view width before anything is closed
const MIN_VIEW_WIDTH_MM = 20; // floor so a tiny imported profile doesn't zoom in absurdly
const FIT_PADDING_FACTOR = 1.2; // 20% margin around a fitted profile

const state = {
  points: [],           // [{x, y}] mm, engineering coords (y up)
  holes: [],             // [[{x, y}, ...], ...] interior loops, e.g. from a multi-loop DXF import
  isClosed: false,
  materials: [],
  selectedMaterial: null,
  sectionId: null,
  pointLoads: [{ position_fraction: 0.5, magnitude: -1000, axis: "y" }],
  viewWidthMm: DEFAULT_VIEW_WIDTH_MM, // how many mm of width the sketch grid shows
  viewOriginX: 0,        // world x mapped to the left margin
  viewOriginY: 0,        // world y mapped to the bottom margin
  pixelsPerMm: 1,        // recomputed on layout/resize
  lastSectionResult: null, // currently-displayed section result, for the baseline comparison
  highlight: null,         // suggestion geometry currently drawn on the sketch, if any
  pinnedSuggestion: null,  // index of a click-pinned suggestion, so it survives mouseout
};

/* ------------------------------------------------------------------
   DOM references
------------------------------------------------------------------ */

const canvas = document.getElementById("sketch-canvas");
const ctx = canvas.getContext("2d");
const canvasWell = canvas.parentElement;
const coordReadout = document.getElementById("coord-readout");

const btnUndo = document.getElementById("btn-undo");
const btnCloseLoop = document.getElementById("btn-close-loop");
const btnClear = document.getElementById("btn-clear");
const btnExportDxf = document.getElementById("btn-export-dxf");
const btnImportDxf = document.getElementById("btn-import-dxf");
const dxfFileInput = document.getElementById("dxf-file-input");
const btnImportPdf = document.getElementById("btn-import-pdf");
const pdfFileInput = document.getElementById("pdf-file-input");
const pdfImportSectionEl = document.getElementById("pdf-import-section");
const pdfImportGridEl = document.getElementById("pdf-import-grid");
const pdfImportSummaryEl = document.getElementById("pdf-import-summary");
const pdfDimTableEl = document.getElementById("pdf-dim-table");
const pdfWarningListEl = document.getElementById("pdf-warning-list");
const pdfMassCheckEl = document.getElementById("pdf-mass-check");
const sketchHint = document.getElementById("sketch-hint");

const materialSelect = document.getElementById("material-select");

const sectionResultsEl = document.getElementById("section-results");
const sectionResultGridPrimary = document.getElementById("section-result-grid-primary");
const sectionResultGridSecondary = document.getElementById("section-result-grid-secondary");
const solidFillComparisonEl = document.getElementById("solid-fill-comparison");
const solidFillGrid = document.getElementById("solid-fill-grid");

const beamInputsEl = document.getElementById("beam-inputs");
const inputLength = document.getElementById("input-length");
const inputBc = document.getElementById("input-bc");
const pointLoadsListEl = document.getElementById("point-loads-list");
const btnAddLoad = document.getElementById("btn-add-load");
const inputAxial = document.getElementById("input-axial");
const btnAnalyzeBeam = document.getElementById("btn-analyze-beam");

const beamResultsEl = document.getElementById("beam-results");
const beamSummaryGrid = document.getElementById("beam-summary-grid");
const chartStressEl = document.getElementById("chart-stress");
const chartDeflectionEl = document.getElementById("chart-deflection");

const errorBanner = document.getElementById("error-banner");
const errorBannerText = document.getElementById("error-banner-text");
const errorBannerClose = document.getElementById("error-banner-close");

const btnOpenHistory = document.getElementById("btn-open-history");
const btnCloseHistory = document.getElementById("btn-close-history");
const historyModal = document.getElementById("history-modal");
const historyListEl = document.getElementById("history-list");
const historyEmptyHint = document.getElementById("history-empty-hint");

const baselineSectionEl = document.getElementById("baseline-section");
const baselineNameEl = document.getElementById("baseline-name");
const btnUseBuiltinBaseline = document.getElementById("btn-use-builtin-baseline");
const baselineMetricsEl = document.getElementById("baseline-metrics");

const suggestionsSectionEl = document.getElementById("suggestions-section");
const suggestionListEl = document.getElementById("suggestion-list");
const suggestionsCountEl = document.getElementById("suggestions-count");
const suggestionsEmptyEl = document.getElementById("suggestions-empty");

/* ------------------------------------------------------------------
   API helper — surfaces the API's own error text, never swallows it
------------------------------------------------------------------ */

async function apiFetch(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (networkErr) {
    throw new Error(`Could not reach the API: ${networkErr.message}`);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (_) {
      /* response wasn't JSON; fall back to statusText above */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showError(message) {
  errorBannerText.textContent = message;
  errorBanner.hidden = false;
}

function hideError() {
  errorBanner.hidden = true;
}

errorBannerClose.addEventListener("click", hideError);

/* ------------------------------------------------------------------
   Number formatting — tabular, unit-aware, matches the mono type scale
------------------------------------------------------------------ */

function fmtNum(n, { digits } = {}) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e7)) {
    return n.toExponential(3);
  }
  const maximumFractionDigits = digits !== undefined ? digits : abs < 1 ? 4 : abs < 1000 ? 2 : 0;
  return n.toLocaleString("en-US", { maximumFractionDigits });
}

/** Title-cases short backend-sourced labels (e.g. beam.py's reaction
 * labels: "Fixed support (x=0)" -> "Fixed Support (x=0)") without
 * touching tokens that aren't pure alphabetic words -- coordinates like
 * "(x=0)" or "(x=L)" pass through untouched. Not a general-purpose title
 * caser (no small-word exceptions); fine for this fixed set of short
 * technical labels. */
function titleCaseLabel(text) {
  return text
    .split(" ")
    .map((word) => (/^[A-Za-z]+$/.test(word) ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(" ");
}

function resultRow(grid, label, value, unit) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  if (unit) {
    const span = document.createElement("span");
    span.className = "unit";
    span.textContent = unit;
    dd.appendChild(span);
  }
  grid.appendChild(dt);
  grid.appendChild(dd);
}

/* ------------------------------------------------------------------
   Sketch canvas: coordinate mapping (CSS-pixel space after ctx.scale)
------------------------------------------------------------------ */

const MARGIN_PX = 28;

function layoutCanvas() {
  // Measure the canvas element itself (not its .canvas-well parent, whose
  // border-box rect is ~2px wider than the canvas's own content width) so
  // this basis exactly matches what click handlers read via
  // canvas.getBoundingClientRect() -- otherwise clicked mm coordinates
  // drift slightly from the intended ones.
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.getBoundingClientRect().width;
  const cssHeight = Math.round(cssWidth * 0.75); // 4:3, matches canvas attrs
  canvas.style.height = cssHeight + "px";
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  state.pixelsPerMm = (cssWidth - 2 * MARGIN_PX) / state.viewWidthMm;
  state.cssWidth = cssWidth;
  state.cssHeight = cssHeight;
  render();
}

function worldToScreen(wx, wy) {
  return {
    x: MARGIN_PX + (wx - state.viewOriginX) * state.pixelsPerMm,
    y: state.cssHeight - MARGIN_PX - (wy - state.viewOriginY) * state.pixelsPerMm,
  };
}

function screenToWorld(sx, sy) {
  return {
    x: (sx - MARGIN_PX) / state.pixelsPerMm + state.viewOriginX,
    y: (state.cssHeight - MARGIN_PX - sy) / state.pixelsPerMm + state.viewOriginY,
  };
}

/** Center the view on `points`' bounding box with padding, or reset to the
 * default sketching view if there are none. Shared by both the manual
 * close-loop path and DXF import -- neither had this before; a manually
 * sketched polygon just happened to sit inside the old fixed 0-300mm
 * window because that's where the visible grid was. */
function fitViewToPolygon(points) {
  if (points.length === 0) {
    state.viewWidthMm = DEFAULT_VIEW_WIDTH_MM;
    state.viewOriginX = 0;
    state.viewOriginY = 0;
    return;
  }
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const shapeWidth = Math.max(maxX - minX, 1e-6);
  const shapeHeight = Math.max(maxY - minY, 1e-6);

  // The drawing area's aspect ratio is fixed at 4:3 (viewHeight = viewWidth
  // * 0.75); pick whichever dimension needs more zoom-out to fit, with
  // padding, then center the shape's bounding box in that view.
  const viewWidth = Math.max(
    shapeWidth * FIT_PADDING_FACTOR,
    (shapeHeight * FIT_PADDING_FACTOR) / 0.75,
    MIN_VIEW_WIDTH_MM
  );
  const viewHeight = viewWidth * 0.75;

  state.viewWidthMm = viewWidth;
  state.viewOriginX = (minX + maxX) / 2 - viewWidth / 2;
  state.viewOriginY = (minY + maxY) / 2 - viewHeight / 2;
}

/* ------------------------------------------------------------------
   Sketch canvas: rendering
------------------------------------------------------------------ */

function render() {
  const w = state.cssWidth;
  const h = state.cssHeight;
  if (!w || !h) return;

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0A1233";
  ctx.fillRect(0, 0, w, h);

  drawGrid(w, h);
  drawAxisIndicator(w, h);
  drawPolygon();
  drawSuggestionHighlight();
}

/** Draws whichever suggestion is currently hovered or pinned, on top of
 * the profile so it reads against the accent-coloured outline. */
function drawSuggestionHighlight() {
  const highlight = state.highlight;
  if (!highlight) return;

  ctx.save();
  ctx.strokeStyle = "#FFB84D"; // --warning
  ctx.fillStyle = "rgba(255, 184, 77, 0.25)";
  ctx.lineWidth = 2;

  (highlight.polylines || []).forEach((line) => {
    if (line.length < 2) return;
    ctx.beginPath();
    line.forEach(([x, y], i) => {
      const p = worldToScreen(x, y);
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  });

  (highlight.points || []).forEach(([x, y]) => {
    const p = worldToScreen(x, y);
    ctx.beginPath();
    ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  });

  ctx.restore();
}

/** Small fixed corner gizmo showing which screen direction is +X / +Y --
 * always visible regardless of pan/zoom (unlike the origin gridlines,
 * which only draw when the origin itself is in view). Must match the
 * same world axes eat.beam's load-axis selector bends about: worldToScreen
 * maps +x to the right and +y up on screen, so the arms point the same
 * way. Kept thin/small (drafting-table aesthetic) so it doesn't compete
 * with the profile -- tucked in the bottom-left, above the coord readout. */
function drawAxisIndicator(w, h) {
  const originX = MARGIN_PX + 10;
  const originY = h - MARGIN_PX - 44;
  const armLength = 26;
  const arrowSize = 5;

  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.22)";
  ctx.fillStyle = "rgba(255, 255, 255, 0.22)";

  drawAxisArm(originX, originY, originX + armLength, originY, arrowSize); // +X: right
  drawAxisArm(originX, originY, originX, originY - armLength, arrowSize); // +Y: up

  ctx.fillStyle = "#8D95C4";
  ctx.font =
    "10px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText("X", originX + armLength + 6, originY);
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("Y", originX, originY - armLength - 6);
}

function drawAxisArm(x0, y0, x1, y1, arrowSize) {
  ctx.beginPath();
  ctx.moveTo(x0 + 0.5, y0 + 0.5);
  ctx.lineTo(x1 + 0.5, y1 + 0.5);
  ctx.stroke();

  const angle = Math.atan2(y1 - y0, x1 - x0);
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x1 - arrowSize * Math.cos(angle - Math.PI / 6), y1 - arrowSize * Math.sin(angle - Math.PI / 6));
  ctx.lineTo(x1 - arrowSize * Math.cos(angle + Math.PI / 6), y1 - arrowSize * Math.sin(angle + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
}

function drawGrid(w, h) {
  const minorMm = 10;
  const majorEvery = 5; // every 5th minor line (50mm) is drawn heavier
  const viewHeightMm = state.viewWidthMm * 0.75;
  const viewMinX = state.viewOriginX;
  const viewMaxX = state.viewOriginX + state.viewWidthMm;
  const viewMinY = state.viewOriginY;
  const viewMaxY = state.viewOriginY + viewHeightMm;

  ctx.lineWidth = 1;

  const startXIdx = Math.floor(viewMinX / minorMm);
  const endXIdx = Math.ceil(viewMaxX / minorMm);
  for (let i = startXIdx; i <= endXIdx; i++) {
    const { x } = worldToScreen(i * minorMm, 0);
    ctx.strokeStyle = i % majorEvery === 0 ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.06)";
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, MARGIN_PX);
    ctx.lineTo(Math.round(x) + 0.5, h - MARGIN_PX);
    ctx.stroke();
  }

  const startYIdx = Math.floor(viewMinY / minorMm);
  const endYIdx = Math.ceil(viewMaxY / minorMm);
  for (let i = startYIdx; i <= endYIdx; i++) {
    const { y } = worldToScreen(0, i * minorMm);
    ctx.strokeStyle = i % majorEvery === 0 ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.06)";
    ctx.beginPath();
    ctx.moveTo(MARGIN_PX, Math.round(y) + 0.5);
    ctx.lineTo(w - MARGIN_PX, Math.round(y) + 0.5);
    ctx.stroke();
  }

  // Origin axes, slightly stronger -- only if the origin is actually in view.
  ctx.strokeStyle = "rgba(255,255,255,0.22)";
  if (viewMinY <= 0 && 0 <= viewMaxY) {
    const { y } = worldToScreen(0, 0);
    ctx.beginPath();
    ctx.moveTo(MARGIN_PX, Math.round(y) + 0.5);
    ctx.lineTo(w - MARGIN_PX, Math.round(y) + 0.5);
    ctx.stroke();
  }
  if (viewMinX <= 0 && 0 <= viewMaxX) {
    const { x } = worldToScreen(0, 0);
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, MARGIN_PX);
    ctx.lineTo(Math.round(x) + 0.5, h - MARGIN_PX);
    ctx.stroke();
  }
}

function drawPolygon() {
  if (state.points.length === 0) return;

  const screenPts = state.points.map((p) => worldToScreen(p.x, p.y));

  ctx.beginPath();
  screenPts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  if (state.isClosed) ctx.closePath();

  if (state.isClosed) {
    ctx.fillStyle = "rgba(213, 253, 95, 0.12)";
    ctx.fill();
    ctx.strokeStyle = "#D5FD5F";
    ctx.lineWidth = 2;
  } else {
    ctx.strokeStyle = "#8D95C4";
    ctx.lineWidth = 1.5;
  }
  ctx.stroke();

  screenPts.forEach((p, i) => {
    ctx.beginPath();
    const isFirst = i === 0 && !state.isClosed && state.points.length >= 3;
    ctx.arc(p.x, p.y, isFirst ? 5 : 3.5, 0, Math.PI * 2);
    ctx.fillStyle = state.isClosed ? "#D5FD5F" : isFirst ? "#D5FD5F" : "#F3F5FF";
    ctx.fill();
  });

  if (state.isClosed) {
    state.holes.forEach(drawHole);
  }
}

/** Interior loops (e.g. bores, T-slot channels from a multi-loop DXF
 * import) render as a cut-out: filled with the canvas background color
 * so they visually punch through the outer profile's lime fill, with a
 * neutral (non-accent) stroke so they read as distinct from the outer
 * boundary. Display-only -- manual hole sketching isn't supported yet. */
function drawHole(holePoints) {
  if (holePoints.length === 0) return;
  const screenPts = holePoints.map((p) => worldToScreen(p.x, p.y));
  ctx.beginPath();
  screenPts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  ctx.closePath();
  ctx.fillStyle = "#0A1233";
  ctx.fill();
  ctx.strokeStyle = "#8D95C4";
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

/* ------------------------------------------------------------------
   Sketch canvas: interaction
------------------------------------------------------------------ */

const CLOSE_HIT_PX = 10;

function updateToolbarState() {
  btnUndo.disabled = state.points.length === 0;
  btnCloseLoop.disabled = state.isClosed || state.points.length < 3;
  btnClear.disabled = state.points.length === 0;
  btnExportDxf.disabled = !state.isClosed;

  if (state.isClosed) {
    sketchHint.textContent = "Profile closed. Undo to edit, or Clear to start over.";
  } else if (state.points.length === 0) {
    sketchHint.textContent = "Click on the grid to place the first vertex of your profile.";
  } else if (state.points.length < 3) {
    sketchHint.textContent = `${state.points.length} point(s) placed — need at least 3 to close the loop.`;
  } else {
    sketchHint.textContent = "Click near the first point (or press Close Loop) to finish the profile.";
  }
}

canvas.addEventListener("click", (ev) => {
  if (state.isClosed) return;
  const rect = canvas.getBoundingClientRect();
  const sx = ev.clientX - rect.left;
  const sy = ev.clientY - rect.top;
  const world = screenToWorld(sx, sy);
  const rounded = { x: Math.round(world.x * 100) / 100, y: Math.round(world.y * 100) / 100 };

  if (state.points.length >= 3) {
    const first = worldToScreen(state.points[0].x, state.points[0].y);
    const distPx = Math.hypot(sx - first.x, sy - first.y);
    if (distPx <= CLOSE_HIT_PX) {
      closeLoop();
      return;
    }
  }

  state.points.push(rounded);
  render();
  updateToolbarState();
});

canvas.addEventListener("mousemove", (ev) => {
  const rect = canvas.getBoundingClientRect();
  const sx = ev.clientX - rect.left;
  const sy = ev.clientY - rect.top;
  const world = screenToWorld(sx, sy);
  coordReadout.textContent = `${world.x.toFixed(1)}, ${world.y.toFixed(1)} mm`;
  coordReadout.style.left = Math.min(sx + 12, rect.width - 110) + "px";
  coordReadout.style.top = Math.min(sy + 12, rect.height - 24) + "px";
  coordReadout.hidden = false;
});

canvas.addEventListener("mouseleave", () => {
  coordReadout.hidden = true;
});

canvas.addEventListener("keydown", (ev) => {
  if (ev.key === "Backspace" || ev.key === "Delete") {
    ev.preventDefault();
    undo();
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    closeLoop();
  }
});

function undo() {
  if (state.points.length === 0) return;
  if (state.isClosed) {
    state.isClosed = false;
    state.holes = [];
    invalidateSection();
    fitViewToPolygon([]); // back to the default sketching view
    layoutCanvas();
  }
  state.points.pop();
  render();
  updateToolbarState();
}

function closeLoop() {
  if (state.points.length < 3 || state.isClosed) return;
  state.isClosed = true;
  fitViewToPolygon(state.points);
  layoutCanvas();
  updateToolbarState();
  computeSection();
}

function clearSketch() {
  state.points = [];
  state.holes = [];
  state.isClosed = false;
  invalidateSection();
  fitViewToPolygon([]);
  layoutCanvas();
  updateToolbarState();
}

btnUndo.addEventListener("click", undo);
btnCloseLoop.addEventListener("click", closeLoop);
btnClear.addEventListener("click", clearSketch);

/* ------------------------------------------------------------------
   Materials
------------------------------------------------------------------ */

async function loadMaterials() {
  try {
    const materials = await apiFetch("/materials");
    state.materials = materials;
    materialSelect.innerHTML = "";
    materials.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = m.name;
      materialSelect.appendChild(opt);
    });
    materialSelect.disabled = false;
    if (materials.length > 0) {
      state.selectedMaterial = materials[0].name;
    }
  } catch (err) {
    showError(`Could not load materials: ${err.message}`);
  }
}

materialSelect.addEventListener("change", () => {
  state.selectedMaterial = materialSelect.value;
  if (state.isClosed) computeSection();
});

/* ------------------------------------------------------------------
   Section analysis
------------------------------------------------------------------ */

function invalidateSection() {
  state.sectionId = null;
  state.lastSectionResult = null;
  state.highlight = null;
  state.pinnedSuggestion = null;
  sectionResultsEl.hidden = true;
  beamInputsEl.hidden = true;
  beamResultsEl.hidden = true;
  baselineSectionEl.hidden = true;
  suggestionsSectionEl.hidden = true;
}

async function computeSection() {
  if (!state.isClosed || state.points.length < 3 || !state.selectedMaterial) return;
  hideError();
  try {
    const body = await apiFetch("/section", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vertices: state.points.map((p) => [p.x, p.y]),
        holes: state.holes.map((loop) => loop.map((p) => [p.x, p.y])),
        material_name: state.selectedMaterial,
      }),
    });
    state.sectionId = body.section_id;
    renderSectionResults(body);
    sectionResultsEl.hidden = false;
    beamInputsEl.hidden = false;
    beamResultsEl.hidden = true;
  } catch (err) {
    showError(`Section analysis failed: ${err.message}`);
    sectionResultsEl.hidden = true;
    beamInputsEl.hidden = true;
    baselineSectionEl.hidden = true;
  }
}

function renderSectionResults(r) {
  state.lastSectionResult = r;
  sectionResultGridPrimary.innerHTML = "";
  resultRow(sectionResultGridPrimary, "Area", fmtNum(r.area), "mm²");
  resultRow(sectionResultGridPrimary, "Centroid", `${fmtNum(r.cx)}, ${fmtNum(r.cy)}`, "mm");
  resultRow(sectionResultGridPrimary, "Ixx", fmtNum(r.ixx), "mm⁴");
  resultRow(sectionResultGridPrimary, "Iyy", fmtNum(r.iyy), "mm⁴");
  resultRow(sectionResultGridPrimary, "Izz", fmtNum(r.izz), "mm⁴");
  resultRow(
    sectionResultGridPrimary,
    "Mass Per Length",
    r.mass_per_length === null ? "n/a" : fmtNum(r.mass_per_length),
    r.mass_per_length === null ? "" : "kg/m"
  );

  sectionResultGridSecondary.innerHTML = "";
  resultRow(sectionResultGridSecondary, "Ixy", fmtNum(r.ixy), "mm⁴");
  resultRow(sectionResultGridSecondary, "J (Torsion)", fmtNum(r.j), "mm⁴");
  resultRow(sectionResultGridSecondary, "Warping Iw", fmtNum(r.iw), "mm⁶");
  resultRow(sectionResultGridSecondary, "Shear Centre", `${fmtNum(r.x_sc)}, ${fmtNum(r.y_sc)}`, "mm");
  resultRow(sectionResultGridSecondary, "Zxx (+/-)", `${fmtNum(r.zxx_plus)} / ${fmtNum(r.zxx_minus)}`, "mm³");
  resultRow(sectionResultGridSecondary, "Zyy (+/-)", `${fmtNum(r.zyy_plus)} / ${fmtNum(r.zyy_minus)}`, "mm³");
  resultRow(sectionResultGridSecondary, "Sxx (Plastic)", fmtNum(r.sxx), "mm³");
  resultRow(sectionResultGridSecondary, "Syy (Plastic)", fmtNum(r.syy), "mm³");
  resultRow(sectionResultGridSecondary, "EA", fmtNum(r.ea), "N");
  resultRow(sectionResultGridSecondary, "EIxx", fmtNum(r.ei_xx), "N·mm²");
  resultRow(sectionResultGridSecondary, "EIyy", fmtNum(r.ei_yy), "N·mm²");
  resultRow(sectionResultGridSecondary, "GJ", fmtNum(r.gj), "N·mm²");

  renderPdfImportInfo(r);
  renderSolidFillComparison(r);
  refreshBaselineComparison(r);
  refreshSuggestions(r);
}

/** Shows how a profile read off a PDF drawing was interpreted: the scale
 * used and where it came from, the length found, and the dimension-by-
 * dimension check that justifies it.
 *
 * Driven off `r.pdf_import`, which the API sets only for /section/from-pdf.
 * Every other path through renderSectionResults (sketch, DXF, a run
 * reloaded from history) leaves it null, so this panel hides itself --
 * which is what keeps it from lingering with a previous profile's
 * provenance after the profile has changed. */
function renderPdfImportInfo(r) {
  const info = r.pdf_import;
  if (!info) {
    pdfImportSectionEl.hidden = true;
    return;
  }
  pdfImportGridEl.innerHTML = "";
  const tb = info.title_block || {};
  if (tb.part_number) resultRow(pdfImportGridEl, "Part Number", tb.part_number, "");
  if (tb.title) resultRow(pdfImportGridEl, "Drawing Title", tb.title, "");
  if (tb.material) resultRow(pdfImportGridEl, "Drawing Material", tb.material, "");
  resultRow(pdfImportGridEl, "Page", String(info.page), "");
  resultRow(pdfImportGridEl, "Scale", info.scale, "");
  resultRow(pdfImportGridEl, "Scale Source", info.scale_source, "");
  resultRow(
    pdfImportGridEl,
    "Extrusion Length",
    info.length_mm === null ? "not found" : fmtNum(info.length_mm),
    info.length_mm === null ? "" : "mm"
  );
  resultRow(pdfImportGridEl, "Length Source", info.length_source, "");

  const checks = info.dimension_checks || [];
  const agreed = checks.filter((c) => c.agrees).length;
  pdfImportSummaryEl.textContent = `${info.scale}, ${agreed}/${checks.length} dimensions confirmed`;

  pdfDimTableEl.innerHTML = "";
  const head = document.createElement("tr");
  ["Callout", "Stated (mm)", "Measured (mm)", "Error"].forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    head.appendChild(th);
  });
  pdfDimTableEl.appendChild(head);
  checks.forEach((c) => {
    const tr = document.createElement("tr");
    if (!c.agrees) tr.className = "dim-check-table__row--off";
    [
      c.text,
      fmtNum(c.stated, { digits: 3 }),
      fmtNum(c.measured, { digits: 3 }),
      `${c.error_pct >= 0 ? "+" : ""}${fmtNum(c.error_pct, { digits: 2 })}%`,
    ].forEach((v) => {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });
    pdfDimTableEl.appendChild(tr);
  });

  pdfWarningListEl.innerHTML = "";
  (info.warnings || []).forEach((w) => {
    const li = document.createElement("li");
    li.textContent = w;
    pdfWarningListEl.appendChild(li);
  });
  pdfMassCheckEl.textContent = info.mass_check || "";
  pdfMassCheckEl.hidden = !info.mass_check;
  pdfImportSectionEl.hidden = false;
}

// Monotonically increasing token guarding refreshSuggestions against a
// stale (out-of-order) response landing after a newer profile has already
// been requested -- see that function's own comment for why this is
// needed and not just defensive.
let suggestionsRequestId = 0;

/** Fetches the DFM / stiffness suggestions for whatever profile is
 * displayed. Like the solid-fill and baseline comparisons, this is always
 * a fresh derived lookup rather than part of any stored result, so it
 * runs for reloaded history entries too.
 *
 * Not awaited by its caller (renderSectionResults), so two calls can be
 * in flight at once -- e.g. sketch profile A, close the loop, then
 * immediately Clear and sketch profile B before A's /suggestions request
 * resolves. Without the token guard below, A's response landing after
 * B's would append A's (now-stale) findings onto B's already-rendered
 * list, showing suggestions for a profile that's no longer on screen.
 * Confirmed as a real, reproducible bug (not just a theoretical race)
 * during review, via a delayed-response test. */
async function refreshSuggestions(r) {
  const requestId = ++suggestionsRequestId;
  state.highlight = null;
  state.pinnedSuggestion = null;
  suggestionListEl.innerHTML = "";
  try {
    const found = await apiFetch("/suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ section: { vertices: r.vertices, holes: r.holes || [] } }),
    });
    if (requestId !== suggestionsRequestId) return; // a newer profile has since been requested
    suggestionsCountEl.textContent = found.length ? `${found.length}` : "none";
    suggestionsEmptyEl.hidden = found.length > 0;
    suggestionListEl.innerHTML = "";
    found.forEach((suggestion, index) => {
      suggestionListEl.appendChild(renderSuggestion(suggestion, index));
    });
    suggestionsSectionEl.hidden = false;
  } catch (err) {
    if (requestId !== suggestionsRequestId) return;
    suggestionsSectionEl.hidden = true;
  }
}

function renderSuggestion(suggestion, index) {
  const li = document.createElement("li");
  li.className = "suggestion";
  li.dataset.index = String(index);

  const title = document.createElement("div");
  title.className = "suggestion__title";
  title.textContent = suggestion.title;

  const detail = document.createElement("div");
  detail.className = "suggestion__detail";
  detail.textContent = suggestion.detail;

  li.appendChild(title);
  li.appendChild(detail);

  if (suggestion.ring && suggestion.vertex_indices.length) {
    const where = document.createElement("div");
    where.className = "suggestion__where";
    const shown = suggestion.vertex_indices.slice(0, 8).join(", ");
    const more = suggestion.vertex_indices.length > 8 ? ", …" : "";
    where.textContent = `${suggestion.ring} — vertex ${shown}${more}`;
    li.appendChild(where);
  }

  const show = () => {
    state.highlight = suggestion;
    render();
  };
  const clear = () => {
    if (state.pinnedSuggestion !== null) return;
    state.highlight = null;
    render();
  };

  li.addEventListener("mouseenter", () => {
    if (state.pinnedSuggestion === null) show();
  });
  li.addEventListener("mouseleave", clear);
  li.addEventListener("click", () => {
    const alreadyPinned = state.pinnedSuggestion === index;
    suggestionListEl
      .querySelectorAll(".suggestion--pinned")
      .forEach((el) => el.classList.remove("suggestion--pinned"));
    if (alreadyPinned) {
      state.pinnedSuggestion = null;
      state.highlight = null;
    } else {
      state.pinnedSuggestion = index;
      li.classList.add("suggestion--pinned");
      state.highlight = suggestion;
    }
    render();
  });

  return li;
}

/** Shows what the same outer boundary's properties would be with its
 * holes ignored (fully filled) -- a quick read on how much the material
 * removal costs in stiffness/mass vs. what it saves in weight. Reuses
 * POST /section (and hence eat.section.analyze_section) on just the
 * outer `vertices`, no new engineering. Skipped entirely for a hole-free
 * profile, since there's nothing to compare against. */
async function renderSolidFillComparison(r) {
  if (!r.holes || r.holes.length === 0) {
    solidFillComparisonEl.hidden = true;
    solidFillGrid.innerHTML = "";
    return;
  }
  try {
    const solid = await apiFetch("/section", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vertices: r.vertices,
        material_name: state.selectedMaterial,
        save_history: false, // derived lookup, not a user-facing analysis run
      }),
    });
    solidFillGrid.innerHTML = "";
    solidFillRow(solidFillGrid, "Area", r.area, solid.area, "mm²");
    solidFillRow(solidFillGrid, "Ixx", r.ixx, solid.ixx, "mm⁴");
    solidFillRow(solidFillGrid, "Iyy", r.iyy, solid.iyy, "mm⁴");
    if (r.mass_per_length !== null && solid.mass_per_length !== null) {
      solidFillRow(solidFillGrid, "Mass Per Length", r.mass_per_length, solid.mass_per_length, "kg/m");
    }
    solidFillComparisonEl.hidden = false;
  } catch (err) {
    // Best-effort extra: don't surface the main error banner over this.
    solidFillComparisonEl.hidden = true;
    solidFillGrid.innerHTML = "";
  }
}

/** One comparison row: hollow value -> solid-fill value, and the percent
 * change filling the holes back in would make (positive = solid is
 * larger, i.e. what the material removal is costing you). */
function solidFillRow(grid, label, hollowValue, solidValue, unit) {
  const pct = solidValue !== 0 ? ((solidValue - hollowValue) / solidValue) * 100 : 0;
  const sign = pct >= 0 ? "+" : "";
  resultRow(
    grid,
    label,
    `${fmtNum(hollowValue)} → ${fmtNum(solidValue)} (${sign}${fmtNum(pct, { digits: 1 })}%)`,
    unit
  );
}

/* ------------------------------------------------------------------
   DXF import / export
------------------------------------------------------------------ */

btnImportDxf.addEventListener("click", () => dxfFileInput.click());

dxfFileInput.addEventListener("change", async () => {
  const file = dxfFileInput.files[0];
  dxfFileInput.value = "";
  if (!file) return;
  if (!state.selectedMaterial) {
    showError("Load a material before importing a DXF file.");
    return;
  }
  hideError();

  const formData = new FormData();
  formData.append("file", file);
  formData.append("material_name", state.selectedMaterial);

  try {
    const body = await apiFetch("/section/from-dxf", { method: "POST", body: formData });
    state.points = body.vertices.map(([x, y]) => ({ x, y }));
    state.holes = (body.holes || []).map((loop) => loop.map(([x, y]) => ({ x, y })));
    state.isClosed = true;
    state.sectionId = body.section_id;
    fitViewToPolygon(state.points);
    layoutCanvas();
    updateToolbarState();
    renderSectionResults(body);
    sectionResultsEl.hidden = false;
    beamInputsEl.hidden = false;
    beamResultsEl.hidden = true;
  } catch (err) {
    showError(`DXF import failed: ${err.message}`);
  }
});

/* ------------------------------------------------------------------
   PDF drawing import
------------------------------------------------------------------ */

btnImportPdf.addEventListener("click", () => pdfFileInput.click());

pdfFileInput.addEventListener("change", async () => {
  const file = pdfFileInput.files[0];
  pdfFileInput.value = "";
  if (!file) return;
  if (!state.selectedMaterial) {
    showError("Load a material before importing a PDF drawing.");
    return;
  }
  hideError();

  const formData = new FormData();
  formData.append("file", file);
  formData.append("material_name", state.selectedMaterial);

  try {
    const body = await apiFetch("/section/from-pdf", { method: "POST", body: formData });
    state.points = body.vertices.map(([x, y]) => ({ x, y }));
    state.holes = (body.holes || []).map((loop) => loop.map(([x, y]) => ({ x, y })));
    state.isClosed = true;
    state.sectionId = body.section_id;
    fitViewToPolygon(state.points);
    layoutCanvas();
    updateToolbarState();
    renderSectionResults(body);
    sectionResultsEl.hidden = false;
    beamInputsEl.hidden = false;
    beamResultsEl.hidden = true;
    // The drawing states the bar's cut length; prefill it so a beam run
    // starts from the real part rather than an arbitrary number. Only when
    // the field is empty, so a length the user has already typed is never
    // overwritten.
    const length = body.pdf_import && body.pdf_import.length_mm;
    if (length && !inputLength.value) inputLength.value = String(length);
  } catch (err) {
    // The importer's refusals explain which drawing feature defeated it;
    // they are the useful part of the message, so pass them through whole
    // rather than collapsing to "import failed".
    showError(`PDF import failed: ${err.message}`);
  }
});

btnExportDxf.addEventListener("click", async () => {
  if (!state.isClosed) return;
  hideError();
  try {
    const res = await fetch("/section/to-dxf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vertices: state.points.map((p) => [p.x, p.y]) }),
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        if (body && body.detail) detail = body.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "profile.dxf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showError(`DXF export failed: ${err.message}`);
  }
});

/* ------------------------------------------------------------------
   Point loads UI
------------------------------------------------------------------ */

function renderPointLoads() {
  pointLoadsListEl.innerHTML = "";
  state.pointLoads.forEach((load, idx) => {
    const row = document.createElement("div");
    row.className = "point-load-row";

    const posInput = document.createElement("input");
    posInput.type = "number";
    posInput.className = "input";
    posInput.min = "0";
    posInput.max = "1";
    posInput.step = "0.01";
    posInput.placeholder = "position (0-1)";
    posInput.value = load.position_fraction;
    posInput.addEventListener("input", () => {
      state.pointLoads[idx].position_fraction = parseFloat(posInput.value);
    });

    const magInput = document.createElement("input");
    magInput.type = "number";
    magInput.className = "input";
    magInput.step = "50"; // spinner step only -- typing remains free-form
    magInput.placeholder = "magnitude (N)";
    magInput.value = load.magnitude;
    magInput.addEventListener("input", () => {
      state.pointLoads[idx].magnitude = parseFloat(magInput.value);
    });

    // All point loads in one analysis must share one axis (see
    // eat.beam.analyze_beam); this per-row select keeps the axis explicit
    // on the load itself rather than a single header note, and changing
    // it here re-syncs every other row so the shared-axis invariant holds
    // without the backend having to reject a mismatched combination.
    const axisSelect = document.createElement("select");
    axisSelect.className = "select point-load-row__axis";
    axisSelect.setAttribute("aria-label", "Load axis");
    ["y", "x"].forEach((axis) => {
      const opt = document.createElement("option");
      opt.value = axis;
      opt.textContent = axis === "y" ? "Y (bends Ixx)" : "X (bends Iyy)";
      axisSelect.appendChild(opt);
    });
    axisSelect.value = load.axis || "y";
    axisSelect.addEventListener("change", () => {
      state.pointLoads.forEach((l) => {
        l.axis = axisSelect.value;
      });
      renderPointLoads();
    });

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "point-load-row__remove";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", "Remove load");
    removeBtn.addEventListener("click", () => {
      state.pointLoads.splice(idx, 1);
      renderPointLoads();
    });

    row.appendChild(posInput);
    row.appendChild(magInput);
    row.appendChild(axisSelect);
    row.appendChild(removeBtn);
    pointLoadsListEl.appendChild(row);
  });
}

btnAddLoad.addEventListener("click", () => {
  const sharedAxis = state.pointLoads.length > 0 ? state.pointLoads[0].axis : "y";
  state.pointLoads.push({ position_fraction: 0.5, magnitude: -1000, axis: sharedAxis });
  renderPointLoads();
});

/* ------------------------------------------------------------------
   Beam analysis
------------------------------------------------------------------ */

function safetyFactorClass(sf) {
  if (sf === null || sf === undefined || !Number.isFinite(sf)) return "";
  if (sf < 1.0) return "sf-danger";
  if (sf < 1.5) return "sf-warning";
  return "sf-good";
}

btnAnalyzeBeam.addEventListener("click", async () => {
  if (!state.isClosed || state.points.length < 3 || !state.selectedMaterial) {
    showError("Close a profile and select a material before analyzing a beam.");
    return;
  }
  const length = parseFloat(inputLength.value);
  if (!(length > 0)) {
    showError("Enter a positive beam length (mm) before analyzing.");
    return;
  }
  const validLoads = state.pointLoads.filter(
    (l) => Number.isFinite(l.position_fraction) && Number.isFinite(l.magnitude)
  );
  if (validLoads.length === 0) {
    showError("Add at least one valid point load before analyzing.");
    return;
  }
  const axialRaw = inputAxial.value;
  const axialLoad = axialRaw === "" ? null : parseFloat(axialRaw);

  hideError();
  btnAnalyzeBeam.disabled = true;
  try {
    if (!state.sectionId) {
      // Profile was reloaded from history (or some other path that never
      // hit computeSection()) and never got a fresh section_id -- get one
      // silently. This is just plumbing to satisfy /beam's section_id
      // requirement, not a user-facing analysis run in its own right, so
      // it doesn't get its own history entry -- the /beam call right
      // below (which always logs) is the real record of what happened.
      const sectionBody = await apiFetch("/section", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vertices: state.points.map((p) => [p.x, p.y]),
          holes: state.holes.map((loop) => loop.map((p) => [p.x, p.y])),
          material_name: state.selectedMaterial,
          save_history: false,
        }),
      });
      state.sectionId = sectionBody.section_id;
    }
    const body = await apiFetch("/beam", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        section_id: state.sectionId,
        material_name: state.selectedMaterial,
        length,
        boundary_condition: inputBc.value,
        point_loads: validLoads,
        axial_load: axialLoad,
      }),
    });
    renderBeamResults(body);
    beamResultsEl.hidden = false;
  } catch (err) {
    showError(`Beam analysis failed: ${err.message}`);
    beamResultsEl.hidden = true;
  } finally {
    btnAnalyzeBeam.disabled = false;
  }
});

function summaryTile(grid, label, value, unit, opts = {}) {
  const wrap = document.createElement("div");
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  if (unit) dd.textContent += ` ${unit}`;
  if (opts.highlight) dd.classList.add("highlight");
  if (opts.className) dd.classList.add(opts.className);
  wrap.appendChild(dt);
  wrap.appendChild(dd);
  if (opts.sub) {
    const sub = document.createElement("div");
    sub.className = "tile-sub";
    sub.textContent = opts.sub;
    wrap.appendChild(sub);
  }
  grid.appendChild(wrap);
}

function renderBeamResults(r) {
  beamSummaryGrid.innerHTML = "";
  summaryTile(
    beamSummaryGrid,
    "Load Axis",
    r.load_axis === "x" ? "X (bends about Iyy)" : "Y (bends about Ixx)"
  );
  summaryTile(beamSummaryGrid, "Max Moment", `${fmtNum(r.max_moment)} N·mm`, "", {
    sub: `@ ${fmtNum(r.max_moment_position)}mm`,
  });
  summaryTile(beamSummaryGrid, "Max Bending Stress", `${fmtNum(r.max_bending_stress)} MPa`);
  summaryTile(
    beamSummaryGrid,
    "Safety Factor",
    r.safety_factor === null ? "n/a" : fmtNum(r.safety_factor, { digits: 2 }),
    "",
    { highlight: true, className: safetyFactorClass(r.safety_factor) }
  );
  summaryTile(beamSummaryGrid, "Max Deflection", `${fmtNum(r.max_deflection, { digits: 4 })} mm`, "", {
    sub: `@ ${fmtNum(r.max_deflection_position)}mm`,
  });
  summaryTile(beamSummaryGrid, "Euler Buckling Load", `${fmtNum(r.euler_buckling_load)} N  (K=${r.effective_length_factor})`);
  if (r.buckling_safety_factor !== null) {
    summaryTile(
      beamSummaryGrid,
      "Buckling Safety Factor",
      fmtNum(r.buckling_safety_factor, { digits: 2 }),
      "",
      { className: safetyFactorClass(r.buckling_safety_factor) }
    );
  }
  r.reactions.forEach((reaction) => {
    summaryTile(
      beamSummaryGrid,
      titleCaseLabel(reaction.label),
      `${fmtNum(reaction.force)} N, ${fmtNum(reaction.moment)} N·mm`
    );
  });

  drawLineChart(chartStressEl, r.diagram_x, r.bending_stress_diagram, {
    xLabel: "Position Along Length",
    xUnit: "mm",
    yLabel: "Bending Stress",
    yUnit: "MPa",
    markerX: r.max_bending_stress_position,
    markerY: r.max_bending_stress,
    forceZeroBaseline: true,
  });

  drawLineChart(chartDeflectionEl, r.diagram_x, r.deflection_diagram, {
    xLabel: "Position Along Length",
    xUnit: "mm",
    yLabel: "Deflection",
    yUnit: "mm",
    markerX: r.max_deflection_position,
    markerY: r.max_deflection,
    forceZeroBaseline: false,
  });
}

/* ------------------------------------------------------------------
   Custom SVG line chart — no charting library
------------------------------------------------------------------ */

function niceTicks(min, max, count) {
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const span = max - min;
  const rawStep = span / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const residual = rawStep / magnitude;
  let step;
  if (residual > 5) step = 10 * magnitude;
  else if (residual > 2) step = 5 * magnitude;
  else if (residual > 1) step = 2 * magnitude;
  else step = magnitude;

  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  const ticks = [];
  for (let t = niceMin; t <= niceMax + step / 2; t += step) {
    ticks.push(Math.round(t / step) * step);
  }
  return { ticks, niceMin, niceMax };
}

function drawLineChart(container, xValues, yValues, opts) {
  const W = 480;
  const H = 260;
  const padLeft = 62;
  const padRight = 16;
  const padTop = 16;
  const padBottom = 42;
  const plotW = W - padLeft - padRight;
  const plotH = H - padTop - padBottom;

  let yMin = Math.min(...yValues);
  let yMax = Math.max(...yValues);
  if (opts.forceZeroBaseline || (yMin < 0 && yMax > 0)) {
    yMin = Math.min(yMin, 0);
    yMax = Math.max(yMax, 0);
  }
  const { ticks: yTicks, niceMin: yNiceMin, niceMax: yNiceMax } = niceTicks(yMin, yMax, 4);
  const xMin = xValues[0];
  const xMax = xValues[xValues.length - 1];
  const { ticks: xTicks } = niceTicks(xMin, xMax, 5);

  const sx = (x) => padLeft + ((x - xMin) / (xMax - xMin)) * plotW;
  const sy = (y) => padTop + plotH - ((y - yNiceMin) / (yNiceMax - yNiceMin)) * plotH;

  let svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;

  // Horizontal gridlines + y tick labels.
  yTicks.forEach((t) => {
    if (t < yNiceMin - 1e-9 || t > yNiceMax + 1e-9) return;
    const y = sy(t);
    const isZero = Math.abs(t) < 1e-9;
    svg += `<line x1="${padLeft}" y1="${y}" x2="${W - padRight}" y2="${y}" class="${isZero ? "chart-zero-line" : "chart-gridline"}" />`;
    svg += `<text x="${padLeft - 8}" y="${y + 3}" text-anchor="end" class="chart-tick-label">${fmtNum(t)}</text>`;
  });

  // Vertical gridlines + x tick labels.
  xTicks.forEach((t) => {
    if (t < xMin - 1e-9 || t > xMax + 1e-9) return;
    const x = sx(t);
    svg += `<line x1="${x}" y1="${padTop}" x2="${x}" y2="${H - padBottom}" class="chart-gridline" />`;
    svg += `<text x="${x}" y="${H - padBottom + 16}" text-anchor="middle" class="chart-tick-label">${fmtNum(t)}</text>`;
  });

  // Axes.
  svg += `<line x1="${padLeft}" y1="${padTop}" x2="${padLeft}" y2="${H - padBottom}" class="chart-axis" />`;
  svg += `<line x1="${padLeft}" y1="${H - padBottom}" x2="${W - padRight}" y2="${H - padBottom}" class="chart-axis" />`;

  // Data line.
  const pathD = xValues
    .map((x, i) => `${i === 0 ? "M" : "L"} ${sx(x).toFixed(2)} ${sy(yValues[i]).toFixed(2)}`)
    .join(" ");
  svg += `<path d="${pathD}" class="chart-line" />`;

  // Marker at the governing (max-magnitude) point.
  if (opts.markerX !== undefined && opts.markerY !== undefined) {
    const mx = sx(opts.markerX);
    const my = sy(opts.markerY);
    svg += `<circle cx="${mx}" cy="${my}" r="4" class="chart-marker" />`;
  }

  // Axis titles.
  svg += `<text x="${padLeft + plotW / 2}" y="${H - 6}" text-anchor="middle" class="chart-axis-label">${opts.xLabel} (${opts.xUnit})</text>`;
  svg += `<text x="14" y="${padTop + plotH / 2}" text-anchor="middle" class="chart-axis-label" transform="rotate(-90 14 ${padTop + plotH / 2})">${opts.yLabel} (${opts.yUnit})</text>`;

  svg += "</svg>";
  container.innerHTML = svg;
}

/* ------------------------------------------------------------------
   Run history
------------------------------------------------------------------ */

function fmtTimestamp(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

async function openHistory() {
  hideError();
  historyModal.hidden = false;
  historyListEl.innerHTML = "";
  historyEmptyHint.hidden = true;
  try {
    const [entries, currentBaseline] = await Promise.all([
      apiFetch("/history"),
      apiFetch("/baseline").catch(() => null), // best-effort: still show the list if this fails
    ]);
    const currentBaselineEntryId = currentBaseline ? currentBaseline.history_entry_id : null;
    if (entries.length === 0) {
      historyEmptyHint.hidden = false;
      return;
    }
    entries.forEach((entry) =>
      historyListEl.appendChild(renderHistoryRow(entry, entry.id === currentBaselineEntryId))
    );
  } catch (err) {
    showError(`Could not load history: ${err.message}`);
  }
}

function closeHistory() {
  historyModal.hidden = true;
}

function renderHistoryRow(summary, isCurrentBaseline) {
  const li = document.createElement("li");
  li.className = "history-row";

  const main = document.createElement("button");
  main.type = "button";
  main.className = "history-row__main";

  const timestamp = document.createElement("div");
  timestamp.className = "history-row__timestamp";
  timestamp.textContent = fmtTimestamp(summary.created_at);

  const material = document.createElement("div");
  material.className = "history-row__material";
  material.textContent = summary.material;

  const meta = document.createElement("div");
  meta.className = "history-row__meta";
  const areaSpan = document.createElement("span");
  areaSpan.textContent = `${fmtNum(summary.area)} mm²`;
  meta.appendChild(areaSpan);
  if (summary.has_holes) {
    const tag = document.createElement("span");
    tag.className = "history-row__tag";
    tag.textContent = "holes";
    meta.appendChild(tag);
  }
  if (summary.has_beam) {
    const tag = document.createElement("span");
    tag.className = "history-row__tag";
    tag.textContent = "beam";
    meta.appendChild(tag);
  }
  if (isCurrentBaseline) {
    const tag = document.createElement("span");
    tag.className = "history-row__tag history-row__tag--baseline";
    tag.textContent = "baseline";
    meta.appendChild(tag);
  }

  main.appendChild(timestamp);
  main.appendChild(material);
  main.appendChild(meta);
  main.addEventListener("click", () => loadHistorySelection(summary.id));

  const actions = document.createElement("div");
  actions.className = "history-row__actions";

  const baselineBtn = document.createElement("button");
  baselineBtn.type = "button";
  baselineBtn.className = "btn btn--small btn--ghost";
  baselineBtn.textContent = isCurrentBaseline ? "Baseline" : "Set as Baseline";
  baselineBtn.disabled = isCurrentBaseline;
  baselineBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    try {
      await apiFetch("/baseline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "history", entry_id: summary.id }),
      });
      await openHistory(); // re-render the list so the "baseline" tag moves
      refreshBaselineComparison(state.lastSectionResult);
    } catch (err) {
      showError(`Could not set baseline: ${err.message}`);
    }
  });

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "history-row__delete";
  deleteBtn.textContent = "×";
  deleteBtn.setAttribute("aria-label", "Delete history entry");
  deleteBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    try {
      await apiFetch(`/history/${summary.id}`, { method: "DELETE" });
      li.remove();
      if (historyListEl.children.length === 0) historyEmptyHint.hidden = false;
      if (isCurrentBaseline) refreshBaselineComparison(state.lastSectionResult);
    } catch (err) {
      showError(`Could not delete history entry: ${err.message}`);
    }
  });

  actions.appendChild(baselineBtn);
  actions.appendChild(deleteBtn);
  li.appendChild(main);
  li.appendChild(actions);
  return li;
}

async function loadHistorySelection(id) {
  try {
    const entry = await apiFetch(`/history/${id}`);
    loadHistoryEntry(entry);
    closeHistory();
  } catch (err) {
    showError(`Could not load history entry: ${err.message}`);
  }
}

/** Reproduces a past run's sketch/results view exactly -- renders the
 * *stored* section/beam results directly rather than recomputing them,
 * so it stays accurate even if the material (or the profile, re-run
 * since) would now give different numbers. `state.sectionId` is
 * deliberately left unset: a later "Analyze Beam" click fetches a fresh
 * one on demand (see that handler) rather than this reload silently
 * creating its own history entry just from being viewed. */
function loadHistoryEntry(entry) {
  hideError();
  state.points = entry.vertices.map(([x, y]) => ({ x, y }));
  state.holes = (entry.holes || []).map((loop) => loop.map(([x, y]) => ({ x, y })));
  state.isClosed = true;
  state.sectionId = null;
  fitViewToPolygon(state.points);
  layoutCanvas();
  updateToolbarState();

  if (state.materials.some((m) => m.name === entry.material)) {
    state.selectedMaterial = entry.material;
    materialSelect.value = entry.material;
  }

  // section_result is the bare eat.section.SectionResult dict (no
  // vertices/holes of its own -- those live on the entry) -- add them so
  // renderSolidFillComparison behaves the same as it does live. That
  // comparison is always a fresh lookup even in the live flow (never
  // part of the frozen result), so recomputing it here doesn't touch the
  // primary hollow-section numbers this function is about reproducing
  // exactly.
  renderSectionResults({ ...entry.section_result, vertices: entry.vertices, holes: entry.holes });
  sectionResultsEl.hidden = false;
  beamInputsEl.hidden = false;

  if (entry.beam_result && entry.beam_request) {
    inputLength.value = entry.beam_request.length;
    inputBc.value = entry.beam_request.boundary_condition;
    state.pointLoads = (entry.beam_request.point_loads || []).map((pl) => ({ ...pl }));
    renderPointLoads();
    const axial = entry.beam_request.axial_load;
    inputAxial.value = axial === null || axial === undefined ? "" : axial;
    renderBeamResults(entry.beam_result);
    beamResultsEl.hidden = false;
  } else {
    beamResultsEl.hidden = true;
  }
}

btnOpenHistory.addEventListener("click", openHistory);
btnCloseHistory.addEventListener("click", closeHistory);
historyModal.addEventListener("click", (ev) => {
  if (ev.target === historyModal) closeHistory();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !historyModal.hidden) closeHistory();
});

/* ------------------------------------------------------------------
   Baseline comparison
------------------------------------------------------------------ */

/** Governing (worst-case, smaller) section modulus for a bending axis --
 * same convention eat.beam uses for max_bending_stress: the fibre
 * furthest from the neutral axis on the side with less material governs
 * the section's actual capacity. */
function zWorst(plus, minus) {
  return Math.min(plus, minus);
}

/** Builds the six stiffness/strength-to-weight metrics (plus mass per
 * length) comparing `sectionResult`/`material` against baseline response
 * `b`. Strength metrics need a yield strength on *both* sides; skipped
 * (not shown with a placeholder) when either is missing, since there's
 * nothing meaningful to compare otherwise. Grouped to match the section
 * headings in the UI. */
function buildBaselineMetricGroups(sectionResult, material, b) {
  const groups = [];
  const massKnown = sectionResult.mass_per_length !== null && b.mass_per_length !== null;

  if (massKnown) {
    groups.push({
      title: null,
      metrics: [
        {
          label: "Mass Per Length",
          unit: "kg/m",
          profile: sectionResult.mass_per_length,
          baseline: b.mass_per_length,
        },
      ],
    });
  }

  if (massKnown) {
    groups.push({
      title: "Stiffness-to-Weight",
      metrics: [
        {
          label: "Axial",
          unit: "N/(kg/m)",
          profile: sectionResult.ea / sectionResult.mass_per_length,
          baseline: b.ea / b.mass_per_length,
        },
        {
          label: "Bending (X)",
          unit: "N·mm²/(kg/m)",
          profile: sectionResult.ei_yy / sectionResult.mass_per_length,
          baseline: b.ei_yy / b.mass_per_length,
        },
        {
          label: "Bending (Y)",
          unit: "N·mm²/(kg/m)",
          profile: sectionResult.ei_xx / sectionResult.mass_per_length,
          baseline: b.ei_xx / b.mass_per_length,
        },
      ],
    });
  }

  const yieldStrength = material ? material.yield_strength : null;
  const strengthKnown =
    massKnown &&
    yieldStrength !== null &&
    yieldStrength !== undefined &&
    b.yield_strength !== null &&
    b.yield_strength !== undefined;

  if (strengthKnown) {
    const zWorstYyProfile = zWorst(sectionResult.zyy_plus, sectionResult.zyy_minus);
    const zWorstXxProfile = zWorst(sectionResult.zxx_plus, sectionResult.zxx_minus);
    const zWorstYyBaseline = zWorst(b.zyy_plus, b.zyy_minus);
    const zWorstXxBaseline = zWorst(b.zxx_plus, b.zxx_minus);
    groups.push({
      title: "Strength-to-Weight",
      metrics: [
        {
          label: "Axial",
          unit: "N/(kg/m)",
          profile: (yieldStrength * sectionResult.area) / sectionResult.mass_per_length,
          baseline: (b.yield_strength * b.area) / b.mass_per_length,
        },
        {
          label: "Bending (X)",
          unit: "N·mm/(kg/m)",
          profile: (yieldStrength * zWorstYyProfile) / sectionResult.mass_per_length,
          baseline: (b.yield_strength * zWorstYyBaseline) / b.mass_per_length,
        },
        {
          label: "Bending (Y)",
          unit: "N·mm/(kg/m)",
          profile: (yieldStrength * zWorstXxProfile) / sectionResult.mass_per_length,
          baseline: (b.yield_strength * zWorstXxBaseline) / b.mass_per_length,
        },
      ],
    });
  }

  return groups;
}

/** One metric's dual bars: profile on top, baseline below, both widths
 * normalized to their own shared max (not a single scale across metrics
 * -- these are different units/magnitudes and aren't meant to be visually
 * compared to each other). Raw values are labeled directly, not a
 * percentage. */
function renderBaselineMetric(metric) {
  const wrap = document.createElement("div");
  wrap.className = "baseline-metric";

  const label = document.createElement("div");
  label.className = "baseline-metric__label";
  label.textContent = metric.label;
  wrap.appendChild(label);

  const barMax = Math.max(metric.profile, metric.baseline, Number.MIN_VALUE);
  wrap.appendChild(baselineMetricRow("Profile", metric.profile, barMax, metric.unit, "profile"));
  wrap.appendChild(baselineMetricRow("Baseline", metric.baseline, barMax, metric.unit, "baseline"));
  return wrap;
}

function baselineMetricRow(rowLabel, value, barMax, unit, variant) {
  const row = document.createElement("div");
  row.className = "baseline-metric__row";

  const rowLabelEl = document.createElement("span");
  rowLabelEl.className = "baseline-metric__row-label";
  rowLabelEl.textContent = rowLabel;

  const track = document.createElement("div");
  track.className = "baseline-metric__bar-track";
  const bar = document.createElement("div");
  bar.className = `baseline-metric__bar baseline-metric__bar--${variant}`;
  const widthPct = barMax > 0 ? Math.max(0, Math.min(100, (value / barMax) * 100)) : 0;
  bar.style.width = `${widthPct}%`;
  track.appendChild(bar);

  const valueEl = document.createElement("span");
  valueEl.className = "baseline-metric__row-value";
  valueEl.textContent = `${fmtNum(value)} ${unit}`;

  row.appendChild(rowLabelEl);
  row.appendChild(track);
  row.appendChild(valueEl);
  return row;
}

function renderBaselineMetrics(groups) {
  baselineMetricsEl.innerHTML = "";
  groups.forEach((group) => {
    if (group.title) {
      const h = document.createElement("h4");
      h.className = "panel__subtitle panel__subtitle--sub baseline-metric-group__title";
      h.textContent = group.title;
      baselineMetricsEl.appendChild(h);
    }
    group.metrics.forEach((metric) => baselineMetricsEl.appendChild(renderBaselineMetric(metric)));
  });
}

/** Refreshes the "Compared to Baseline" section for whatever's currently
 * displayed. Always a fresh, live lookup against the current baseline
 * setting and the currently-selected material -- like the solid-fill
 * comparison, this was never part of any *frozen* stored result, so
 * recomputing it (including for a reloaded history entry) doesn't touch
 * the primary numbers those flows are about reproducing exactly. */
async function refreshBaselineComparison(sectionResult) {
  if (!sectionResult) {
    baselineSectionEl.hidden = true;
    return;
  }
  try {
    const b = await apiFetch("/baseline");
    baselineNameEl.textContent = b.name;
    const material = state.materials.find((m) => m.name === state.selectedMaterial) || null;
    renderBaselineMetrics(buildBaselineMetricGroups(sectionResult, material, b));
    baselineSectionEl.hidden = false;
  } catch (err) {
    baselineSectionEl.hidden = true;
  }
}

btnUseBuiltinBaseline.addEventListener("click", async () => {
  try {
    await apiFetch("/baseline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "builtin" }),
    });
    refreshBaselineComparison(state.lastSectionResult);
  } catch (err) {
    showError(`Could not reset baseline: ${err.message}`);
  }
});

/* ------------------------------------------------------------------
   Init
------------------------------------------------------------------ */

renderPointLoads();
updateToolbarState();
loadMaterials();
layoutCanvas();

let resizeTimeout;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimeout);
  resizeTimeout = setTimeout(layoutCanvas, 100);
});

if (window.ResizeObserver) {
  new ResizeObserver(() => layoutCanvas()).observe(canvasWell);
}
