/* cellengine frontend: no build step, no framework.
 *
 * Flow:  pick dataset -> move slider (release) -> POST /api/runs/ -> poll GET /api/runs/{id}/
 *        status "markers"  => labels are committed: fetch /cells/ and draw
 *        status "done"     => fetch /markers/ and fill the legend + table
 *
 * Scatter is a <canvas>, not SVG: 3k points is fine either way, 50k is not.
 * Hover uses a uniform grid over screen space so nearest-point lookup is O(1).
 */
(() => {
  "use strict";

  // ---------- palette -------------------------------------------------------
  // First 8 slots are the validated categorical order (see dataviz palette).
  // Clusters beyond 8 use a documented extension; identity never relies on color
  // alone: every cluster gets a centroid label on the plot and a legend row.
  const PALETTE = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948",
    "#8c564b", "#17becf", "#9a9a1f", "#c2185b", "#1b6f6f", "#b5651d", "#5c6bc0", "#6aa84f",
    "#a05195", "#4fc3f7", "#5e2750", "#c9a15a",
  ];
  const colorOf = (c) => PALETTE[c % PALETTE.length];

  // ---------- tiny API client -------------------------------------------------
  const cookie = (name) => document.cookie.split("; ").find((r) => r.startsWith(name + "="))?.split("=")[1];
  async function api(method, url, body) {
    const opts = { method, headers: { "X-CSRFToken": cookie("csrftoken") || "" }, credentials: "same-origin" };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!r.ok) throw Object.assign(new Error(data?.detail || JSON.stringify(data) || r.statusText), { status: r.status, data });
    return data;
  }
  const $ = (id) => document.getElementById(id);
  const fmt = (x, d = 2) => (x == null ? "" : Number(x).toFixed(d));
  const sci = (p) => (p == null ? "" : p < 1e-3 ? p.toExponential(1) : p.toFixed(3));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- state -----------------------------------------------------------
  const S = {
    user: null,
    datasets: [],
    dataset: null,          // selected dataset object
    runs: [],               // runs for the selected dataset
    run: null,              // the run currently displayed / polled
    cells: null,            // {n, x, y, cluster, barcodes}
    markers: {},            // cluster_id -> [rows]
    selected: null,         // selected cluster id
    pollToken: 0,
  };

  // ---------- auth ------------------------------------------------------------
  let registering = false;
  $("auth-toggle").onclick = () => {
    registering = !registering;
    $("auth-title").textContent = registering ? "Create an account" : "Log in";
    $("auth-submit").textContent = registering ? "Create account" : "Log in";
    $("auth-toggle").textContent = registering ? "I have an account" : "Create an account";
  };
  $("auth-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    $("auth-error").hidden = true;
    try {
      const u = await api("POST", registering ? "/api/auth/register/" : "/api/auth/login/",
        { username: fd.get("username"), password: fd.get("password") });
      setUser(u.username);
    } catch (err) { $("auth-error").textContent = err.message; $("auth-error").hidden = false; }
  };
  $("logout").onclick = async () => { await api("POST", "/api/auth/logout/"); setUser(null); };

  function setUser(name) {
    S.user = name;
    $("auth").hidden = !!name;
    $("app").hidden = !name;
    $("userbox").hidden = !name;
    $("username").textContent = name || "";
    if (name) loadDatasets();
  }

  // ---------- datasets --------------------------------------------------------
  async function loadDatasets() {
    S.datasets = await api("GET", "/api/datasets/");
    renderDatasets();
    if (!S.dataset && S.datasets.length) selectDataset(S.datasets.find((d) => d.status === "ready") || S.datasets[0]);
    if (S.datasets.some((d) => d.status === "uploaded")) { await sleep(1500); loadDatasets(); }
  }
  function renderDatasets() {
    const ul = $("dataset-list");
    ul.innerHTML = "";
    for (const d of S.datasets) {
      const li = document.createElement("li");
      li.className = d.id === S.dataset?.id ? "active" : "";
      const shape = d.n_cells ? `${d.n_cells.toLocaleString()} × ${d.n_genes.toLocaleString()}` : "";
      li.innerHTML = `<span>${esc(d.name)}</span><span class="meta">${shape} <span class="badge ${d.status}">${d.status}</span></span>`;
      li.title = d.error || d.original_filename;
      li.onclick = () => selectDataset(d);
      ul.appendChild(li);
    }
  }
  async function selectDataset(d) {
    S.dataset = d; S.run = null; S.cells = null; S.markers = {}; S.selected = null;
    renderDatasets();
    $("controls").hidden = d.status !== "ready";
    $("plot-title").textContent = `UMAP · ${d.name}`;
    clearPlot();
    await loadRuns();
    const last = S.runs.find((r) => r.status === "done" || r.status === "markers");
    if (last) showRun(last);
  }

  $("upload-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = $("upload-file").files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("name", $("upload-name").value);
    $("upload-status").textContent = `Uploading ${(f.size / 1e6).toFixed(1)} MB…`;
    try {
      await api("POST", "/api/datasets/", fd);
      $("upload-status").textContent = "Uploaded. Worker is validating…";
      e.target.reset();
      await loadDatasets();
    } catch (err) { $("upload-status").textContent = err.data?.file?.[0] || err.message; }
  };

  // ---------- runs ------------------------------------------------------------
  function currentParams() {
    return {
      resolution: parseFloat($("resolution").value),
      n_pcs: parseInt($("n_pcs").value, 10),
      n_neighbors: parseInt($("n_neighbors").value, 10),
      n_top_genes: parseInt($("n_top_genes").value, 10),
    };
  }
  $("resolution").oninput = () => { $("res-value").textContent = fmt($("resolution").value, 1); };
  $("resolution").onchange = () => submitRun();          // fires on release, not while dragging
  $("run-btn").onclick = () => submitRun();

  async function submitRun() {
    if (!S.dataset || S.dataset.status !== "ready") return;
    const run = await api("POST", "/api/runs/", { dataset: S.dataset.id, params: currentParams() });
    S.runs.unshift(run);
    renderHistory();
    showRun(run);
  }

  async function loadRuns() {
    S.runs = S.dataset ? await api("GET", `/api/runs/?dataset=${S.dataset.id}`) : [];
    $("history-card").hidden = S.runs.length === 0;
    renderHistory();
  }

  // Display a run: draw it if it already has labels, otherwise poll until it does.
  async function showRun(run) {
    S.run = run; S.cells = null; S.markers = {}; S.selected = null;
    $("status-card").hidden = false;
    $("markers-card").hidden = true;
    $("legend").innerHTML = "";
    clearPlot();
    syncSliderTo(run.params);
    renderStatus(run);
    renderHistory();
    const token = ++S.pollToken;
    while (token === S.pollToken) {
      if (["markers", "done"].includes(run.status) && !S.cells) {
        S.cells = await api("GET", `/api/runs/${run.id}/cells/`);
        if (token !== S.pollToken) return;
        draw();
        renderLegend();
      }
      if (run.status === "done") {
        const m = await api("GET", `/api/runs/${run.id}/markers/`);
        if (token !== S.pollToken) return;
        S.markers = {};
        for (const row of m.markers) (S.markers[row.cluster_id] ||= []).push(row);
        renderLegend();
        if (S.selected != null) renderMarkers();
        return;
      }
      if (run.status === "failed") return;
      await sleep(600);
      if (token !== S.pollToken) return;
      run = await api("GET", `/api/runs/${run.id}/`);
      S.run = run;
      const i = S.runs.findIndex((r) => r.id === run.id);
      if (i >= 0) S.runs[i] = run;
      renderStatus(run);
      renderHistory();
    }
  }

  function syncSliderTo(p) {
    if (p.resolution != null) { $("resolution").value = p.resolution; $("res-value").textContent = fmt(p.resolution, 1); }
    if (p.n_pcs != null) $("n_pcs").value = p.n_pcs;
    if (p.n_neighbors != null) $("n_neighbors").value = p.n_neighbors;
    if (p.n_top_genes != null) $("n_top_genes").value = p.n_top_genes;
  }

  const ORDER = ["queued", "preprocessing", "clustering", "markers", "done"];
  function renderStatus(run) {
    const idx = ORDER.indexOf(run.status);
    for (const li of $("stages").children) {
      const i = ORDER.indexOf(li.dataset.stage);
      li.className = run.status === "failed" ? (i <= 1 ? "failed" : "") : i < idx ? "done" : i === idx ? "active" : "";
      if (run.status === "done") li.className = "done";
    }
    const t = run.timings || {};
    const bits = [];
    if (run.cache_hit != null) bits.push(run.cache_hit ? "graph from cache" : "graph computed");
    if (t.preprocess != null) bits.push(`preprocess ${fmt(t.preprocess, 1)}s`);
    if (t.leiden != null) bits.push(`Leiden ${fmt(t.leiden, 2)}s`);
    if (t.markers != null) bits.push(`markers ${fmt(t.markers, 2)}s`);
    if (run.n_clusters != null) bits.push(`${run.n_clusters} clusters`);
    $("run-summary").textContent = bits.join(" · ");
    $("run-error").hidden = run.status !== "failed";
    $("run-error").textContent = run.error || "";
    $("plot-sub").textContent = run.n_clusters != null ? `resolution ${fmt(run.params.resolution ?? 1, 1)} · ${run.n_clusters} clusters` : `resolution ${fmt(run.params.resolution ?? 1, 1)}`;
  }

  function renderHistory() {
    const tb = $("history-table").querySelector("tbody");
    tb.innerHTML = "";
    $("history-card").hidden = S.runs.length === 0;
    for (const r of S.runs) {
      const p = r.params || {}, t = r.timings || {};
      const tr = document.createElement("tr");
      tr.className = "clickable" + (r.id === S.run?.id ? " current" : "");
      const cache = r.cache_hit == null ? "" : r.cache_hit ? `<span class="pill hit">hit</span>` : `<span class="pill miss">miss</span>`;
      const status = r.status === "failed" ? `<span class="pill failed">failed</span>` : `<span class="pill">${r.status}</span>`;
      tr.innerHTML = `<td>${new Date(r.created_at).toLocaleTimeString()}</td>
        <td class="num">${fmt(p.resolution ?? 1, 1)}</td><td class="num">${p.n_pcs ?? 50}</td>
        <td class="num">${p.n_neighbors ?? 15}</td><td class="num">${p.n_top_genes ?? 2000}</td>
        <td class="num">${r.n_clusters ?? ""}</td><td>${status}</td><td>${cache}</td>
        <td class="num">${t.preprocess != null ? fmt(t.preprocess, 1) + "s" : ""}</td>
        <td class="num">${t.leiden != null ? fmt(t.leiden, 2) + "s" : ""}</td>
        <td class="num">${t.markers != null ? fmt(t.markers, 2) + "s" : ""}</td>`;
      tr.onclick = () => showRun(r);
      tb.appendChild(tr);
    }
  }

  // ---------- legend + markers ------------------------------------------------
  function renderLegend() {
    const ul = $("legend");
    ul.innerHTML = "";
    if (!S.cells) return;
    const counts = [];
    for (const c of S.cells.cluster) counts[c] = (counts[c] || 0) + 1;
    counts.forEach((n, c) => {
      const li = document.createElement("li");
      li.className = S.selected == null ? "" : S.selected === c ? "active" : "dim";
      const genes = (S.markers[c] || []).slice(0, 3).map((m) => m.gene).join(" ");
      li.innerHTML = `<span class="swatch" style="background:${colorOf(c)}"></span><span>${c}</span>
        <span class="muted">${n.toLocaleString()}</span>${genes ? `<span class="genes">${esc(genes)}</span>` : ""}`;
      li.onclick = () => selectCluster(S.selected === c ? null : c);
      ul.appendChild(li);
    });
  }
  function selectCluster(c) {
    S.selected = c;
    renderLegend();
    draw();
    renderMarkers();
  }
  function renderMarkers() {
    const card = $("markers-card");
    if (S.selected == null) { card.hidden = true; return; }
    card.hidden = false;
    const rows = S.markers[S.selected];
    $("markers-title").innerHTML = `Marker genes · cluster <span class="swatch" style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${colorOf(S.selected)}"></span> ${S.selected}`;
    const tb = $("markers-table").querySelector("tbody");
    tb.innerHTML = "";
    if (!rows) { tb.innerHTML = `<tr><td colspan="7" class="muted">${S.run?.status === "done" ? "No significant markers." : "Computing markers…"}</td></tr>`; return; }
    for (const m of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="num">${m.rank}</td><td><strong>${esc(m.gene)}</strong></td>
        <td class="num">${fmt(m.score, 1)}</td><td class="num">${fmt(m.log2fc, 2)}</td>
        <td class="num">${fmt(m.pct_in * 100, 0)}%</td><td class="num">${fmt(m.pct_out * 100, 0)}%</td><td class="num">${sci(m.padj)}</td>`;
      tb.appendChild(tr);
    }
  }

  // ---------- canvas scatter --------------------------------------------------
  const canvas = $("scatter");
  const ctx = canvas.getContext("2d");
  const view = { sx: 1, sy: 1, ox: 0, oy: 0, w: 0, h: 0, grid: null, cell: 12, cols: 0 };

  function clearPlot() {
    $("plot-empty").hidden = !!S.cells;
    $("plot-empty").textContent = S.run ? "Waiting for cluster labels…" : "Select a dataset and move the resolution slider.";
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function fitCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    view.w = rect.width; view.h = rect.height;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function draw() {
    if (!S.cells) { clearPlot(); return; }
    $("plot-empty").hidden = true;
    fitCanvas();
    const { x, y, cluster, n } = S.cells;
    let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
    for (let i = 0; i < n; i++) {
      if (x[i] < minx) minx = x[i]; if (x[i] > maxx) maxx = x[i];
      if (y[i] < miny) miny = y[i]; if (y[i] > maxy) maxy = y[i];
    }
    const pad = 24;
    view.sx = (view.w - 2 * pad) / (maxx - minx || 1);
    view.sy = (view.h - 2 * pad) / (maxy - miny || 1);
    const s = Math.min(view.sx, view.sy);               // equal aspect: UMAP distances are the point
    view.sx = view.sy = s;
    view.ox = pad + (view.w - 2 * pad - s * (maxx - minx)) / 2 - s * minx;
    view.oy = pad + (view.h - 2 * pad - s * (maxy - miny)) / 2 + s * maxy;   // flip y

    const r = n > 20000 ? 1.2 : n > 8000 ? 1.8 : 2.4;
    ctx.clearRect(0, 0, view.w, view.h);
    view.cols = Math.ceil(view.w / view.cell) + 1;
    view.grid = new Map();

    // Draw dimmed clusters first so the selected one sits on top.
    const passes = S.selected == null ? [null] : ["dim", "sel"];
    for (const pass of passes) {
      for (let i = 0; i < n; i++) {
        const c = cluster[i];
        if (pass === "dim" && c === S.selected) continue;
        if (pass === "sel" && c !== S.selected) continue;
        const px = view.ox + s * x[i], py = view.oy - s * y[i];
        ctx.fillStyle = colorOf(c);
        ctx.globalAlpha = pass === "dim" ? 0.12 : 0.85;
        ctx.beginPath(); ctx.arc(px, py, r, 0, 6.2832); ctx.fill();
        if (pass !== "dim") {
          const key = ((py / view.cell) | 0) * view.cols + ((px / view.cell) | 0);
          const b = view.grid.get(key); if (b) b.push(i); else view.grid.set(key, [i]);
        }
      }
    }
    ctx.globalAlpha = 1;
    drawCentroidLabels(s);
  }

  // Direct labels: the cluster id at each cluster's median position, with a halo.
  function drawCentroidLabels(s) {
    const { x, y, cluster, n } = S.cells;
    const xs = [], ys = [];
    for (let i = 0; i < n; i++) { (xs[cluster[i]] ||= []).push(x[i]); (ys[cluster[i]] ||= []).push(y[i]); }
    ctx.font = "600 12px " + getComputedStyle(document.body).fontFamily;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    xs.forEach((arr, c) => {
      if (S.selected != null && S.selected !== c) return;
      const mx = median(arr), my = median(ys[c]);
      const px = view.ox + s * mx, py = view.oy - s * my;
      ctx.lineWidth = 3; ctx.strokeStyle = "rgba(252,252,251,0.9)"; ctx.strokeText(String(c), px, py);
      ctx.fillStyle = "#0b0b0b"; ctx.fillText(String(c), px, py);
    });
  }
  function median(a) { const b = Float64Array.from(a).sort(); const m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; }

  // Hover: look up the 3x3 grid neighborhood, pick the nearest point within 6px.
  function nearest(mx, my) {
    if (!view.grid) return -1;
    const gx = (mx / view.cell) | 0, gy = (my / view.cell) | 0;
    let best = -1, bd = 36;
    const { x, y } = S.cells;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const b = view.grid.get((gy + dy) * view.cols + gx + dx);
      if (!b) continue;
      for (const i of b) {
        const px = view.ox + view.sx * x[i] - mx, py = view.oy - view.sy * y[i] - my;
        const d = px * px + py * py;
        if (d < bd) { bd = d; best = i; }
      }
    }
    return best;
  }
  canvas.onmousemove = (e) => {
    if (!S.cells) return;
    const rect = canvas.getBoundingClientRect();
    const i = nearest(e.clientX - rect.left, e.clientY - rect.top);
    const tip = $("tooltip");
    if (i < 0) { tip.hidden = true; return; }
    const c = S.cells.cluster[i];
    const genes = (S.markers[c] || []).slice(0, 3).map((m) => m.gene).join(", ");
    tip.innerHTML = `<b>cluster ${c}</b>${genes ? " · " + esc(genes) : ""}<br><span style="opacity:.75">${esc(S.cells.barcodes[i])}</span>`;
    tip.style.left = (e.clientX - rect.left) + "px";
    tip.style.top = (e.clientY - rect.top) + "px";
    tip.hidden = false;
  };
  canvas.onmouseleave = () => { $("tooltip").hidden = true; };
  canvas.onclick = (e) => {
    if (!S.cells) return;
    const rect = canvas.getBoundingClientRect();
    const i = nearest(e.clientX - rect.left, e.clientY - rect.top);
    selectCluster(i < 0 ? null : S.cells.cluster[i]);
  };
  window.addEventListener("resize", () => { if (S.cells) draw(); });

  function esc(s) { return String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch])); }

  // ---------- boot ------------------------------------------------------------
  api("GET", "/api/auth/me/").then((u) => setUser(u.username)).catch(() => setUser(null));
})();
