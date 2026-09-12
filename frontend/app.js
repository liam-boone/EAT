"use strict";

/* ------------------------------------------------------------------
   State
------------------------------------------------------------------ */

const state = {
  points: [],           // [{x, y}] mm, engineering coords (y up)
  isClosed: false,
  materials: [],
  selectedMaterial: null,
  sectionId: null,
  pointLoads: [{ position_fraction: 0.5, magnitude: -1000 }],
  viewWidthMm: 300,      // how many mm of width the sketch grid shows
  pixelsPerMm: 1,        // recomputed on layout/resize
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
const sketchHint = document.getElementById("sketch-hint");

const materialSelect = document.getElementById("material-select");

const sectionResultsEl = document.getElementById("section-results");
const sectionResultGrid = document.getElementById("section-result-grid");

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
    x: MARGIN_PX + wx * state.pixelsPerMm,
    y: state.cssHeight - MARGIN_PX - wy * state.pixelsPerMm,
  };
}

function screenToWorld(sx, sy) {
  return {
    x: (sx - MARGIN_PX) / state.pixelsPerMm,
    y: (state.cssHeight - MARGIN_PX - sy) / state.pixelsPerMm,
  };
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
  drawPolygon();
}

function drawGrid(w, h) {
  const minorMm = 10;
  const majorMm = 50;

  ctx.lineWidth = 1;

  for (let mm = 0; ; mm += minorMm) {
    const { x } = worldToScreen(mm, 0);
    if (x > w - MARGIN_PX + 0.5) break;
    ctx.strokeStyle = mm % majorMm === 0 ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.06)";
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, MARGIN_PX);
    ctx.lineTo(Math.round(x) + 0.5, h - MARGIN_PX);
    ctx.stroke();
  }

  for (let mm = 0; ; mm += minorMm) {
    const { y } = worldToScreen(0, mm);
    if (y < MARGIN_PX - 0.5) break;
    ctx.strokeStyle = mm % majorMm === 0 ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.06)";
    ctx.beginPath();
    ctx.moveTo(MARGIN_PX, Math.round(y) + 0.5);
    ctx.lineTo(w - MARGIN_PX, Math.round(y) + 0.5);
    ctx.stroke();
  }

  // Origin axes, slightly stronger.
  const origin = worldToScreen(0, 0);
  ctx.strokeStyle = "rgba(255,255,255,0.22)";
  ctx.beginPath();
  ctx.moveTo(MARGIN_PX, Math.round(origin.y) + 0.5);
  ctx.lineTo(w - MARGIN_PX, Math.round(origin.y) + 0.5);
  ctx.moveTo(Math.round(origin.x) + 0.5, MARGIN_PX);
  ctx.lineTo(Math.round(origin.x) + 0.5, h - MARGIN_PX);
  ctx.stroke();
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
    sketchHint.textContent = "Click near the first point (or press Close loop) to finish the profile.";
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
    invalidateSection();
  }
  state.points.pop();
  render();
  updateToolbarState();
}

function closeLoop() {
  if (state.points.length < 3 || state.isClosed) return;
  state.isClosed = true;
  render();
  updateToolbarState();
  computeSection();
}

function clearSketch() {
  state.points = [];
  state.isClosed = false;
  invalidateSection();
  render();
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
  sectionResultsEl.hidden = true;
  beamInputsEl.hidden = true;
  beamResultsEl.hidden = true;
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
  }
}

