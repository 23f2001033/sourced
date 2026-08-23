"use strict";

/* Static snapshot of a pipeline run.
 *
 * Everything is a flat file: an index, one JSON per record, and one PNG per
 * cited page. The red region is drawn here rather than server-side, from the
 * bounding box the extractor recorded, so the interaction that carries the
 * whole idea -- click a value, see the cell it was read from -- survives with
 * no backend at all. */

const state = { items: [], pages: {}, selected: null, record: null,
                filter: "", status: "" };

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => v === null || v === undefined ? "n/a" : (v * 100).toFixed(1) + "%";

async function json(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response.json();
}

// ------------------------------------------------------------------ stat bar

async function loadStats() {
  try {
    const r = await json("results.json");
    const t = r.test_metrics;
    const stats = [
      [pct(t.accuracy.precision_published), "precision, published values"],
      [pct(t.validation_and_enrichment.auto_publish_rate), "auto-publishable"],
      [`${t.source_location.wrong_part_traps_correctly_refused}/${t.source_location.wrong_part_traps}`,
       "wrong-part traps refused"],
      [r.calibration.expected_calibration_error.toFixed(3), "calibration error"],
      [pct(t.source_location.source_location_rate), "source located"],
      [pct(r.category_routing.schema_routing_accuracy), "routed to right schema"],
      [Math.round(t.scalable_engine.throughput_skus_per_min).toLocaleString(),
       "SKUs / minute"],
    ];
    el("statbar").innerHTML = stats
      .map(([v, l]) => `<div class="s"><b>${esc(v)}</b><span>${esc(l)}</span></div>`)
      .join("");
  } catch (error) {
    el("statbar").remove();
  }
}

// ---------------------------------------------------------------- list pane

function renderList() {
  const needle = state.filter.trim().toLowerCase();
  const items = state.items.filter((item) => {
    if (state.status && item.source_status !== state.status) return false;
    if (!needle) return true;
    return item.mpn.toLowerCase().includes(needle) ||
           (item.manufacturer || "").toLowerCase().includes(needle);
  });

  el("list-count").textContent = `${items.length} of ${state.items.length} SKUs`;
  el("product-list").innerHTML = items.slice(0, 400).map((item) => `
    <li data-slug="${esc(item.slug)}" class="${item.slug === state.selected ? "active" : ""}">
      <div class="mpn">${esc(item.mpn)}</div>
      <div class="meta">
        <span>${esc(item.manufacturer || "unknown maker")}</span>
        ${item.source_status === "verified"
          ? `<span>${item.published} published</span>`
          : `<span class="tag abstained">no source</span>`}
      </div>
    </li>`).join("");

  document.querySelectorAll("#product-list li").forEach((node) =>
    node.addEventListener("click", () => select(node.dataset.slug)));
}

// -------------------------------------------------------------- detail pane

async function select(slug) {
  state.selected = slug;
  renderList();
  state.record = await json(`records/${slug}.json`);
  renderDetail();
}

