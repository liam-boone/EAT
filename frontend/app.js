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
const beamBucklingGrid = document.getElementById("beam-buckling-grid");
const bucklingNoAxialEl = document.getElementById("buckling-no-axial");
const localBucklingSectionEl = document.getElementById("local-buckling-section");
const localBucklingTableEl = document.getElementById("local-buckling-table");
const busyIndicator = document.getElementById("busy-indicator");
const busyIndicatorText = document.getElementById("busy-indicator-text");
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
const historyBaselineNameEl = document.getElementById("history-baseline-name");
const btnModalUseBuiltin = document.getElementById("btn-modal-use-builtin");

const baselineSectionEl = document.getElementById("baseline-section");
const baselineNameEl = document.getElementById("baseline-name");
const topbarBaselineNameEl = document.getElementById("topbar-baseline-name");
const btnTopbarBaseline = document.getElementById("btn-topbar-baseline");
const btnChangeBaseline = document.getElementById("btn-change-baseline");
const btnUseBuiltinBaseline = document.getElementById("btn-use-builtin-baseline");
const baselineMetricsEl = document.getElementById("baseline-metrics");

const suggestionsSectionEl = document.getElementById("suggestions-section");
const suggestionsCountEl = document.getElementById("suggestions-count");
const dfmListEl = document.getElementById("dfm-list");
const dfmEmptyEl = document.getElementById("dfm-empty");
const structuralListEl = document.getElementById("structural-list");
const structuralEmptyEl = document.getElementById("structural-empty");

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
   Busy indicator

   Section analysis on a real imported profile takes 1-3 s, and a full
   pass (section, then suggestions, baseline and beam) runs to about 5 s.
   Until now nothing on screen said so: the canvas just sat there, which
   reads as "the click didn't register" rather than "this is working".

   Reference-counted rather than a boolean, because several of these
   overlap by design -- renderSectionResults fires the suggestions,
   solid-fill and baseline lookups without awaiting them -- so the last
   one to finish has to be the one that clears it.
------------------------------------------------------------------ */

let busyCount = 0;

function setBusy(on, label) {
  busyCount = Math.max(0, busyCount + (on ? 1 : -1));
  const active = busyCount > 0;
  if (active && label) busyIndicatorText.textContent = label;
  busyIndicator.hidden = !active;
  document.body.classList.toggle("is-busy", active);
}

/** Runs `fn` with the busy indicator up, and takes it down again however
 * `fn` ends -- a failed analysis must not leave the app looking stuck. */
async function withBusy(label, fn) {
  setBusy(true, label);
  try {
    return await fn();
  } finally {
    setBusy(false);
  }
}

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
 * the profile so it reads against the accent-coloured outline. Painted in
 * the finding's design-review category colour (the same two the cards use)
 * rather than one shared warning colour, so the highlight still says which
 * kind of finding it is once the eye is on the canvas. */
const HIGHLIGHT_COLORS = {
  dfm: { stroke: "#FFB84D", fill: "rgba(255, 184, 77, 0.25)" }, // --dfm / --warning
  structural: { stroke: "#8FA6FF", fill: "rgba(143, 166, 255, 0.25)" }, // --structural
};