function renderSectionResults(r) {
  sectionResultGrid.innerHTML = "";
  resultRow(sectionResultGrid, "Area", fmtNum(r.area), "mm²");
  resultRow(sectionResultGrid, "Centroid", `${fmtNum(r.cx)}, ${fmtNum(r.cy)}`, "mm");
  resultRow(sectionResultGrid, "Ixx", fmtNum(r.ixx), "mm⁴");
  resultRow(sectionResultGrid, "Iyy", fmtNum(r.iyy), "mm⁴");
  resultRow(sectionResultGrid, "Ixy", fmtNum(r.ixy), "mm⁴");
  resultRow(sectionResultGrid, "J (torsion)", fmtNum(r.j), "mm⁴");
  resultRow(sectionResultGrid, "Warping Iw", fmtNum(r.iw), "mm⁶");
  resultRow(sectionResultGrid, "Shear centre", `${fmtNum(r.x_sc)}, ${fmtNum(r.y_sc)}`, "mm");
  resultRow(sectionResultGrid, "Zxx (+/-)", `${fmtNum(r.zxx_plus)} / ${fmtNum(r.zxx_minus)}`, "mm³");
  resultRow(sectionResultGrid, "Zyy (+/-)", `${fmtNum(r.zyy_plus)} / ${fmtNum(r.zyy_minus)}`, "mm³");
  resultRow(sectionResultGrid, "Sxx (plastic)", fmtNum(r.sxx), "mm³");
  resultRow(sectionResultGrid, "Syy (plastic)", fmtNum(r.syy), "mm³");
  resultRow(sectionResultGrid, "EA", fmtNum(r.ea), "N");
  resultRow(sectionResultGrid, "EIxx", fmtNum(r.ei_xx), "N·mm²");
  resultRow(sectionResultGrid, "EIyy", fmtNum(r.ei_yy), "N·mm²");
  resultRow(sectionResultGrid, "GJ", fmtNum(r.gj), "N·mm²");
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
    state.isClosed = true;
    state.sectionId = body.section_id;
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
    magInput.step = "any";
    magInput.placeholder = "magnitude (N)";
    magInput.value = load.magnitude;
    magInput.addEventListener("input", () => {
      state.pointLoads[idx].magnitude = parseFloat(magInput.value);
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
    row.appendChild(removeBtn);
    pointLoadsListEl.appendChild(row);
  });
}

btnAddLoad.addEventListener("click", () => {
  state.pointLoads.push({ position_fraction: 0.5, magnitude: -1000 });
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
  if (!state.sectionId) {
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
  summaryTile(beamSummaryGrid, "Max moment", `${fmtNum(r.max_moment)} N·mm`, "", {
    sub: `@ ${fmtNum(r.max_moment_position)}mm`,
  });
  summaryTile(beamSummaryGrid, "Max bending stress", `${fmtNum(r.max_bending_stress)} MPa`);
  summaryTile(
    beamSummaryGrid,
    "Safety factor",
    r.safety_factor === null ? "n/a" : fmtNum(r.safety_factor, { digits: 2 }),
    "",
    { highlight: true, className: safetyFactorClass(r.safety_factor) }
  );
  summaryTile(beamSummaryGrid, "Max deflection", `${fmtNum(r.max_deflection, { digits: 4 })} mm`, "", {
    sub: `@ ${fmtNum(r.max_deflection_position)}mm`,
  });
  summaryTile(beamSummaryGrid, "Euler buckling load", `${fmtNum(r.euler_buckling_load)} N  (K=${r.effective_length_factor})`);
  if (r.buckling_safety_factor !== null) {
    summaryTile(
      beamSummaryGrid,
      "Buckling safety factor",
      fmtNum(r.buckling_safety_factor, { digits: 2 }),
      "",
      { className: safetyFactorClass(r.buckling_safety_factor) }
    );
  }
  r.reactions.forEach((reaction) => {
    summaryTile(
      beamSummaryGrid,
      reaction.label,
      `${fmtNum(reaction.force)} N, ${fmtNum(reaction.moment)} N·mm`
    );
  });

  drawLineChart(chartStressEl, r.diagram_x, r.bending_stress_diagram, {
    xLabel: "Position along length",
    xUnit: "mm",
    yLabel: "Bending stress",
    yUnit: "MPa",
    markerX: r.max_bending_stress_position,
    markerY: r.max_bending_stress,
    forceZeroBaseline: true,
  });

  drawLineChart(chartDeflectionEl, r.diagram_x, r.deflection_diagram, {
    xLabel: "Position along length",
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