function renderDetail() {
  const record = state.record;
  el("empty").hidden = true;
  const detail = el("detail");
  detail.hidden = false;

  if (record.source_status !== "verified") {
    const abstention = record.abstention || {};
    detail.innerHTML = `
      <h1>${esc(record.mpn)}</h1>
      <div class="mpn-line">${esc(record.manufacturer || "manufacturer unresolved")}</div>
      <div class="banner warn">
        <strong>${esc(abstention.code || "no_source_located")}</strong>
        ${esc(abstention.detail || "")}
        <div class="hint">${esc(abstention.resolution_hint || "")}</div>
      </div>
      <p class="small">No verified source means no extraction. Nothing was inferred
      from model memory, and no value was published for this part. A sibling part's
      datasheet matches on manufacturer, series and layout — only the part number
      distinguishes them, so only the part number is trusted.</p>`;
    el("source-body").innerHTML =
      `<div class="nosource">This record has no verified source document, so there
       is nothing to show. That is the correct outcome, not a gap.</div>`;
    return;
  }

  const attributes = record.attributes || {};
  const provenance = Object.fromEntries(
    (record.provenance || []).map((p) => [p.canonical_key, p]));
  const commerce = record.commerce || {};
  const completeness = record.completeness || {};

  const rows = Object.keys(attributes).map((key) => {
    const attr = attributes[key];
    const reason = attr.abstention_reason;
    const prov = provenance[key] || {};
    const value = attr.value === null || attr.value === undefined
      ? "&mdash;"
      : `${esc(attr.value)}${attr.unit ? `<span class="unit">${esc(attr.unit)}</span>` : ""}`;
    return `
      <tr class="attr" data-key="${esc(key)}">
        <td class="key">${esc(key)}
          ${attr.criticality === "safety" ? '<span class="tag safety">safety</span>' : ""}
        </td>
        <td class="value">${value}
          ${reason ? `<div class="reason"><em>${esc(reason.code)}</em> — ${esc(reason.detail)}
             <br>${esc(reason.resolution_hint)}</div>` : ""}
        </td>
        <td><span class="tag ${esc(attr.resolution)}">${esc(attr.resolution)}</span></td>
        <td>${prov.tier ? `<span class="tag">${esc(prov.tier)}</span>` : ""}
            ${prov.locator ? `<span class="tag">${esc(prov.locator)}</span>` : ""}</td>
        <td><div>${(attr.confidence ?? 0).toFixed(3)}</div>
            <div class="conf-bar"><i style="width:${Math.round((attr.confidence ?? 0) * 100)}%"></i></div></td>
      </tr>`;
  }).join("");

  detail.innerHTML = `
    <h1>${esc(commerce.title || record.mpn)}</h1>
    <div class="mpn-line">${esc(record.mpn)} · ${esc(record.manufacturer || "unknown")} ·
      ${esc(record.category_label || "uncategorised")} (${esc(record.category_code || "-")})</div>

    <div class="stats">
      <div class="stat"><b>${completeness.published_count ?? 0}</b><span>published</span></div>
      <div class="stat"><b>${completeness.review_count ?? 0}</b><span>review</span></div>
      <div class="stat"><b>${completeness.abstained_count ?? 0}</b><span>abstained</span></div>
      <div class="stat"><b>${completeness.required_filled ?? 0}/${completeness.required_total ?? 0}</b>
        <span>required filled</span></div>
    </div>

    <h2>Attributes</h2>
    <table>
      <thead><tr><th>attribute</th><th>value</th><th>decision</th><th>evidence</th>
        <th>calibrated confidence</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>

    <h2>Commerce output</h2>
    <div class="copy">
      <div class="title">${esc(commerce.title || "")}</div>
      <div id="description">${renderDescription(commerce)}</div>
      <div class="reason" style="margin-top:8px">
        generator: <span class="tag">${esc(commerce.description_generator || "n/a")}</span>
      </div>
    </div>

    <h2>Facets</h2>
    <div class="facets">${Object.entries(commerce.facets || {})
      .map(([k, v]) => `<span class="tag">${esc(k)}: ${esc(v)}</span>`).join("")}</div>

    ${completeness.blocking_for_publish?.length ? `
      <h2>Blocking publication</h2>
      <div class="small">${completeness.blocking_for_publish.map(esc).join(", ")}</div>` : ""}`;

  document.querySelectorAll("tr.attr").forEach((row) =>
    row.addEventListener("click", () => showSource(row.dataset.key)));
  document.querySelectorAll(".claim").forEach((node) =>
    node.addEventListener("click", () => showSource(node.dataset.attr)));

  // Prefer an attribute whose citation can actually be outlined -- landing on a
  // listing-sourced value would show the argument at its weakest.
  const isDrawable = (key) => {
    const p = provenance[key];
    return Boolean(p && p.bbox && state.pages[`${p.source_id}:${p.page}`]);
  };
  const published = Object.keys(attributes)
    .filter((k) => attributes[k].resolution === "published");
  const first = published.find(isDrawable) || published[0] || Object.keys(attributes)[0];
  if (first) showSource(first);
}

function renderDescription(commerce) {
  const text = commerce.description || "";
  const claims = (commerce.description_claims || [])
    .slice().sort((a, b) => a.span_start - b.span_start);
  let out = "", cursor = 0;
  for (const claim of claims) {
    if (claim.span_start < cursor) continue;
    out += esc(text.slice(cursor, claim.span_start));
    const cls = claim.source_attribute ? "claim" : "claim untraced";
    out += `<span class="${cls}" data-attr="${esc(claim.source_attribute || "")}"
             title="${claim.source_attribute
               ? `licensed by ${esc(claim.source_attribute)} — click for its provenance`
               : "no source attribute"}">${esc(text.slice(claim.span_start, claim.span_end))}</span>`;
    cursor = claim.span_end;
  }
  out += esc(text.slice(cursor));
  return out;
}