function drawSuggestionHighlight() {
  const highlight = state.highlight;
  if (!highlight) return;

  const color = HIGHLIGHT_COLORS[highlight.category] || HIGHLIGHT_COLORS.dfm;
  ctx.save();
  ctx.strokeStyle = color.stroke;
  ctx.fillStyle = color.fill;
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
// A click this close to the vertex just placed is a double-click, not a
// second vertex. Smaller than CLOSE_HIT_PX so it can never swallow a
// deliberate short edge that the close-loop test would have caught.
const COINCIDENT_HIT_PX = 6;
// Below this |Ixy|/sqrt(Ixx*Iyy) the section is symmetric enough that the
// out-of-plane response is numerical dust, and reporting it is noise.
const ASYMMETRY_VISIBLE = 1e-6;
// The two "there is no safety factor" states, which are different facts
// and used to render identically as "n/a".
const SAFETY_FACTOR_TEXT = {
  no_yield_strength: { value: "n/a", why: "this material has no yield strength on file" },
  no_stress: { value: "∞", why: "this load case produces no bending stress" },
};

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

  // Ignore a click that lands on the vertex just placed. A double-click
  // otherwise puts two coincident vertices down: harmless to the engine,
  // which accepts them, but the outline and the vertex count then say
  // something the drawing doesn't, and it takes two undos to clear one
  // apparent point. Checked in SCREEN space so the tolerance means the
  // same thing at every zoom level, and only against the LAST point, so
  // deliberately revisiting an earlier vertex still works.
  if (state.points.length > 0) {
    const last = worldToScreen(
      state.points[state.points.length - 1].x,
      state.points[state.points.length - 1].y
    );
    if (Math.hypot(sx - last.x, sy - last.y) <= COINCIDENT_HIT_PX) return;
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

// Monotonically increasing token guarding computeSection against an
// out-of-order response, exactly as suggestionsRequestId does below. This
// one matters more, because it is the token everything else hangs off:
// a stale response here doesn't just render stale numbers, it also sets
// state.sectionId and hands its stale profile to renderSectionResults,
// which seeds the baseline, solid-fill and suggestion lookups in turn.
let sectionRequestId = 0;

function invalidateSection() {
  // Also retires any /section request still in flight, so a response for
  // the profile we just abandoned cannot re-open the panel behind us.
  sectionRequestId += 1;
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

/** Analyzes the current profile + material, and renders the result.
 *
 * Not serialized by its callers: changing the material fires one of these
 * while the previous one may still be in flight, and a real profile takes
 * 1-3 seconds on the server. Without the token below, whichever response
 * arrives LAST wins regardless of which was asked for last. Both halves of
 * that were reproduced in the browser during review, with a held-back
 * response: selecting ABS then 6061 left the dropdown reading 6061 while
 * the panel showed ABS's numbers (EA 2.7e7 against 8.2e8 N, 12.3 against
 * 32.0 kg/m); clearing a large profile and sketching a small one left the
 * panel reporting the old 19,572 mm^2 next to a canvas showing 2,175. */
async function computeSection() {
  if (!state.isClosed || state.points.length < 3 || !state.selectedMaterial) return;
  const requestId = ++sectionRequestId;
  hideError();
  setBusy(true, "Analyzing section\u2026");
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
    if (requestId !== sectionRequestId) return; // a newer profile/material has since been requested
    state.sectionId = body.section_id;
    renderSectionResults(body);
    sectionResultsEl.hidden = false;
    beamInputsEl.hidden = false;
    beamResultsEl.hidden = true;
  } catch (err) {
    // A superseded request that fails must not tear down the newer one's
    // results, nor raise a banner about a profile no longer on screen.
    if (requestId !== sectionRequestId) return;
    showError(`Section analysis failed: ${err.message}`);
    sectionResultsEl.hidden = true;
    beamInputsEl.hidden = true;
    baselineSectionEl.hidden = true;
  } finally {
    setBusy(false);
  }
}

/** The main view answers the two questions asked of every profile before
 * anything else -- how much metal is in the section, and what a metre of
 * it weighs. Everything else, the second moments included, is a follow-up
 * question and lives under "More Info": it is all still one click away,
 * and keeping the top of this panel to two lines is what lets the
 * baseline comparison below it sit above the fold. */
function renderSectionResults(r) {
  state.lastSectionResult = r;
  sectionResultGridPrimary.innerHTML = "";
  resultRow(sectionResultGridPrimary, "Area", fmtNum(r.area), "mm²");
  resultRow(
    sectionResultGridPrimary,
    "Mass Per Length",
    r.mass_per_length === null ? "n/a" : fmtNum(r.mass_per_length),
    r.mass_per_length === null ? "" : "kg/m"
  );

  sectionResultGridSecondary.innerHTML = "";
  resultRow(sectionResultGridSecondary, "Centroid", `${fmtNum(r.cx)}, ${fmtNum(r.cy)}`, "mm");
  resultRow(sectionResultGridSecondary, "Ixx", fmtNum(r.ixx), "mm⁴");
  resultRow(sectionResultGridSecondary, "Iyy", fmtNum(r.iyy), "mm⁴");
  resultRow(sectionResultGridSecondary, "Izz", fmtNum(r.izz), "mm⁴");
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
  setBusy(true, "Running design review\u2026");
  state.highlight = null;
  state.pinnedSuggestion = null;
  dfmListEl.innerHTML = "";
  structuralListEl.innerHTML = "";
  try {
    const found = await apiFetch("/suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ section: { vertices: r.vertices, holes: r.holes || [] } }),
    });
    if (requestId !== suggestionsRequestId) return; // a newer profile has since been requested
    suggestionsCountEl.textContent = found.length ? `${found.length} finding${found.length === 1 ? "" : "s"}` : "nothing flagged";
    dfmListEl.innerHTML = "";
    structuralListEl.innerHTML = "";
    let dfmCount = 0;
    let structuralCount = 0;
    // Index is the whole list's index, not the category's: it keys the
    // pinned-highlight state, which is global across both lists (only one
    // finding is ever drawn on the canvas at a time).
    found.forEach((suggestion, index) => {
      const category = suggestionCategory(suggestion.kind);
      const list = category === "dfm" ? dfmListEl : structuralListEl;
      list.appendChild(renderSuggestion(suggestion, index, category));
      if (category === "dfm") dfmCount++;
      else structuralCount++;
    });
    dfmEmptyEl.hidden = dfmCount > 0;
    structuralEmptyEl.hidden = structuralCount > 0;
    suggestionsSectionEl.hidden = false;
  } catch (err) {
    if (requestId !== suggestionsRequestId) return;
    suggestionsSectionEl.hidden = true;
  } finally {
    setBusy(false);
  }
}

/** Which of the two design-review columns a finding belongs in.
 *
 * The split is by what you do about it, not by what physics the check
 * used. DFM findings are answered by changing the drawing so the die can
 * make the part -- wall thickness in both directions, unfilleted internal
 * corners (a die tongue that chips), and notches that leave less than a
 * manufacturable wall. Structural findings are answered by moving
 * material: the section is more lopsided than its envelope requires, or
 * mass is sitting on the neutral axis earning nothing.
 *
 * Sharp corners are a stress riser *and* a die-life problem; they sit
 * under DFM because the fix ("put a 0.5-1mm radius on it") is a
 * manufacturing change either way. Buckling -- Euler and per-wall plate
 * both -- is deliberately NOT here: it needs a length and a load case, so
 * it is reported in Beam Results, and the Structural column says so
 * rather than leaving the reader to wonder where it went.
 *
 * An unrecognised kind (a check added to eat.suggestions without this
 * list being updated) falls to DFM, which is where the manufacturing-
 * critical checks live and therefore the safer default -- it shows up
 * misfiled rather than not at all. */
const SUGGESTION_CATEGORIES = {
  thin_wall: "dfm",
  thick_wall: "dfm",
  narrow_notch: "dfm",
  sharp_corner: "dfm",
  material_distribution: "structural",
  core_material: "structural",
};

function suggestionCategory(kind) {
  return SUGGESTION_CATEGORIES[kind] || "dfm";
}

function renderSuggestion(suggestion, index, category) {
  const li = document.createElement("li");
  li.className = `suggestion suggestion--${category}`;
  li.dataset.index = String(index);
  li.dataset.category = category;

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

  // The canvas highlight is drawn in the finding's own category colour, so
  // a highlighted feature stays identifiable as DFM or Structural once the
  // eye has left the card that triggered it.
  const highlight = { ...suggestion, category };

  const show = () => {
    state.highlight = highlight;
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
    // Pinning is global across both category lists -- only one finding is
    // ever drawn on the canvas -- so clear the pin from either of them.
    suggestionsSectionEl
      .querySelectorAll(".suggestion--pinned")
      .forEach((el) => el.classList.remove("suggestion--pinned"));
    if (alreadyPinned) {
      state.pinnedSuggestion = null;
      state.highlight = null;
    } else {
      state.pinnedSuggestion = index;
      li.classList.add("suggestion--pinned");
      state.highlight = highlight;
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

  setBusy(true, "Importing DXF\u2026");
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
  } finally {
    setBusy(false);
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

  setBusy(true, "Reading drawing\u2026");
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
  } finally {
    setBusy(false);
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
  setBusy(true, "Analyzing beam…");
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
    // The results panel is full-width below both columns, so on a 16:9
    // screen it is reliably off-screen from where this button sits at the
    // bottom of the right-hand column -- without this the click looks like
    // it did nothing.
    beamResultsEl.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showError(`Beam analysis failed: ${err.message}`);
    beamResultsEl.hidden = true;
  } finally {
    btnAnalyzeBeam.disabled = false;
    setBusy(false);
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

/** Two groups, not one strip of tiles.
 *
 * Everything the point loads cause -- moment, bending stress, its safety
 * factor, deflection, the support reactions, and the two diagrams that
 * plot exactly those -- goes in the first. Everything about stability
 * under the axial load -- the Euler column result and the per-wall plate
 * table -- goes in the second. They are separate failure modes answering
 * separate questions, and a single undifferentiated grid let the eye read
 * "Safety Factor" and "Buckling Safety Factor" as two readings of one
 * verdict when they are not: a beam can pass one and fail the other, which
 * is the whole reason both are computed. */
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
  // Under unsymmetric bending the peak fibre isn't the obvious one, so say
  // where it is rather than leaving the number unattributable.
  summaryTile(beamSummaryGrid, "Max Bending Stress", `${fmtNum(r.max_bending_stress)} MPa`, "", {
    sub: r.max_bending_stress_point
      ? `at fibre ${fmtNum(r.max_bending_stress_point[0], { digits: 1 })}, ${fmtNum(
          r.max_bending_stress_point[1],
          { digits: 1 }
        )} mm from centroid`
      : undefined,
  });
  // Three genuinely different states, which all used to render as "n/a":
  // a real factor, an undefined one (no yield strength on file), and an
  // infinite one (this load case produces no bending stress at all).
  summaryTile(
    beamSummaryGrid,
    "Bending Safety Factor",
    SAFETY_FACTOR_TEXT[r.safety_factor_status]
      ? SAFETY_FACTOR_TEXT[r.safety_factor_status].value
      : fmtNum(r.safety_factor, { digits: 2 }),
    "",
    {
      highlight: true,
      className: SAFETY_FACTOR_TEXT[r.safety_factor_status]
        ? "sf-na"
        : safetyFactorClass(r.safety_factor),
      sub: SAFETY_FACTOR_TEXT[r.safety_factor_status]
        ? SAFETY_FACTOR_TEXT[r.safety_factor_status].why
        : undefined,
    }
  );
  summaryTile(beamSummaryGrid, "Max Deflection", `${fmtNum(r.max_deflection, { digits: 4 })} mm`, "", {
    sub: `@ ${fmtNum(r.max_deflection_position)}mm`,
  });
  // Only meaningful when the section's principal axes aren't the sketch
  // axes; on a symmetric profile it is identically zero and saying so
  // would be noise.
  if (r.asymmetry > ASYMMETRY_VISIBLE) {
    summaryTile(
      beamSummaryGrid,
      "Out-of-Plane Deflection",
      `${fmtNum(r.max_deflection_transverse, { digits: 4 })} mm`,
      "",
      {
        sub: `resultant ${fmtNum(r.max_deflection_resultant, { digits: 4 })}mm — this section bends out of the load plane`,
      }
    );
  }
  r.reactions.forEach((reaction) => {
    summaryTile(
      beamSummaryGrid,
      titleCaseLabel(reaction.label),
      `${fmtNum(reaction.force)} N, ${fmtNum(reaction.moment)} N·mm`
    );
  });

  beamBucklingGrid.innerHTML = "";
  summaryTile(beamBucklingGrid, "Euler Buckling Load", `${fmtNum(r.euler_buckling_load)} N`, "", {
    sub: `K=${r.effective_length_factor} (whole column)`,
  });
  if (r.buckling_status === "compression") {
    summaryTile(
      beamBucklingGrid,
      "Euler Safety Factor",
      fmtNum(r.buckling_safety_factor, { digits: 2 }),
      "",
      { highlight: true, className: safetyFactorClass(r.buckling_safety_factor) }
    );
  } else if (r.buckling_status === "tension") {
    // Buckling is a compression failure mode. This used to divide through
    // anyway and show a negative "safety factor".
    summaryTile(beamBucklingGrid, "Euler Safety Factor", "N/A — tension", "", {
      highlight: true,
      className: "sf-na",
      sub: "a member in tension cannot buckle",
    });
  }
  bucklingNoAxialEl.hidden = r.buckling_status !== "no_axial";

  renderLocalBuckling(r.local_buckling);

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

/** Per-wall local (plate) buckling table (eat.local_buckling), shown as
 * its own section rather than folded into the Euler summary tiles above
 * -- the two are different failure modes (member-as-column vs.
 * wall-as-plate) and reporting one number set as if it were the other
 * would misstate which check actually governs.
 *
 * `lb` is the /beam response's `local_buckling` field: null on history
 * entries stored before this check existed, or if the profile's geometry
 * couldn't be segmented into wall elements at all (a solid bar, say --
 * that's not a failure, there's just nothing here for this check to say
 * beyond what the Euler result above already covers).
 *
 * A wall the model can't confidently classify (tapered, non-parallel
 * faces, curved, only one face resolved, or free at both ends) still gets
 * a row -- its measured slenderness (b, t, b/t) is real and shown -- but
 * k / critical stress / class / safety factor are explicitly "Not
 * classified" plus the reason, never blank, since a blank cell here could
 * as easily read as "checked and fine" as "not checked at all". */
function renderLocalBuckling(lb) {
  localBucklingTableEl.innerHTML = "";
  if (!lb || !lb.segments || lb.segments.length === 0) {
    localBucklingSectionEl.hidden = true;
    return;
  }
  localBucklingSectionEl.hidden = false;

  const head = document.createElement("tr");
  // Two safety factors, deliberately side by side and separately named.
  // "Elastic SF" is pure plate theory and has no upper bound, so on a
  // stocky wall it reads absurdly high (324 on a 6mm wall that yields at
  // 8.7). "Effective SF" caps the wall's capacity at the proof stress and
  // is the number that actually limits it.
  [
    ["Wall", ""],
    ["Edges", ""],
    ["b (mm)", ""],
    ["t (mm)", ""],
    ["b/t", ""],
    ["k", ""],
    ["σcr (MPa)", "elastic critical stress — perfect-plate theory, uncapped"],
    ["EC9 Class", "EN 1999-1-1 Table 6.2"],
    ["Applied σ (MPa)", "worst compressive fibre on this wall"],
    ["Elastic SF", "σcr / applied — how close this wall is to buckling"],
    ["Effective SF", "min(σcr, proof stress) / applied — the wall's real limit"],
  ].forEach(([h, title]) => {
    const th = document.createElement("th");
    th.textContent = h;
    if (title) th.title = title;
    head.appendChild(th);
  });
  localBucklingTableEl.appendChild(head);

  lb.segments.forEach((s) => {
    const uncertain = s.support === "uncertain";
    const tr = document.createElement("tr");
    tr.dataset.wall = String(s.index); // lets a test (or anything else) find "wall 4" directly,
    // independent of a caveat row shifting its position in the table
    tr.className = [
      uncertain ? "local-buckling-table__row--uncertain" : "",
      s.index === lb.governing_index ? "local-buckling-table__row--governing" : "",
    ]
      .filter(Boolean)
      .join(" ");

    const cells = [
      [String(s.index), ""],
      [uncertain ? "Not classified" : titleCaseLabel(s.supports_label), "lb-support"],
      [fmtNum(s.width, { digits: 2 }), ""],
      [fmtNum(s.thickness, { digits: 2 }), ""],
      [fmtNum(s.slenderness, { digits: 2 }), ""],
      [s.k === null ? "—" : fmtNum(s.k, { digits: 3 }), ""],
      [s.elastic_critical_stress === null ? "—" : fmtNum(s.elastic_critical_stress, { digits: 1 }), ""],
      [s.section_class === null ? "—" : String(s.section_class), ""],
      [s.applied_stress === null ? "—" : fmtNum(s.applied_stress, { digits: 1 }), ""],
      // The elastic factor is deliberately NOT colour-coded by severity:
      // it is a true statement about buckling, but colouring it green
      // would be exactly the reassurance the yield cap exists to withhold.
      [s.safety_factor === null ? "—" : fmtNum(s.safety_factor, { digits: 2 }), "lb-elastic-sf"],
      [
        s.effective_safety_factor === null ? "—" : fmtNum(s.effective_safety_factor, { digits: 2 }),
        safetyFactorClass(s.effective_safety_factor),
      ],
    ];
    cells.forEach(([text, className]) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (className) td.className = className;
      tr.appendChild(td);
    });
    localBucklingTableEl.appendChild(tr);

    if (s.caveats && s.caveats.length > 0) {
      const note = document.createElement("tr");
      note.dataset.wall = String(s.index);
      note.className = "local-buckling-table__caveat";
      const td = document.createElement("td");
      td.colSpan = cells.length;
      td.textContent = s.caveats.join(" · ");
      note.appendChild(td);
      localBucklingTableEl.appendChild(note);
    }
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
    if (currentBaseline) setBaselineName(currentBaseline.name);
    btnModalUseBuiltin.disabled = !currentBaseline || currentBaseline.source === "builtin";
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
      await applyBaseline({ type: "history", entry_id: summary.id });
      await openHistory(); // re-render the list so the "baseline" tag moves
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

/* Specific-stiffness / specific-strength unit reductions.
 *
 * Each metric is (something in the engine's mm-N-MPa system) divided by a
 * mass per length in kg/m, which lands on a unit with a fraction inside a
 * fraction -- N/(kg/m), N·mm²/(kg/m). Those are the same quantity as the
 * plain SI forms below, so the displayed value is scaled once here and
 * labelled with the simple unit rather than the compound one:
 *
 *   EA/μ        N/(kg/m)      = N·m/kg   -> MN·m/kg  (E/ρ, "specific stiffness")
 *   EI/μ        N·mm²/(kg/m)  = N·m³/kg  (mm² = 1e-6 m²)
 *   σy·A/μ      N/(kg/m)      = N·m/kg   -> kN·m/kg  (σy/ρ, "specific strength")
 *   σy·Z/μ      N·mm/(kg/m)   = N·m²/kg  (mm = 1e-3 m)
 *
 * The prefixes (M, k) are chosen to keep aluminium extrusions in a
 * readable range rather than in exponent notation. Scaling is applied to
 * profile and baseline alike, so the bar lengths are unchanged. */
const SPECIFIC_STIFFNESS_AXIAL_SCALE = 1e-6; // N/(kg/m) -> MN·m/kg
const SPECIFIC_STIFFNESS_BENDING_SCALE = 1e-6; // N·mm²/(kg/m) -> N·m³/kg
const SPECIFIC_STRENGTH_AXIAL_SCALE = 1e-3; // N/(kg/m) -> kN·m/kg
const SPECIFIC_STRENGTH_BENDING_SCALE = 1e-3; // N·mm/(kg/m) -> N·m²/kg

/** Builds the six stiffness/strength-to-weight metrics (plus mass per
 * length) comparing `sectionResult`/`material` against baseline response
 * `b`. Strength metrics need a yield strength on *both* sides; skipped
 * (not shown with a placeholder) when either is missing, since there's
 * nothing meaningful to compare otherwise. Grouped to match the section
 * headings in the UI.
 *
 * Two of the six carry a `note`, and exactly two: the axial rows cancel
 * their geometry algebraically (EA/μ = E·A/(ρ·A) = E/ρ, and σy·A/μ =
 * σy/ρ), so they are pure material properties and read identically for
 * every profile in the same alloy. That is the correct answer, not a
 * broken comparison, but it looks like one unexplained -- so it is
 * explained on the row itself. The bending rows keep an I or a Z that does
 * *not* cancel, so they are genuinely geometry-dependent and get no note;
 * mass per length (ρ·A) keeps its area too. eat/verify_baseline.py checks
 * the same reduction algebraically on the backend side. */
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
          unit: "MN·m/kg",
          note:
            "Material property only: EA ÷ mass per length cancels the area, leaving E ÷ density. " +
            "Every profile in this material reads the same here by definition — geometry shows up " +
            "in the bending rows below, not this one.",
          profile: (sectionResult.ea / sectionResult.mass_per_length) * SPECIFIC_STIFFNESS_AXIAL_SCALE,
          baseline: (b.ea / b.mass_per_length) * SPECIFIC_STIFFNESS_AXIAL_SCALE,
        },
        {
          label: "Bending (X)",
          unit: "N·m³/kg",
          profile: (sectionResult.ei_yy / sectionResult.mass_per_length) * SPECIFIC_STIFFNESS_BENDING_SCALE,
          baseline: (b.ei_yy / b.mass_per_length) * SPECIFIC_STIFFNESS_BENDING_SCALE,
        },
        {
          label: "Bending (Y)",
          unit: "N·m³/kg",
          profile: (sectionResult.ei_xx / sectionResult.mass_per_length) * SPECIFIC_STIFFNESS_BENDING_SCALE,
          baseline: (b.ei_xx / b.mass_per_length) * SPECIFIC_STIFFNESS_BENDING_SCALE,
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
          unit: "kN·m/kg",
          note:
            "Material property only: yield × area ÷ mass per length cancels the area, leaving " +
            "yield ÷ density. Every profile in this material reads the same here by definition — " +
            "geometry shows up in the bending rows below, not this one.",
          profile:
            ((yieldStrength * sectionResult.area) / sectionResult.mass_per_length) *
            SPECIFIC_STRENGTH_AXIAL_SCALE,
          baseline: ((b.yield_strength * b.area) / b.mass_per_length) * SPECIFIC_STRENGTH_AXIAL_SCALE,
        },
        {
          label: "Bending (X)",
          unit: "N·m²/kg",
          profile:
            ((yieldStrength * zWorstYyProfile) / sectionResult.mass_per_length) *
            SPECIFIC_STRENGTH_BENDING_SCALE,
          baseline:
            ((b.yield_strength * zWorstYyBaseline) / b.mass_per_length) * SPECIFIC_STRENGTH_BENDING_SCALE,
        },
        {
          label: "Bending (Y)",
          unit: "N·m²/kg",
          profile:
            ((yieldStrength * zWorstXxProfile) / sectionResult.mass_per_length) *
            SPECIFIC_STRENGTH_BENDING_SCALE,
          baseline:
            ((b.yield_strength * zWorstXxBaseline) / b.mass_per_length) * SPECIFIC_STRENGTH_BENDING_SCALE,
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

  if (metric.note) {
    const note = document.createElement("p");
    note.className = "baseline-metric__note";
    note.textContent = metric.note;
    wrap.appendChild(note);
  }
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

/** The baseline gets switched often, so it is reachable from three places
 * rather than one: the topbar (always visible, and doubles as the readout
 * of what's currently selected), the comparison panel, and the History
 * modal that actually lists the candidates. All three show the same name
 * and offer the same operations -- "Change…" and the topbar button both
 * open the modal, and both the panel and the modal can reset to built-in
 * -- so no entry point is a subset of another. This keeps them in step. */
function setBaselineName(name) {
  baselineNameEl.textContent = name;
  topbarBaselineNameEl.textContent = name;
  historyBaselineNameEl.textContent = name;
}

async function refreshBaselineName() {
  try {
    const b = await apiFetch("/baseline");
    setBaselineName(b.name);
    return b;
  } catch (err) {
    setBaselineName("unavailable");
    return null;
  }
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
    refreshBaselineName(); // the topbar readout is independent of any loaded profile
    return;
  }
  try {
    const b = await apiFetch("/baseline");
    setBaselineName(b.name);
    const material = state.materials.find((m) => m.name === state.selectedMaterial) || null;
    renderBaselineMetrics(buildBaselineMetricGroups(sectionResult, material, b));
    baselineSectionEl.hidden = false;
  } catch (err) {
    baselineSectionEl.hidden = true;
  }
}

/** Single path for every baseline change, whichever control triggered it,
 * so all three readouts and the comparison land in the same state. */
async function applyBaseline(setting) {
  await apiFetch("/baseline", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(setting),
  });
  await refreshBaselineName();
  refreshBaselineComparison(state.lastSectionResult);
}

async function useBuiltinBaseline() {
  try {
    await applyBaseline({ type: "builtin" });
  } catch (err) {
    showError(`Could not reset baseline: ${err.message}`);
  }
}

btnUseBuiltinBaseline.addEventListener("click", useBuiltinBaseline);

btnModalUseBuiltin.addEventListener("click", async () => {
  await useBuiltinBaseline();
  openHistory(); // re-render the list so the "baseline" tag clears
});

btnChangeBaseline.addEventListener("click", openHistory);
btnTopbarBaseline.addEventListener("click", openHistory);

/* ------------------------------------------------------------------
   Init
------------------------------------------------------------------ */

/** Reports any local-state file the server had to reset on startup.
 *
 * A store that can't be decoded is quarantined and reset rather than
 * 500-ing every endpoint (see eat/storage.py), but that has to be VISIBLE
 * -- a reset material list in particular leaves the app unable to analyze
 * anything, and silently showing an empty dropdown would be baffling.
 * Uses the error banner rather than a new surface: it is the one place
 * the user already looks when something is wrong. */
async function reportStoreWarnings() {
  try {
    const body = await apiFetch("/warnings");
    if (body && body.warnings && body.warnings.length > 0) {
      showError(body.warnings.join("  •  "));
    }
  } catch (_) {
    /* the warnings channel itself failing must not break startup */
  }
}

renderPointLoads();
updateToolbarState();
loadMaterials();
refreshBaselineName(); // topbar readout is meaningful before any profile is loaded
reportStoreWarnings();
layoutCanvas();

let resizeTimeout;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimeout);
  resizeTimeout = setTimeout(layoutCanvas, 100);
});

if (window.ResizeObserver) {
  new ResizeObserver(() => layoutCanvas()).observe(canvasWell);
}
