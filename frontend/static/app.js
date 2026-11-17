// Grant desk: renders the agent's recommend_grants() and get_cascade() output.
// Every figure comes from those tools; this file only lays them out.

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const pct = (x) => `${Math.round(x * 100)}%`;
const SVG = "http://www.w3.org/2000/svg";

// Line map geometry (viewBox units)
const COL_X = [24, 280, 536, 792];
const ROW = 44;
const TOP = 46;
const LABEL_MAX = 26;

const state = { rec: null, audit: null, selected: null, order: [] };

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}

function s(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

const statusOf = (a) => (a.award <= 0 ? "not_funded" : a.pct_of_request >= 100 ? "funded" : "partial");
const STATUS = { funded: "Fund in full", partial: "Partial grant", not_funded: "Not funded" };

// The allocator's assumptions, in words an officer can repeat.
const ASSUMPTION_TEXT = {
  STRONG_LINK: (v) => `A supplier worth ${Math.round(v * 100)} cents per dollar of a buyer's output counts as a full-strength dependency.`,
  HOP_DECAY: (v) => `Each extra hop multiplies exposure by ${v}.`,
  CONTAGION: (v) => `If a full-strength local supplier closes and isn't replaced, its buyer closes with probability ${pct(v)}.`,
  MAX_RISK_REDUCTION: (v) => `Fully funding a request cuts the applicant's closure risk by ${pct(v)}.`,
  EFFECT_CURVE: (v) => `Partial grants have diminishing returns: the effect scales with the share funded to the power ${v}.`,
  NEED_WEIGHT: (v) => `Tracts poorer than the corridor average raise a grant's value (weight ${v}, capped at 0.75x to 1.5x).`,
  STEP_FRACTION: (v) => `Money is allocated in slices of ${pct(v)} of each request.`,
  MIN_AWARD: (v) => `No grant is smaller than ${money.format(v)}.`,
};

/* Header */
function renderDesk() {
  const r = state.rec;
  const n = r.recommended.length + r.not_funded.length;
  const asked = [...r.recommended, ...r.not_funded].reduce((t, a) => t + a.requested, 0);
  document.getElementById("desk-sub").textContent =
    `${n} applications · ${money.format(asked)} requested · ${r.recommended.length} grants · ` +
    `${r.expected_closures_avoided_total} expected closures avoided`;
  document.getElementById("budget-allocated").textContent = money.format(r.allocated);
  document.getElementById("budget-total").textContent = money.format(r.budget);

  const bar = document.getElementById("budget-bar");
  bar.setAttribute("role", "group");
  bar.setAttribute("aria-label", `${money.format(r.allocated)} of ${money.format(r.budget)} allocated across ${r.recommended.length} grants`);
  const segs = r.recommended.map((a) => h("button", {
    type: "button", class: "budget-seg", style: `flex: ${a.award} 1 0`,
    title: `${a.name}: ${money.format(a.award)}`, "aria-label": `${a.name}, ${money.format(a.award)}`,
    onclick: () => select(a.business_id),
  }, a.name));
  if (r.unallocated > 0) segs.push(h("span", { style: `flex: ${r.unallocated} 1 0`, "aria-hidden": "true" }));
  bar.replaceChildren(...segs);

  const banner = document.getElementById("source-banner");
  banner.hidden = r.source !== "sample";
  if (r.source === "sample") {
    banner.textContent = `Saved run of the allocator, captured ${r.captured}. ` +
      "The queue is a saved snapshot; agent answers may use newer live data.";
  }
}

/* Queue */
function renderQueue() {
  const r = state.rec;
  state.order = [...r.recommended, ...r.not_funded];
  const items = [];
  const group = (label, list) => {
    if (!list.length) return;
    items.push(h("li", { class: "queue-group", "aria-hidden": "true" }, label));
    for (const a of list) {
      const st = statusOf(a);
      const amount = st === "not_funded"
        ? h("span", { class: "q-amount" }, "$0", h("small", {}, `asked ${money.format(a.requested)}`))
        : h("span", { class: "q-amount" }, money.format(a.award), h("small", {}, `${a.pct_of_request}% of ask`));
      const reach = a.downstream_exposed ? `${a.downstream_exposed} downstream` : "no downstream links";
      items.push(h("li", { class: `queue-item${st === "not_funded" ? " not-funded" : ""}` },
        h("button", { type: "button", "data-id": a.business_id, onclick: () => select(a.business_id) },
          h("span", { class: `mark ${st}`, "aria-hidden": "true" }),
          h("span", {}, h("span", { class: "q-name" }, a.name),
            h("span", { class: "q-meta" }, `risk ${pct(a.baseline_risk)} · ${reach}`)),
          amount)));
    }
  };
  group("Recommended", r.recommended);
  group("Not funded", r.not_funded);
  document.getElementById("queue-list").replaceChildren(...items);
}

/* Case */
function caseHeader(a) {
  const st = statusOf(a);
  const rank = state.rec.recommended.findIndex((x) => x.business_id === a.business_id);
  const status = st === "not_funded" ? STATUS.not_funded : `${STATUS[st]} · grant ${rank + 1} of ${state.rec.recommended.length}`;
  const decision = st === "not_funded"
    ? [h("strong", {}, "$0"), h("span", {}, `asked ${money.format(a.requested)}`)]
    : [h("strong", {}, money.format(a.award)),
       h("span", {}, `${a.pct_of_request}% of the ${money.format(a.requested)} asked`),
       h("span", {}, `${a.pct_of_budget}% of the fund`)];
  return [
    h("p", { class: `case-status ${st}` }, status),
    h("h2", {}, a.name),
    h("p", { class: "case-address" }, a.address),
    h("div", { class: "decision" }, ...decision),
    riskLine(a, st),
  ];
}

function riskLine(a, st) {
  const tag = sensitivityTag(a, st);
  const change = st === "not_funded"
    ? `Closure risk ${pct(a.baseline_risk)}, unchanged without a grant.`
    : `Closure risk ${pct(a.baseline_risk)} → ${pct(a.risk_after_award)} with this grant; ` +
      `${a.closures_avoided_by_this_grant} expected closures avoided, counting the cascade.`;
  return h("p", { class: "risk" }, change, tag, st === "not_funded" ? null : modeledTag(a));
}

function sensitivityTag(a, st) {
  if (st === "not_funded") return null;
  const sens = state.rec.sensitivity;
  if (sens.funded_in_every_scenario.includes(a.business_id)) return h("span", { class: "tag holds" }, "Receives some funding in every tested scenario");
  const scen = Object.values(sens.funded_ids_by_scenario);
  const n = scen.filter((ids) => ids.includes(a.business_id)).length;
  return h("span", { class: "tag depends" }, `Funded in ${n} of ${scen.length} assumption sets`);
}

function comparisonLine(a, st) {
  const r = state.rec;
  const per = (x) => x.closures_avoided_per_10k_if_fully_funded;
  if (st === "not_funded") {
    const w = r.weakest_funded;
    return w ? `If fully funded, ${a.name} avoids ${per(a)} expected closures per $10,000. ` +
      `The weakest funded grant, ${w.name}, avoids ${per(w)}.` : null;
  }
  // value_grants: this grant's own runner-up, i.e. where the same money would go instead.
  const own = a.runner_up;
  if (own && own.name) {
    const fmt = (x) => x.toFixed(2);
    const to = own.money_would_go_to.map((m) => `${m.name} (${money.format(m.amount)})`).join(", ");
    const verdict = own.verdict === "indistinguishable" ? " The data can't separate them."
      : own.verdict === "alternative" ? " The alternative would do better." : "";
    return `This grant avoids ${fmt(own.closures_avoided_by_this_grant)} expected closures. ` +
      `The same money would go to ${to} and avoid ${fmt(own.closures_avoided_instead)}.${verdict}`;
  }
  const ru = r.runner_up;
  if (!ru || ru.business_id === a.business_id) return null;
  return `If fully funded, ${a.name} avoids ${per(a)} expected closures per $10,000; ` +
    `the runner-up, ${ru.name}, avoids ${per(ru)}.`;
}

function reason(title, ...body) {
  return h("section", { class: "reason" }, h("h3", {}, title), ...body);
}

function renderCase(a, c) {
  const st = statusOf(a);
  const r = state.rec;
  const nb = r.neighborhood[a.business_id];
  const cmp = comparisonLine(a, st);
  const assumptions = Object.entries(r.assumptions)
    .map(([k, v]) => (ASSUMPTION_TEXT[k] ? ASSUMPTION_TEXT[k](v) : `${k}: ${v}`));
  return [
    ...caseHeader(a),
    lineMap(a, c, st),
    h("div", { class: "reasons" },
      reason(st === "not_funded" ? "The case for it" : "Why fund it",
        h("ul", { class: "reason-list" }, ...a.reasons.map((t) => h("li", {}, t)))),
      reason(st === "not_funded" ? "Why it misses out" : "Against the runner-up",
        h("p", {}, cmp || "Every applicant received something; this grant is not the one closest to being cut.")),
      reason("Who the neighborhood is",
        nb && nb.poverty_rate != null
          ? h("p", {}, `Tract ${nb.geo_id}: ${pct(nb.poverty_rate)} of residents below poverty (corridor ${pct(r.corridor_poverty)}); ` +
              `${pct(nb.no_vehicle_rate)} of households without a car.`)
          : h("p", {}, "No census data for this location."),
        h("p", { class: "fine" }, "ACS 2019 five-year estimates.")),
      reason("What we're assuming",
        h("ul", { class: "reason-list fine" }, ...assumptions.map((t) => h("li", {}, t))),
        h("p", { class: "fine" }, r.sensitivity.note)),
      reason("What's modeled", h("p", {}, r.evidence_note))),
    h("details", { class: "query" },
      h("summary", {}, `Show the ${c.paths.length} strongest paths`),
      h("ol", { class: "path-list" }, ...c.paths.map((p) =>
        h("li", {}, p.path, h("span", { class: "fine" },
          ` · ${p.hops} hop${p.hops > 1 ? "s" : ""}, link strengths ${p.link_intensities.join(", ")}, weight ${p.weight}`))))),
    askForm(a, st),
  ];
}

/* The line map: the cascade drawn as a transit line, applicant first, then each hop */
function track(x1, y1, x2, y2) {
  const half = Math.min(Math.abs(y2 - y1) / 2, (x2 - x1) / 2 - 16);
  if (half < 0) return `M${x1} ${y1} L${x2} ${y2}`;
  const mid = (x1 + x2) / 2;
  return `M${x1} ${y1} H${mid - half} L${mid + half} ${y2} H${x2}`;
}

const shortName = (n) => (n.length > LABEL_MAX ? `${n.slice(0, LABEL_MAX - 1)}…` : n);

function lineMap(a, c, st) {
  const seed = c.business.name;
  const nodes = new Map([[seed, { name: seed, col: 0, tip: `${seed}, ${c.business.address}` }]]);
  const edges = new Map();  // "parent\u0000child" -> strongest link intensity
  for (const p of c.paths) {
    const names = p.path.split(" → ");
    names.forEach((n, i) => {
      const cur = nodes.get(n);
      if (!cur || cur.col > i) nodes.set(n, { name: n, col: i, tip: cur?.tip });
    });
    const end = nodes.get(names.at(-1));
    end.tip = `${p.path}\n${p.hops} hop${p.hops > 1 ? "s" : ""}, link strengths ${p.link_intensities.join(", ")}`;
    for (let k = 1; k < names.length; k++) {
      const key = `${names[k - 1]}\u0000${names[k]}`;
      edges.set(key, Math.max(edges.get(key) ?? 0, p.link_intensities[k - 1]));
    }
  }

  const maxCol = Math.max(0, ...[...nodes.values()].map((n) => n.col));
  const cols = Array.from({ length: maxCol + 1 }, (_, i) => [...nodes.values()].filter((n) => n.col === i));
  const parentRow = new Map([[seed, 0]]);
  const strongestIn = (name) => Math.max(0, ...[...edges].filter(([k]) => k.endsWith(`\u0000${name}`)).map(([, v]) => v));
  const parentOf = (name) => {
    let best = null, bestV = -1;
    for (const [k, v] of edges) {
      const [p, ch] = k.split("\u0000");
      if (ch === name && nodes.get(p)?.col === nodes.get(name).col - 1 && v > bestV) { best = p; bestV = v; }
    }
    return best;
  };
  for (let i = 1; i <= maxCol; i++) {
    cols[i].sort((x, y) => (parentRow.get(parentOf(x.name)) ?? 0) - (parentRow.get(parentOf(y.name)) ?? 0)
      || strongestIn(y.name) - strongestIn(x.name));
    cols[i].forEach((n, r) => parentRow.set(n.name, r));
  }

  const rows = Math.max(1, ...cols.slice(1).map((col) => col.length));
  const height = TOP + rows * ROW + 8;
  const width = COL_X[Math.max(maxCol, 1)] + 230;
  const xy = new Map();
  cols.forEach((col, i) => col.forEach((n, r) => {
    const y = i === 0 ? TOP + (rows * ROW) / 2 : TOP + ((rows - col.length) * ROW) / 2 + r * ROW + ROW / 2;
    xy.set(n.name, [COL_X[i], y]);
  }));

  const color = st === "not_funded" ? "var(--stone)" : "var(--line)";
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Cascade from ${seed}: ${c.exposed_count} businesses within ${c.depth} hops` });
  ["Applicant", "1 hop", "2 hops", "3 hops"].slice(0, maxCol + 1)
    .forEach((t, i) => svg.append(s("text", { class: "lm-col", x: COL_X[i] - 6, y: 14 }, t)));

  for (const [key, v] of edges) {
    const [p, ch] = key.split("\u0000");
    if (!xy.has(p) || !xy.has(ch)) continue;
    const [x1, y1] = xy.get(p);
    const [x2, y2] = xy.get(ch);
    const w = 2 + 9 * Math.sqrt(Math.min(v, 0.21) / 0.21);
    svg.append(s("path", { class: "lm-edge", d: track(x1, y1, x2, y2), stroke: color,
      "stroke-width": w.toFixed(1), style: `--delay: ${(nodes.get(ch).col - 1) * 0.35}s` }));
  }
  for (const n of nodes.values()) {
    const [x, y] = xy.get(n.name);
    const isSeed = n.col === 0;
    const g = s("g", { class: `lm-stop${isSeed ? " seed" : ""}`, style: `--delay: ${isSeed ? 0 : (n.col - 1) * 0.35 + 0.3}s` });
    g.append(s("title", {}, n.tip || n.name));
    g.append(s("circle", { cx: x, cy: y, r: isSeed ? 11 : 7, stroke: isSeed ? "var(--ink)" : color }));
    g.append(s("text", { class: "lm-label", x: x - 6, y: y - (isSeed ? 18 : 13) }, shortName(n.name)));
    svg.append(g);
  }

  const hops = Object.entries(c.by_hop).map(([k, v]) => `${v} at ${k} hop${k > 1 ? "s" : ""}`).join(", ");
  const shown = c.paths.length;
  const caption = c.is_leaf
    ? `No qualifying downstream path from ${seed} at this depth and cutoff. The case rests on its own survival.`
    : `${c.exposed_count} businesses downstream (${hops}). Showing the ${shown} strongest paths; ` +
      `thicker lines are stronger links. ${c.local_alternatives_same_industry} other local businesses share its industry.`;
  return h("figure", { class: "line-map" }, svg, h("figcaption", {}, caption));
}

function animateLines(root) {
  root.querySelectorAll(".lm-edge").forEach((p) => p.style.setProperty("--len", p.getTotalLength().toFixed(1)));
}

/* Equity audit: renders audit_allocation(). Industry and tract poverty only, never race. */
const SECTOR_COLS = [["corridor_share", "Corridor", "corridor"], ["applicant_share", "Applicants", "applicants"],
                     ["money_share", "Money", "money"]];

function renderAuditStrip() {
  const strip = document.getElementById("audit-strip");
  strip.hidden = false;
  const a = state.audit;
  if (a.error) {
    strip.replaceChildren(h("strong", {}, "Equity check"), h("span", { class: "muted" }, `Not available: ${a.error}`));
    return;
  }
  strip.replaceChildren(h("strong", {}, "Equity check"), h("span", {}, a.headline),
    h("button", { type: "button", onclick: showAudit }, "Open the audit"));
}

function shareCell(v, max, kind) {
  return h("td", {}, h("div", { class: `share ${kind}` },
    h("span", { class: "share-bar", "aria-hidden": "true" },
      h("span", { style: `width: ${max ? (100 * v) / max : 0}%` })),
    h("span", { class: "share-num" }, pct(v))));
}

function industryTable(rows) {
  const shown = rows.filter((r) => r.corridor_share || r.applicant_share || r.money_share);
  const max = Math.max(...shown.flatMap((r) => SECTOR_COLS.map(([k]) => r[k])));
  return h("div", { class: "table-scroll" }, h("table", { class: "audit-table" },
    h("caption", {}, "Each sector's share of corridor businesses, of applicants, and of the money"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Sector"),
      ...SECTOR_COLS.map(([, label]) => h("th", { scope: "col" }, label)), h("th", { scope: "col" }, "Flag"))),
    h("tbody", {}, ...shown.map((r) => h("tr", { class: r.flag ? "flagged" : "" },
      h("th", { scope: "row" }, r.sector),
      ...SECTOR_COLS.map(([k, , kind]) => shareCell(r[k], max, kind)),
      h("td", {}, r.flag ? [h("span", { class: `tag ${r.flag}` },
                              `${r.flag} · ${r.money_vs_corridor != null ? `${r.money_vs_corridor}x` : "small sector"}`),
                            r.edge_generator_bias ? h("span", { class: "tag bias" }, "edge-generator bias") : null,
                            h("span", { class: "cause" }, r.likely_cause)] : "")))))) ;
}

function needLines(need) {
  const fmt = (x) => (x == null ? "n/a" : pct(x));
  const line = (label, n) => h("p", {}, h("strong", {}, `${label}: `),
    `${fmt(n.funded_dollar_weighted)} where the money lands (dollar-weighted), ` +
    `${fmt(n.applicant_avg)} across applicants, ${fmt(n.corridor_avg)} corridor average.`);
  return [line("Residents below poverty", need.poverty_rate), line("Households without a car", need.no_vehicle_rate),
          h("p", { class: "fine" }, "ACS 2019 five-year estimates, by census tract.")];
}

function modeledBlock(m) {
  const share = m.plan_cascade_share_dollar_weighted;
  const list = m.cascade_driven_awards;
  return [
    h("p", {}, share == null ? "No funded awards." :
      `${pct(share)} of the plan's value (dollar-weighted) comes from the supply cascades grants protect, ` +
      "which rest on modeled links."),
    list.length
      ? h("ul", { class: "reason-list driven-list" }, ...list.map((d) => h("li", {},
          h("button", { type: "button", onclick: () => select(d.business_id) }, d.name),
          ` · ${money.format(d.award)} · ${pct(d.cascade_share)} of its value from the cascade`)))
      : h("p", { class: "fine" }, "No award rests mainly on modeled links."),
  ];
}