// -------------------------------------------------------------- source pane

function showSource(key) {
  if (!key || !state.record) return;
  document.querySelectorAll("tr.attr").forEach((row) =>
    row.classList.toggle("active", row.dataset.key === key));

  const prov = (state.record.provenance || []).find((p) => p.canonical_key === key);
  const body = el("source-body");
  if (!prov) { body.innerHTML = `<div class="empty small">No provenance row.</div>`; return; }

  const checks = Object.entries(prov.checks || {}).map(([name, check]) =>
    `<div class="check ${check.passed ? "ok" : "bad"}">${esc(name)}${
      check.detail ? ` — ${esc(check.detail)}` : ""}</div>`).join("");

  body.innerHTML = `
    <div class="kv"><b>attribute</b> ${esc(prov.canonical_key)}</div>
    <div class="kv"><b>value</b> ${esc(prov.value_text ?? "—")} ${esc(prov.unit || "")}</div>
    <div class="kv"><b>decision</b> <span class="tag ${esc(prov.resolution)}">${esc(prov.resolution)}</span></div>
    <div class="kv"><b>tier</b> ${esc(prov.tier || "—")} · ${esc(prov.locator || "—")}</div>
    <div class="kv"><b>source</b> ${esc(prov.source_id || "—")} (${esc(prov.source_type || "—")})</div>
    <div class="kv"><b>page</b> ${prov.page ?? "—"}</div>
    <div class="kv"><b>producer</b> ${esc(prov.model_id || "—")}</div>
    ${prov.abstention_code ? `
      <div class="banner warn" style="margin:8px 0">
        <strong>${esc(prov.abstention_code)}</strong>${esc(prov.abstention_detail || "")}
        <div class="hint">${esc(prov.resolution_hint || "")}</div>
      </div>` : ""}
    <div class="kv" style="margin-top:8px"><b>cited span</b></div>
    <pre>${esc(prov.span || "(none)")}</pre>
    <div class="kv"><b>checks</b></div>
    ${checks || '<div class="small">none recorded</div>'}
    ${renderPage(prov)}`;
}

function renderPage(prov) {
  const meta = state.pages[`${prov.source_id}:${prov.page}`];
  if (!meta || !prov.bbox || prov.bbox.length !== 4) {
    return `<div class="nosource">This source carries no page geometry — a
      structured listing field or the distributor's own row — so there is no
      region to outline.</div>`;
  }
  const [x0, top, x1, bottom] = prov.bbox.map(Number);
  const style = [
    `left:${(x0 / meta.width) * 100}%`,
    `top:${(top / meta.height) * 100}%`,
    `width:${((x1 - x0) / meta.width) * 100}%`,
    `height:${((bottom - top) / meta.height) * 100}%`,
  ].join(";");
  return `
    <figure class="pageview">
      <img src="${esc(meta.file)}" alt="page ${prov.page} of ${esc(prov.source_id)}"
           loading="lazy">
      <div class="hl" style="${style}"></div>
      <figcaption>${esc(prov.source_id)} · page ${prov.page} · the outlined cell
        is where this value was read from</figcaption>
    </figure>`;
}

// ------------------------------------------------------------------- boot

el("filter").addEventListener("input", (event) => {
  state.filter = event.target.value;
  renderList();
});
el("status-filter").addEventListener("change", (event) => {
  state.status = event.target.value;
  renderList();
});
el("notice-close").addEventListener("click", () => el("notice").classList.add("hidden"));

loadStats();
json("index.json").then((data) => {
  state.items = data.items;
  state.pages = data.pages || {};
  renderList();
  const first = state.items.find((i) => i.has_page) ||
                state.items.find((i) => i.source_status === "verified");
  if (first) select(first.slug);
}).catch((error) => {
  el("list-count").textContent = "no records";
  el("empty").textContent = `Could not load the catalogue (${error.message}).`;
});
