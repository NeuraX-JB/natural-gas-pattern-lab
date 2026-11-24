"use strict";

const el = (id) => document.getElementById(id);
const state = {
  meta: null,
  data: null,
  research: null,
  researchPending: false,
  date: "",
  fields: [],
  overrides: {},
  sequence: "",
  mode: "exact",
  rowSet: "all",
  outcome: "all",
  page: 0,
  request: 0,
};
const PAGE_SIZE = 10;
const finite = (x) => typeof x === "number" && Number.isFinite(x);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ],
  );
const numeric = (x, decimals = 2) => (finite(x) ? x.toFixed(decimals) : "—");
const sign = (x, decimals = 2) =>
  finite(x)
    ? (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(decimals)
    : "—";
const rate = (x) => (finite(x) ? (100 * x).toFixed(1) + "%" : "—");
const pp = (x) => (finite(x) ? sign(100 * x, 1) + " pp" : "—");
const count = (x) => (finite(x) ? x.toLocaleString("en-US") : "—");
const tone = (x) =>
  finite(x) && x !== 0 ? (x > 0 ? "positive" : "negative") : "";
const iso = (x) => (typeof x === "string" ? x : "");
const niceDate = (s) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s || "")) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(s + "T12:00:00Z"));
};
const labels = () => state.meta?.fields || {};
const shortLabel = (key) =>
  ({
    season: "Season",
    prior_move: "Prior move",
    move: "Latest move",
    trend_5: "Five-session trend",
    volatility: "Volatility",
    storage: "Storage",
    weather: "Weather revision",
    move_cents: "Move in cents",
  })[key] ||
  labels()[key] ||
  key.replaceAll("_", " ");