function renderAudit(a) {
  const t = a.thresholds;
  return [
    h("p", { class: "case-status audit" }, "Equity audit of this plan"),
    h("h2", {}, "Where the money goes"),
    h("p", { class: "audit-headline" }, a.headline),
    a.source === "sample" ? h("p", { class: "fine" }, `Sample run captured ${a.captured}.`) : null,
    reason("In the memo", h("ul", { class: "reason-list memo-lines" }, ...a.memo_lines.map((t) => h("li", {}, t)))),
    industryTable(a.industry),
    h("div", { class: "reasons" },
      reason("Findings", h("ul", { class: "reason-list" }, ...a.findings.map((f) => h("li", {}, f)))),
      reason("Who the money reaches", ...needLines(a.need)),
      reason("Resting on modeled links", ...modeledBlock(a.modeled_evidence)),
      reason("Read this with care",
        h("ul", { class: "reason-list fine" }, ...a.caveats.map((c) => h("li", {}, c))),
        h("p", { class: "fine" }, `Flags: at least ${t.over}x or at most ${t.under}x a sector's corridor share ` +
          `(sectors with at least ${pct(t.min_sector_share)} of corridor businesses), or money at least ` +
          `${Math.round(t.over_gap * 100)} points above corridor share at any size.`))),
  ];
}

