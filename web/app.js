"use strict";

const state = { items: [], selected: null, record: null, filter: "", status: "" };

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response.json();
}

// ---------------------------------------------------------------- list pane

async function loadList() {
  const query = state.status ? `?status=${encodeURIComponent(state.status)}&limit=1000` : "?limit=1000";
  const data = await api(`/api/products${query}`);
  state.items = data.items;
  renderList();
}

function renderList() {
  const needle = state.filter.trim().toLowerCase();
  const items = state.items.filter((item) =>
    !needle || item.mpn.toLowerCase().includes(needle) ||
    (item.manufacturer || "").toLowerCase().includes(needle));

  el("list-count").textContent = `${items.length} of ${state.items.length} SKUs`;
  el("product-list").innerHTML = items.map((item) => `
    <li data-mpn="${esc(item.mpn)}" class="${item.mpn === state.selected ? "active" : ""}">
      <div class="mpn">${esc(item.mpn)}</div>
      <div class="meta">
        <span>${esc(item.manufacturer || "unknown maker")}</span>
        ${item.source_status === "verified"
          ? `<span>${item.published} published</span>`
          : `<span class="tag abstained">no source</span>`}
      </div>
    </li>`).join("");

  document.querySelectorAll("#product-list li").forEach((node) =>
    node.addEventListener("click", () => select(node.dataset.mpn)));
}

// -------------------------------------------------------------- detail pane

async function select(mpn) {
  state.selected = mpn;
  renderList();
  state.record = await api(`/api/products/${encodeURIComponent(mpn)}`);
  renderDetail();
}

function confidenceCell(attr) {
  const value = attr.confidence ?? 0;
  return `<div>${value.toFixed(3)}</div>
          <div class="conf-bar"><i style="width:${Math.round(value * 100)}%"></i></div>`;
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
      <p class="small">No verified source means no extraction. Nothing was inferred from
      model memory, and no value was published for this part.</p>`;
    el("source-body").innerHTML =
      `<div class="empty small">This record has no verified source document.</div>`;
    return;
  }

  const attributes = record.attributes || {};
  const provenance = Object.fromEntries((record.provenance || []).map((p) => [p.canonical_key, p]));
  const commerce = record.commerce || {};
  const completeness = record.completeness || {};
  const order = Object.keys(attributes);

  const rows = order.map((key) => {
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
        <td>${confidenceCell(attr)}</td>
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
      <div class="stat"><b>${(record.sources_count ?? (record.provenance || []).length) ? "" : ""}${
        completeness.blocking_for_publish?.length ?? 0}</b><span>blocking</span></div>
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
        title template: <span class="tag">${esc(commerce.title_template || "")}</span>
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

  const first = order.find((k) => attributes[k].resolution === "published");
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

  const bbox = prov.bbox && prov.bbox.length === 4 ? prov.bbox.join(",") : null;
  const image = prov.source_id && prov.page && bbox
    ? `<img alt="cited region" src="/api/source/${encodeURIComponent(prov.source_id)}/page/${prov.page}.png?bbox=${bbox}">`
    : `<div class="small" style="color:#6a7280">This source has no page geometry
       (structured field or input row), so there is no region to highlight.</div>`;

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
    <div style="margin-top:10px">${image}</div>`;
}

// ------------------------------------------------------------------- boot

el("filter").addEventListener("input", (event) => {
  state.filter = event.target.value;
  renderList();
});
el("status-filter").addEventListener("change", (event) => {
  state.status = event.target.value;
  loadList();
});

api("/api/health").then((health) => {
  el("health").textContent = `llm tier: ${health.llm_tier_enabled ? "on" : "off"} · ${health.db}`;
}).catch(() => { el("health").textContent = "api unreachable"; });

loadList().catch((error) => {
  el("list-count").textContent = "no records";
  el("empty").textContent =
    `Could not load the catalogue (${error.message}). Run \`python -m sourced.eval.report --persist\` first.`;
});