function metric(label, value, unit, cls = "") {
  return (
    '<div class="metric"><span class="label">' +
    esc(label) +
    '</span><strong class="value ' +
    esc(cls) +
    '">' +
    esc(value) +
    '</strong><span class="unit">' +
    esc(unit) +
    "</span></div>"
  );
}
async function getJSON(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("The local server returned an unreadable response.");
  }
  if (!response.ok)
    throw new Error(
      data.error ||
        data.message ||
        "The local server returned HTTP " + response.status + ".",
    );
  return data;
}
function params() {
  const p = new URLSearchParams({
    date: state.date,
    fields: state.fields.join(","),
  });
  if (state.sequence) p.set("sequence", state.sequence);
  for (const key of state.fields) {
    if (state.overrides[key] !== undefined)
      p.set("c_" + key, state.overrides[key]);
  }
  return p;
}
function showError(message) {
  el("query-error").hidden = !message;
  el("query-error").textContent = message || "";
}
function anchorFeatures() {
  if (state.data?.query?.date === state.date)
    return state.data.query.features || {};
  return {};
}
function valuesFor(key) {
  const opts = state.meta.values?.[key];
  return Array.isArray(opts) ? opts.map(String) : [];
}
function drawFields() {
  if (!state.meta) return;
  const anchor = anchorFeatures();
  el("fields").innerHTML = Object.entries(labels())
    .map(([key, label]) => {
      const active = state.fields.includes(key);
      const value = state.overrides[key] ?? anchor[key] ?? "";
      const opts = valuesFor(key);
      if (value && !opts.includes(String(value))) opts.unshift(String(value));
      const missing = !value && !opts.length;
      const locked =
        missing || (active && state.fields.length === 1 && !state.sequence);
      const choose =
        '<select data-field-value="' +
        esc(key) +
        '" aria-label="' +
        esc(label) +
        ' value">' +
        (!value ? '<option value="">No value on this date</option>' : "") +
        opts
          .map(
            (v) =>
              '<option value="' +
              esc(v) +
              '"' +
              (String(value) === v ? " selected" : "") +
              ">" +
              esc(v) +
              "</option>",
          )
          .join("") +
        "</select>";
      return (
        '<div class="field"><label class="field-label"><input type="checkbox" value="' +
        esc(key) +
        '" ' +
        (active ? "checked " : "") +
        (locked ? "disabled " : "") +
        "><span>" +
        esc(label) +
        "</span></label>" +
        (active && !missing
          ? choose
          : '<p class="field-value">' +
            esc(
              value ||
                (opts.length
                  ? "Unavailable on selected date"
                  : "Unavailable in this sample"),
            ) +
            "</p>") +
        "</div>"
      );
    })
    .join("");
}
function renderControlDate() {
  const dates = state.meta.dates,
    ix = dates.indexOf(state.date);
  const input = el("date-input");
  input.min = dates[0];
  input.max = dates[dates.length - 1];
  input.value = state.date;
  el("prev-date").disabled = ix <= 0;
  el("next-date").disabled = ix < 0 || ix >= dates.length - 1;
}
function renderMode() {
  const exact = state.mode === "exact";
  el("exact-tab").setAttribute("aria-pressed", String(exact));
  el("analogue-tab").setAttribute("aria-pressed", String(!exact));
  el("exact-controls").hidden = !exact;
  el("analogue-controls").hidden = exact;
  if (state.data?.analogue?.weights) {
    const names = {
      change_1: "One-session price change",
      change_5: "Five-session price change",
      season: "Season",
      vol_5: "Five-session volatility",
      storage_gap: "Storage gap (Bcf)",
      hdd_revision: "Five-day HDD revision",
      cdd_revision: "Five-day CDD revision",
    };
    el("weights").innerHTML = Object.entries(state.data.analogue.weights)
      .map(
        ([k, v]) =>
          '<div class="weight"><span>' +
          esc(names[k] || k.replaceAll("_", " ")) +
          "</span><span>" +
          esc(numeric(v, 2)) +
          "</span></div>",
      )
      .join("");
  }
  if (state.data) {
    const p = params();
    p.set("mode", state.mode);
    el("export").href = "/api/export?" + p;
  }
}
function highlightChange(v) {
  return '<span class="number ' + tone(v) + '">' + esc(sign(v)) + "</span>";
}
function outcomeOf(m) {
  return m.outcomes?.["5"];
}
function isFlatMove(v) {
  return finite(v) && Math.abs(v) < 1e-8;
}
function median(values) {
  const a = values.filter(finite).sort((a, b) => a - b);
  if (!a.length) return null;
  const m = Math.floor(a.length / 2);
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}
function makeChart(matches, query, baseline, exact) {
  const sample = exact
    ? [...matches].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 8)
    : matches.slice(0, 10);
  const histories = sample.map((m) => ({ name: m.date, values: series(m) }));
  const qPre = before(query);
  const originOnly = [...qPre, 100, ...Array(10).fill(null)];
  const allPaths = matches.map((m) => series(m));
  const middle = Array.from({ length: 16 }, (_, i) =>
    median(allPaths.map((s) => s[i])),
  );
  const bench = Array.isArray(baseline?.path) ? baseline.path : [];
  const basePath = [...Array(5).fill(null), ...bench];
  const all = [...histories.map((h) => h.values), originOnly, middle, basePath]
    .flat()
    .filter(finite);
  if (!histories.length || !all.length)
    return '<p class="inline-empty">No historical paths meet this query.</p>';
  let lo = Math.min(...all),
    hi = Math.max(...all);
  const pad = Math.max((hi - lo) * 0.1, 0.4);
  lo -= pad;
  hi += pad;
  const width = 800,
    height = 232,
    left = 52,
    right = 25,
    top = 23,
    bottom = 30,
    plotW = width - left - right,
    plotH = height - top - bottom;
  const x = (i) => left + (i / 15) * plotW,
    y = (v) => top + ((hi - v) / (hi - lo)) * plotH;
  const points = (arr) => {
    let last = false;
    return arr
      .slice(0, 16)
      .map((v, i) => {
        if (!finite(v)) {
          last = false;
          return "";
        }
        const cmd = last ? "L" : "M";
        last = true;
        return cmd + x(i).toFixed(2) + "," + y(v).toFixed(2);
      })
      .join(" ");
  };
  const marks = [];
  for (let k = 0; k < 5; k++) {
    const v = lo + ((hi - lo) * k) / 4,
      yy = y(v);
    marks.push(
      '<line x1="' +
        left +
        '" x2="' +
        (width - right) +
        '" y1="' +
        yy +
        '" y2="' +
        yy +
        '" stroke="#e2e8e1"/><text x="' +
        (left - 7) +
        '" y="' +
        (yy + 4) +
        '" text-anchor="end" font-size="11">' +
        v.toFixed(1) +
        "</text>",
    );
  }
  for (const i of [0, 3, 5, 7, 10, 15]) {
    const n = i - 5;
    marks.push(
      '<text x="' +
        x(i) +
        '" y="' +
        (height - 9) +
        '" text-anchor="middle" font-size="11">' +
        (n > 0 ? "+" : "") +
        n +
        "</text>",
    );
  }
  const x0 = x(5),
    xx = width - right;
  return (
    '<svg viewBox="0 0 ' +
    width +
    " " +
    height +
    '" role="img" aria-label="Historical spot prices indexed to 100 on each match date. Outcomes start to the right of the vertical line.">' +
    '<rect x="' +
    x0 +
    '" y="' +
    top +
    '" width="' +
    (xx - x0) +
    '" height="' +
    plotH +
    '" fill="#f4f8f2"/>' +
    marks.join("") +
    '<line x1="' +
    left +
    '" x2="' +
    xx +
    '" y1="' +
    y(100) +
    '" y2="' +
    y(100) +
    '" stroke="#a8b6ac" stroke-dasharray="2 4"/>' +
    histories
      .map(
        (h) =>
          '<path d="' +
          points(h.values) +
          '" fill="none" stroke="#91b19f" stroke-width="1.35" opacity=".82"><title>' +
          esc(h.name) +
          "</title></path>",
      )
      .join("") +
    (bench.length
      ? '<path d="' +
        points(basePath) +
        '" fill="none" stroke="#485f81" stroke-width="2.3" stroke-dasharray="5 4"/>'
      : "") +
    '<path d="' +
    points(middle) +
    '" fill="none" stroke="#17695a" stroke-width="2.8"/>' +
    '<path d="' +
    points(originOnly) +
    '" fill="none" stroke="#aa6026" stroke-width="2.8"/>' +
    '<line x1="' +
    x0 +
    '" x2="' +
    x0 +
    '" y1="' +
    top +
    '" y2="' +
    (height - bottom) +
    '" stroke="#425e50" stroke-dasharray="4 3"/>' +
    '<circle cx="' +
    x0 +
    '" cy="' +
    y(100) +
    '" r="3.8" fill="#163f35" stroke="white" stroke-width="1.4"/>' +
    '<text x="' +
    (x0 - 6) +
    '" y="14" text-anchor="end" font-size="11">Lookback</text><text x="' +
    (x0 + 6) +
    '" y="14" font-size="11">Subsequent observations →</text></svg>'
  );
}
function before(m) {
  let pre = Array.isArray(m?.path_pre) ? m.path_pre : [];
  if (pre.length === 6) pre = pre.slice(0, -1);
  pre = pre.slice(-5);
  return [...Array(5 - pre.length).fill(null), ...pre];
}
function series(m) {
  const path = Array.isArray(m.path) ? m.path.slice(0, 11) : [];
  return [...before(m), ...path, ...Array(11 - path.length).fill(null)];
}
function ruleSentence(result, matches) {
  const conditions = Object.entries(result.conditions || {})
    .map(([k, v]) => shortLabel(k) + " = " + (v ?? "unavailable"))
    .join(" + ");
  const summary = result.summary?.horizons?.["5"] || {};
  const countUp = summary.up ?? matches.filter((m) => outcomeOf(m) > 0).length;
  const steps = Array.isArray(result.sequence)
    ? result.sequence.join(" → ")
    : state.sequence.split(",").filter(Boolean).join(" → ");
  const definition = [steps ? "Moves: " + steps : "", conditions]
    .filter(Boolean)
    .join(" + ");
  const prefix = definition ? "<strong>" + esc(definition) + ".</strong> " : "";
  if (!matches.length)
    return (
      prefix + "No completed, distinct episode is available for this rule."
    );
  const counter = [...matches]
    .filter(
      (m) =>
        finite(outcomeOf(m)) && (outcomeOf(m) < 0 || isFlatMove(outcomeOf(m))),
    )
    .sort((a, b) => b.date.localeCompare(a.date))[0];
  const rest = counter
    ? " On " +
      esc(niceDate(counter.date)) +
      ", the five-observation change was " +
      highlightChange(outcomeOf(counter)) +
      " $/MMBtu."
    : " Every distinct match in this sample finished higher at five observations.";
  return (
    prefix +
    esc(count(countUp)) +
    " of " +
    esc(count(matches.length)) +
    " distinct episodes finished higher five observations later." +
    rest
  );
}
function statusText(status) {
  return (
    {
      unavailable_features:
        "At least one selected condition lacks a usable value. Choose an available value or remove that condition.",
      no_matches:
        "No earlier episode met these conditions with a complete outcome window.",
      insufficient_matches:
        "The available distinct episodes are too few for a stable estimate.",
    }[status] || ""
  );
}
function renderResults() {
  if (!state.data) return;
  renderMode();
  const exact = state.mode === "exact",
    q = state.data.query || {},
    r = exact ? state.data.exact : state.data.analogue;
  if (!r) return;
  const matches = r.matches || [],
    occurrences = r.occurrences || matches,
    n = matches.length;
  const h = r.summary?.horizons?.["5"] || {},
    bench = exact
      ? state.data.exact_benchmark || state.data.benchmark || {}
      : state.data.analogue_benchmark || state.data.benchmark || {};
  const medianChange = h.median ?? median(matches.map(outcomeOf));
  const upRate =
    h.up_rate ??
    (n ? matches.filter((m) => outcomeOf(m) > 0).length / n : null);
  const title = exact
    ? "Matched price paths"
    : "Nearest historical price paths";
  const subtitle = exact
    ? "Distinct episodes with every selected condition in common."
    : "Nearest earlier episodes under the engine’s weighted distance.";
  const displayed = exact ? Math.min(8, n) : Math.min(10, n),
    benchLabel = bench.season || q.features?.season || "Same season";
  const status = statusText(r.status);
  let stats =
    metric(
      exact ? "Distinct episodes" : "Nearest episodes",
      count(n),
      exact
        ? count(occurrences.length) + " matching dates"
        : "ranked by similarity",
    ) +
    metric(
      "Five-session rises",
      rate(upRate),
      "share of distinct episodes",
      finite(upRate) ? "positive" : "",
    ) +
    metric(
      "Median change",
      sign(medianChange),
      "$/MMBtu after 5 observations",
      tone(medianChange),
    ) +
    metric(
      "Seasonal median",
      sign(bench.median_5),
      benchLabel + " · " + count(bench.n) + " dates",
      tone(bench.median_5),
    );
  const intro = exact
    ? ruleSentence(r, matches) +
      (finite(bench.up_rate) && n
        ? " The seasonal comparison rose on " +
          rate(bench.up_rate) +
          " of dates."
        : "")
    : "Each candidate had a complete ten-observation outcome before the selected market date. Expand a row below to see why it ranked.";
  el("results").innerHTML =
    '<div class="metrics panel">' +
    stats +
    "</div>" +
    (status ? '<div class="finding">' + esc(status) + "</div>" : "") +
    '<section class="panel content-card"><div class="card-title"><div><h2>' +
    title +
    "</h2><p>" +
    subtitle +
    '</p></div><span class="micro">PRICE INDEX<br>MATCH DATE = 100</span></div>' +
    '<div class="chart">' +
    makeChart(matches, q, bench, exact) +
    "</div>" +
    (n
      ? '<div class="chart-legend"><span><i class="key history"></i>Historical path</span><span><i class="key median"></i>Match median</span>' +
        (Array.isArray(bench.path) && bench.path.length
          ? '<span><i class="key baseline"></i>Seasonal median</span>'
          : "") +
        '<span><i class="key market-date"></i>Cutoff date · reference</span></div><p class="chart-foot">' +
        esc(
          exact
            ? "Showing " +
                displayed +
                " most recent distinct paths. The match median uses all " +
                count(n) +
                "."
            : "Showing " + displayed + " ranked paths.",
        ) +
        " Horizontal axis counts observed spot prices.</p>"
      : "") +
    '</section><div class="finding">' +
    intro +
    "</div>" +
    '<section class="panel content-card"><div class="card-title"><div><h2>' +
    (exact ? "Occurrence ledger" : "Analogue ledger") +
    "</h2><p>" +
    (exact
      ? "Inspect every matching date or isolate non-overlapping episodes."
      : "Inspect the distance and the largest difference for each episode.") +
    '</p></div></div><div id="ledger"></div></section>';
  renderLedger();
}
function renderLedger() {
  const exact = state.mode === "exact",
    r = exact ? state.data.exact : state.data.analogue;
  const matches = r.matches || [],
    all = r.occurrences || matches;
  const distinct = new Set(matches.map((m) => m.date));
  const rows = exact
    ? state.rowSet === "all"
      ? [...all]
      : [...matches]
    : [...matches];
  if (exact) rows.sort((a, b) => b.date.localeCompare(a.date));
  const filtered = rows.filter((m) => {
    const v = outcomeOf(m);
    return (
      state.outcome === "all" ||
      (finite(v) &&
        { up: v > 0, down: v < 0, flat: isFlatMove(v), nonrise: v <= 0 }[
          state.outcome
        ])
    );
  });
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  state.page = Math.min(state.page, pages - 1);
  const start = state.page * PAGE_SIZE,
    visible = filtered.slice(start, start + PAGE_SIZE);
  const opts = (pairs, cur) =>
    pairs
      .map(
        ([k, v]) =>
          '<option value="' +
          k +
          '"' +
          (cur === k ? " selected" : "") +
          ">" +
          v +
          "</option>",
      )
      .join("");
  const controls =
    '<div class="table-controls">' +
    (exact
      ? '<label>Episodes <select id="row-set">' +
        opts(
          [
            ["all", "All matching dates"],
            ["distinct", "Distinct only"],
          ],
          state.rowSet,
        ) +
        "</select></label>"
      : "<span></span>") +
    '<label>Five-session outcome <select id="outcome">' +
    opts(
      [
        ["all", "All outcomes"],
        ["up", "Rose"],
        ["nonrise", "Flat or fell"],
        ["down", "Fell"],
        ["flat", "Flat"],
      ],
      state.outcome,
    ) +
    "</select></label></div>";
  const body = visible
    .map((m) => {
      const reason = Array.isArray(m.reasons)
        ? m.reasons.join(" · ")
        : m.reasons || "";
      const biggest = m.biggest_difference || "";
      const why =
        '<details class="row-detail"><summary>' +
        esc(numeric(m.distance, 3)) +
        " · Details</summary><small>" +
        esc(reason) +
        "</small><small>Largest difference: " +
        esc(biggest || "Unavailable") +
        "</small></details>";
      const observed = Object.entries(m.features || {})
        .filter(([, v]) => v !== null && v !== undefined)
        .map(
          ([k, v]) =>
            "<small>" + esc(shortLabel(k)) + " · " + esc(v) + "</small>",
        )
        .join("");
      const episode =
        '<details class="row-detail"><summary>' +
        (distinct.has(m.date) ? "Distinct" : "Overlapping") +
        "</summary>" +
        observed +
        "</details>";
      return (
        '<tr><td class="mono">' +
        esc(iso(m.date)) +
        '</td><td class="number">' +
        numeric(m.price) +
        "</td>" +
        ["1", "5", "10"]
          .map(
            (key) =>
              '<td class="number">' +
              highlightChange(m.outcomes?.[key]) +
              "</td>",
          )
          .join("") +
        "<td>" +
        (exact ? episode : why) +
        "</td></tr>"
      );
    })
    .join("");
  const table =
    '<div class="table-wrap"><table class="' +
    (exact ? "exact-table" : "analogue-table") +
    '"><thead><tr><th scope="col">Match date</th><th scope="col" class="number">Spot price</th>' +
    '<th scope="col" class="number">Δ 1 obs.</th><th scope="col" class="number">Δ 5 obs.</th><th scope="col" class="number">Δ 10 obs.</th><th scope="col">' +
    (exact ? "Episode" : "Distance") +
    "</th></tr></thead><tbody>" +
    body +
    "</tbody></table></div>";
  el("ledger").innerHTML =
    controls +
    (visible.length
      ? table
      : '<p class="inline-empty">No dates meet the current selection.</p>') +
    '<div class="table-foot"><span>' +
    (filtered.length
      ? start +
        1 +
        "–" +
        (start + visible.length) +
        " of " +
        count(filtered.length)
      : "0 dates") +
    "</span>" +
    '<div class="pagers"><button type="button" id="previous-page" ' +
    (state.page === 0 ? "disabled" : "") +
    ">Previous</button>" +
    '<button type="button" id="next-page" ' +
    (state.page === pages - 1 ? "disabled" : "") +
    ">Next</button></div></div>";
  el("row-set")?.addEventListener("change", (e) => {
    state.rowSet = e.target.value;
    state.page = 0;
    renderLedger();
  });
  el("outcome").addEventListener("change", (e) => {
    state.outcome = e.target.value;
    state.page = 0;
    renderLedger();
  });
  el("previous-page").onclick = () => {
    state.page--;
    renderLedger();
  };
  el("next-page").onclick = () => {
    state.page++;
    renderLedger();
  };
}
async function refresh() {
  const request = ++state.request;
  showError("");
  el("work-area").setAttribute("aria-busy", "true");
  el("export").removeAttribute("href");
  try {
    const data = await getJSON("/api/query?" + params());
    if (request !== state.request) return;
    state.data = data;
    state.page = 0;
    drawFields();
    renderResults();
    const q = data.query || {};
    el("date-note").textContent = finite(q.price)
      ? "Selected spot price · $" + numeric(q.price) + " / MMBtu"
      : "";
  } catch (e) {
    if (request !== state.request) return;
    state.data = null;
    el("results").innerHTML = "";
    showError(e.message);
  } finally {
    if (request === state.request)
      el("work-area").setAttribute("aria-busy", "false");
  }
}
function setDate(date) {
  if (!date || !state.meta) return;
  const dates = state.meta.dates;
  let ix = dates.indexOf(date);
  if (ix < 0) {
    let lo = 0,
      hi = dates.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (dates[m] <= date) lo = m + 1;
      else hi = m;
    }
    ix = Math.max(0, lo - 1);
  }
  const snapped = dates[ix];
  state.date = snapped;
  state.overrides = {};
  state.page = 0;
  renderControlDate();
  refresh().then(() => {
    if (date !== snapped && state.date === snapped)
      el("date-note").textContent += " · Closest available observation shown.";
  });
}
function viewMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  state.page = 0;
  state.rowSet = "all";
  state.outcome = "all";
  renderMode();
  renderResults();
}
function researchRule(p) {
  const conditions = Object.entries(p.conditions || {})
    .map(([k, v]) => shortLabel(k) + " = " + v)
    .join(" + ");
  const t = p.train || {},
    h = p.holdout || {},
    c = h.counterexample;
  const dates = (h.dates || []).join(", ");
  return (
    '<article class="rule"><h3>' +
    esc(conditions || "Rule") +
    "</h3><p>Training · " +
    esc(pp(t.excess_rate)) +
    " versus season across " +
    count(t.n) +
    " episodes.</p>" +
    "<p><strong>Holdout · " +
    rate(h.positive_rate) +
    " rose</strong> across " +
    count(h.n) +
    " episodes, versus " +
    rate(h.seasonal_rate) +
    " in the seasonal comparison.</p>" +
    "<p>Median change " +
    highlightChange(h.median_change) +
    " $/MMBtu. " +
    esc(p.status || "") +
    "</p>" +
    '<details class="rule-details"><summary>Dates and counterexample</summary><div>' +
    (c
      ? "Counterexample on " +
        esc(niceDate(c.date)) +
        " · " +
        highlightChange(c.change) +
        " $/MMBtu."
      : "No holdout counterexample recorded.") +
    "</div><div>Holdout episodes · " +
    esc(dates || "None") +
    "</div></details></article>"
  );
}
function forecastMetrics(s) {
  return (
    '<div class="metrics research-metrics">' +
    metric("Evaluation dates", count(s.n), "paired forecasts") +
    metric("Analogue MAE", numeric(s.analogue_mae, 3), "$/MMBtu") +
    metric("Seasonal MAE", numeric(s.seasonal_mae, 3), "$/MMBtu") +
    metric(
      "Difference",
      sign(s.mae_difference, 3),
      "analogue minus seasonal",
      finite(s.mae_difference)
        ? s.mae_difference < 0
          ? "positive"
          : s.mae_difference > 0
            ? "negative"
            : ""
        : "",
    ) +
    "</div>"
  );
}
function ciNote(ci) {
  return Array.isArray(ci) && ci.every(finite)
    ? "Paired 95% uncertainty interval for the difference: " +
        sign(ci[0], 3) +
        " to " +
        sign(ci[1], 3) +
        " $/MMBtu."
    : "A paired uncertainty interval is unavailable for this sample.";
}
function drawResearch(data) {
  const ev = data.evaluation || {},
    s = ev.summary || {},
    d = data.discovery || {},
    patterns = d.patterns || [],
    vint = data.vintage;
  const n = ev.records?.length || 0;
  const first = n ? ev.records[0].date : null,
    last = n ? ev.records[n - 1].date : null;
  const dateRange =
    first && last ? " (" + niceDate(first) + " – " + niceDate(last) + ")" : "";
  let out =
    '<section class="panel research-card"><div class="card-title"><div><h2>Walk-forward comparison</h2><p>Five-observation price change · lower mean absolute error is better.</p></div></div>';
  if (s.n) {
    out +=
      forecastMetrics(s) +
      "<p>" +
      esc(ciNote(s.ci95)) +
      "</p><p>Both methods use the same evaluation dates" +
      esc(dateRange) +
      ". A negative difference favors the analogue method.</p>";
  } else {
    out +=
      "<p>" +
      esc(data.status || "No paired evaluation results are available.") +
      "</p>";
  }
  if (ev.caution) out += "<p>" + esc(ev.caution) + "</p>";
  out += "</section>";
  if (vint && vint.n) {
    out +=
      '<section class="panel research-card"><h2>As-published replay</h2>' +
      "<p>A separate monthly evaluation uses dated ALFRED snapshots and measures five new spot observations strictly after each decision.</p>" +
      forecastMetrics(vint) +
      "<p>" +
      esc(ciNote(vint.ci95)) +
      "</p>" +
      (vint.first_decision || vint.first_date
        ? "<p>Decision dates · " +
          esc(niceDate(vint.first_decision || vint.first_date)) +
          " – " +
          esc(niceDate(vint.last_decision || vint.last_date)) +
          ".</p>"
        : "") +
      "</section>";
  }
  out +=
    '<section class="panel research-card"><h2>Rule discovery</h2>' +
    "<p>Rules are selected using earlier observations. The holdout reports what happened on later dates satisfying those same rules.</p>";
  if (finite(d.tested_rules) || finite(d.eligible_rules)) {
    out +=
      "<p>" +
      count(d.tested_rules) +
      " rules tested · " +
      count(d.eligible_rules) +
      " eligible before holdout.</p>";
  }
  if (d.caution) out += '<div class="finding">' + esc(d.caution) + "</div>";
  out += patterns.length
    ? '<div class="research-rules">' +
      patterns.map(researchRule).join("") +
      "</div>"
    : "<p>No rules qualified under the study settings.</p>";
  el("research-content").innerHTML = out + "</section>";
}
async function setView() {
  const research = location.hash === "#research";
  el("explore-view").hidden = research;
  el("research-view").hidden = !research;
  [
    ["nav-research", research],
    ["nav-explore", !research],
  ].forEach(([id, active]) => {
    if (active) el(id).setAttribute("aria-current", "page");
    else el(id).removeAttribute("aria-current");
  });
  if (!research) return;
  if (state.research) {
    drawResearch(state.research);
    return;
  }
  if (state.researchPending) return;
  state.researchPending = true;
  el("research-content").innerHTML =
    '<p class="panel empty">Loading research results…</p>';
  try {
    state.research = await getJSON("/api/research");
    drawResearch(state.research);
  } catch (e) {
    el("research-content").innerHTML =
      '<div class="error">' + esc(e.message) + "</div>";
  } finally {
    state.researchPending = false;
  }
}
async function init() {
  window.addEventListener("hashchange", setView);
  setView();
  el("exact-tab").onclick = () => viewMode("exact");
  el("analogue-tab").onclick = () => viewMode("analogue");
  el("date-input").addEventListener("change", (e) => setDate(e.target.value));
  for (const [id, diff] of [
    ["prev-date", -1],
    ["next-date", 1],
  ]) {
    el(id).onclick = () => {
      const ds = state.meta.dates;
      setDate(ds[ds.indexOf(state.date) + diff]);
    };
  }
  el("fields").addEventListener("change", (e) => {
    if (e.target.matches("input[type=checkbox]")) {
      const key = e.target.value;
      if (e.target.checked) {
        if (!state.fields.includes(key)) state.fields.push(key);
      } else {
        state.fields = state.fields.filter((x) => x !== key);
        delete state.overrides[key];
      }
      drawFields();
      refresh();
    } else if (e.target.matches("select[data-field-value]")) {
      const key = e.target.dataset.fieldValue;
      if (
        e.target.value &&
        e.target.value !== String(anchorFeatures()[key] ?? "")
      )
        state.overrides[key] = e.target.value;
      else delete state.overrides[key];
      refresh();
    }
  });
  function applySequence(seq, only = false) {
    const parts = seq.trim() === "" ? [] : seq.split(",").map((x) => x.trim());
    const valid =
      parts.length <= 10 &&
      parts.every((x) => /^(up|down|flat|[+-]\d{1,9}|0|\*)$/i.test(x)) &&
      (!parts.length || parts.some((x) => x !== "*"));
    if (!valid) {
      showError(
        "Enter one to ten moves separated by commas. Use Up, Down, Flat, signed whole cents, or * alongside a specific move.",
      );
      return;
    }
    if (only) {
      state.fields = [];
      state.rowSet = "all";
      state.outcome = "all";
    }
    if (!parts.length && !state.fields.length)
      state.fields = [...state.meta.default_fields];
    if (only) state.overrides = {};
    state.sequence = parts.join(",");
    el("sequence-input").value = parts.join(", ");
    drawFields();
    refresh();
  }
  el("sequence-form").addEventListener("submit", (e) => {
    e.preventDefault();
    applySequence(el("sequence-input").value);
  });
  el("preset").onclick = () => applySequence("Down,+8", true);
  el("physical-preset").onclick = () => {
    state.fields = ["storage", "weather"];
    state.overrides = { storage: "Deficit", weather: "HDD Up / CDD Flat" };
    state.outcome = "all";
    state.rowSet = "all";
    applySequence("");
  };
  el("reset").onclick = () => {
    state.sequence = "";
    el("sequence-input").value = "";
    state.fields = [...state.meta.default_fields];
    state.overrides = {};
    state.outcome = "all";
    state.rowSet = "all";
    drawFields();
    refresh();
  };
  try {
    const m = await getJSON("/api/meta");
    state.meta = m;
    el("physical-preset").hidden = !(
      m.values?.storage?.includes("Deficit") &&
      m.values?.weather?.includes("HDD Up / CDD Flat")
    );
    if (!m.dates?.length)
      throw new Error(
        "The loaded dataset contains no usable spot observations.",
      );
    state.date = m.default_date || m.dates[m.dates.length - 1];
    state.fields = [...m.default_fields];
    const ds = m.dates;
    el("coverage").innerHTML =
      "<strong>" +
      esc(ds[0]) +
      " — " +
      esc(ds[ds.length - 1]) +
      "</strong>" +
      esc(count(ds.length)) +
      " spot observations · retrospective";
    el("source").textContent = [
      typeof m.source === "string"
        ? m.source
        : "Henry Hub daily spot observations.",
      m.availability_note,
    ]
      .filter(Boolean)
      .join(". ")
      .replace(/\.\./g, ".");
    renderControlDate();
    drawFields();
    await refresh();
    if (state.research && location.hash === "#research")
      drawResearch(state.research);
  } catch (e) {
    showError(e.message);
    el("results").innerHTML = "";
    el("work-area").setAttribute("aria-busy", "false");
  }
}
init();