function showAudit() {
  state.selected = null;
  document.querySelectorAll(".queue-item button").forEach((b) => b.setAttribute("aria-current", "false"));
  const pane = document.getElementById("case");
  pane.replaceChildren(...renderAudit(state.audit));
  pane.querySelector("h2").scrollIntoView({ block: "nearest" });
}

function modeledTag(a) {
  const d = state.audit?.modeled_evidence?.cascade_driven_awards?.find((x) => x.business_id === a.business_id);
  return d ? h("span", { class: "tag modeled" }, `${pct(d.cascade_share)} of value from modeled links`) : null;
}

/* Ask the agent */

// The agent answers in Markdown (tables, lists, bold). Render it, sanitized because it
// is model output. The libraries load on first use; if the CDN is unreachable the
// answer still shows, as plain text.
let markdownLibs = null;
async function renderMarkdown(target, text) {
  try {
    markdownLibs ??= Promise.all([
      import("https://cdn.jsdelivr.net/npm/marked@15.0.12/lib/marked.esm.js"),
      import("https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.es.mjs"),
    ]);
    const [{ marked }, { default: DOMPurify }] = await markdownLibs;
    target.innerHTML = DOMPurify.sanitize(marked.parse(text, { gfm: true }));
    target.querySelectorAll("table").forEach((t) => t.replaceWith(h("div", { class: "table-scroll" }, t.cloneNode(true))));
  } catch {
    markdownLibs = null;
    target.textContent = text;
  }
}

function askForm(a, st) {
  const reply = h("div", { class: "ask-reply", "aria-live": "polite" });
  const prompt = st === "not_funded" ? `Why isn't ${a.name} funded?` : `Why fund ${a.name} and not the runner-up?`;
  const input = h("input", { id: "ask-input", name: "question", type: "text", autocomplete: "off", placeholder: prompt });
  const form = h("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      const question = input.value.trim() || prompt;
      reply.className = "ask-reply pending";
      reply.textContent = "Asking the agent… this can take up to 30 seconds.";
      try {
        const res = await getJSON("/api/ask", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, business_id: a.business_id }),
        });
        reply.className = "ask-reply answer";
        await renderMarkdown(reply, res.answer);
      } catch (e) {
        reply.className = "ask-reply error";
        reply.textContent = e.message;
      }
    },
  }, input, h("button", { type: "submit" }, "Ask the agent"));
  return h("section", { class: "ask" }, h("label", { for: "ask-input" }, "Ask the agent about this case"), form, reply);
}

async function select(id) {
  state.selected = id;
  document.querySelectorAll(".queue-item button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === id)));
  const a = state.order.find((x) => x.business_id === id);
  const pane = document.getElementById("case");
  pane.replaceChildren(...caseHeader(a), h("p", { class: "case-empty" }, "Tracing the cascade in the graph…"));
  try {
    const c = await getJSON(`/api/cascade/${encodeURIComponent(id)}`);
    if (state.selected !== id) return;
    pane.replaceChildren(...renderCase(a, c));
    animateLines(pane);
  } catch (e) {
    if (state.selected !== id) return;
    pane.replaceChildren(...caseHeader(a),
      h("p", { class: "case-error" }, `Couldn't trace this cascade: ${e.message}. Check the grant desk server log.`));
  }
}

async function init() {
  // The audit loads alongside the plan; if it fails, the desk still works without it.
  const audit = getJSON("/api/audit").catch((e) => ({ error: e.message }));
  try {
    state.rec = await getJSON("/api/recommendation");
  } catch (e) {
    document.getElementById("desk-sub").textContent = "";
    document.getElementById("case").replaceChildren(
      h("p", { class: "case-error" }, `Couldn't load the recommendation: ${e.message}. Check the grant desk server log.`));
    return;
  }
  renderDesk();
  renderQueue();
  if (state.order.length) select(state.order[0].business_id);
  state.audit = await audit;
  renderAuditStrip();
  if (location.hash === "#audit" && !state.audit.error) showAudit();  // deep link for the demo
  else if (state.selected) select(state.selected);  // redraw the open case with its modeled-links tag
}

init();
