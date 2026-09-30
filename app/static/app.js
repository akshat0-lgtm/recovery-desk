/* Recovery Desk frontend: home (what needs you + pipeline), case page (stages 0-7 + live agent feed). */
(() => {
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]));
const money = n => n == null ? "—" : "$" + Number(n).toLocaleString("en-US", {maximumFractionDigits: 0});
const money2 = n => n == null ? "—" : "$" + Number(n).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
};
const post = (path, body) => api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});

const STATUS = {
  received: ["Received", "s-run"], extracting: ["Reading file", "s-run"], detecting: ["Detecting", "s-run"],
  validating: ["Validating", "s-run"], building: ["Building demand", "s-run"], agent_working: ["Agent working", "s-run"],
  awaiting_approval: ["Approve demand", "s-wait"], with_carrier: ["With carrier", "s-info"], needs_you: ["Needs you", "s-wait"],
  awaiting_payment: ["Payment due", "s-info"], in_arbitration: ["In arbitration", "s-info"], recovered: ["Recovered", "s-ok"],
  with_counsel: ["With counsel", "s-park"], parked: ["Parked", "s-park"], needs_review: ["Needs review", "s-wait"],
  failed: ["Failed", "s-bad"], rejected: ["Rejected", "s-bad"], closed: ["Closed", "s-park"],
};
const RUNNING = new Set(["received", "extracting", "detecting", "validating", "building", "agent_working"]);
const pill = st => { const [l, k] = STATUS[st] || [st, "s-info"]; return `<span class="pill ${k}">${esc(l)}</span>`; };
const KIND = {settle: "Settle?", arbitration: "Requires arbitration", litigation: "Requires litigation", request: "Carrier needs something", approve: "Approve demand", check: "Check"};

let state = {view: "home", sel: null, home: null, detail: null, events: [], es: null, cfg: null};

function toast(m) { const t = $("#toast"); t.textContent = m; t.hidden = false; clearTimeout(t._t); t._t = setTimeout(() => t.hidden = true, 3000); }

/* ================================================================ HOME */
async function loadHome() {
  const h = state.home = await api("/api/home");
  $("#deskDate").textContent = h.settings.desk_date;
  $("#advancing").hidden = !h.advancing;
  document.querySelectorAll("[data-clock]").forEach(b => b.disabled = h.advancing);
  if (document.activeElement !== $("#settle")) { $("#settle").value = h.settings.settle_pct; $("#settleVal").textContent = h.settings.settle_pct + "%"; }
  if (document.activeElement !== $("#autosend")) $("#autosend").value = h.settings.auto_send_limit;
  if (state.view !== "home") return;
  const c = h.counts;
  $("#kpis").innerHTML = `
    <div class="kpi hl"><span class="label">Recovered</span><b class="num">${money(h.totals.recovered)}</b><span class="sub">${c.recovered} files paid</span></div>
    <div class="kpi"><span class="label">In play</span><b class="num">${money(h.totals.in_play)}</b><span class="sub">demands out, awards due</span></div>
    <div class="kpi ${h.needs_you.length ? "warn" : ""}"><span class="label">Needs you</span><b class="num">${h.needs_you.length}</b><span class="sub">decisions and requests</span></div>
    <div class="kpi"><span class="label">Agent working</span><b class="num">${c.reading + h.pipeline.filter(r => r.status === "agent_working").length}</b><span class="sub">reading or replying</span></div>
    <div class="kpi"><span class="label">Files</span><b class="num">${h.totals.files}</b><span class="sub">${c.closed} parked or closed</span></div>`;
  renderNeeds(h.needs_you);
  renderBoard(h.pipeline);
  $("#payments").innerHTML = h.payments.length ? `<table><thead><tr><th>Received</th><th>Payer</th><th>Reference</th><th class="r">Amount</th><th>Match</th></tr></thead><tbody>
    ${h.payments.map(p => `<tr><td class="num">${esc(p.date)}</td><td>${esc(p.payer)}</td><td class="mono">${esc(p.reference)}</td><td class="r num">${money2(p.amount)}</td><td>${p.status === "matched" ? `<span class="okmark">matched</span>` : p.status === "short" ? `<span class="bad">short, agent chasing</span>` : `<span class="sub">unmatched</span>`}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty" style="padding:12px">No payments yet. Payments arrive after a carrier settles; move the clock forward.</div>`;
}

function renderNeeds(list) {
  if (!list.length) { $("#needs").innerHTML = `<div class="allclear">Nothing needs you right now. The agent is handling everything in the pipeline.</div>`; return; }
  $("#needs").innerHTML = list.map(r => {
    const a = r.action || {kind: "check", text: r.status === "failed" ? "The agent could not finish." : "Needs review."};
    const k = r.status === "failed" ? "check" : a.kind;
    let acts = "";
    if (k === "settle") acts = `<button class="btn primary sm" data-do="accept">Accept ${money(a.amount)}</button><button class="btn sm" data-do="decline">Decline, go to ${esc(a.alt || "arbitration")}</button>`;
    if (k === "arbitration") acts = `<a class="btn sm" href="/api/cases/${r.id}/files/arbitration" target="_blank" rel="noopener">Review filing</a><button class="btn primary sm" data-do="file">File arbitration</button>${a.last_offer ? `<button class="btn sm" data-do="settle_offer">Settle at ${money(a.last_offer)}</button>` : ""}<button class="btn sm" data-do="close">Close</button>`;
    if (k === "litigation") acts = `<a class="btn sm" href="/api/cases/${r.id}/files/litigation" target="_blank" rel="noopener">Review memo</a><button class="btn primary sm" data-do="refer">Refer to counsel</button>${a.last_offer ? `<button class="btn sm" data-do="settle_offer">Settle at ${money(a.last_offer)}</button>` : ""}<button class="btn sm" data-do="close">Close</button>`;
    if (k === "request") acts = `<input type="text" class="note" placeholder="Note to send with it (optional)"><input type="file" class="file"><div class="acts"><button class="btn primary sm" data-do="provide">Send to carrier</button><button class="btn sm" data-do="cannot_provide">I can't get this</button></div>`;
    if (k === "approve") acts = `<a class="btn sm" href="/api/cases/${r.id}/files/letter" target="_blank" rel="noopener">Read demand</a><button class="btn primary sm" data-do="send">Send demand</button><button class="btn sm" data-do="reject">Reject</button>`;
    if (k === "check") acts = r.status === "failed" ? `<button class="btn primary sm" data-do="retry">Retry</button><button class="btn sm" data-do="close">Close</button>` : `<a class="btn sm" href="/cases/${r.id}" data-nav="case" data-id="${r.id}">Open file</a>`;
    return `<article class="need k-${k}" data-id="${r.id}">
      <div class="t"><span class="kind">${KIND[k]}</span><a class="mono sub" href="/cases/${r.id}" data-nav="case" data-id="${r.id}">${esc(r.claim_no || "file")} ›</a></div>
      <b>${esc(r.title || "")}</b>
      <p>${esc(a.text || "")}</p>
      ${r.demand ? `<span class="sub">Demand ${money(r.demand)}${r.carrier ? ` · ${esc(r.carrier)}` : ""}</span>` : ""}
      <div class="acts">${acts}</div></article>`;
  }).join("");
}

function renderBoard(rows) {
  const cols = [
    ["Agent reading", r => ["received", "extracting", "detecting", "validating", "building"].includes(r.status) || (r.status === "agent_working" && r.stage <= 3)],
    ["Negotiating", r => r.status === "with_carrier" || (r.status === "agent_working" && r.stage >= 4)],
    ["Payment due", r => r.status === "awaiting_payment"],
    ["In arbitration", r => r.status === "in_arbitration"],
    ["Recovered", r => r.status === "recovered"],
    ["Parked or closed", r => ["parked", "closed", "with_counsel", "rejected"].includes(r.status)],
  ];
  $("#board").innerHTML = cols.map(([name, fn]) => { const list = rows.filter(fn); return `<div class="col"><h3><span>${name}</span><span class="num">${list.length}</span></h3>
    ${list.map(r => `<a class="card" href="/cases/${r.id}" data-nav="case" data-id="${r.id}"><div class="t"><span class="mono">${esc(r.claim_no || "new file")}</span><span class="num">${r.recovered ? money(r.recovered) : r.demand ? money(r.demand) : "—"}</span></div><div class="d">${esc(r.title || "")}</div><div class="d">${RUNNING.has(r.status) ? "Agent working…" : esc(r.carrier || "")}</div></a>`).join("") || `<div class="empty">None</div>`}</div>`; }).join("");
}

/* ================================================================ CASE */
async function openCase(id, push = true) {
  state.view = "case"; state.sel = id; state.events = [];
  if (push) history.pushState({}, "", `/cases/${id}`);
  $("#homeView").hidden = true; $("#caseView").hidden = false;
  if (state.es) state.es.close();
  await refreshDetail();
  state.es = new EventSource(`/api/cases/${id}/stream`);
  state.es.addEventListener("agent", e => {
    const ev = JSON.parse(e.data);
    if (state.events.some(x => x.id === ev.id)) return;
    state.events.push(ev); appendFeed(ev);
    if (["status", "human", "error", "carrier"].includes(ev.kind) || ev.name === "run_finished") scheduleRefresh();
  });
}
function goHome(push = true) {
  state.view = "home"; state.sel = null; if (state.es) state.es.close();
  if (push) history.pushState({}, "", "/");
  $("#caseView").hidden = true; $("#homeView").hidden = false; loadHome();
}
let refreshT;
function scheduleRefresh() { clearTimeout(refreshT); refreshT = setTimeout(refreshDetail, 300); }
async function refreshDetail() { if (!state.sel) return; state.detail = await api(`/api/cases/${state.sel}`); renderCase(); }

const STAGES = ["Intake", "Detect", "Validate", "Build demand", "Send", "Negotiate", "Arbitrate / litigate", "Collect"];
function stageState(d, n) {
  const st = d.status, cur = d.stage;
  if (st === "recovered") return n === 6 && !d.escalation ? ["", "Not needed"] : ["done", "Done"];
  if (["parked", "closed", "rejected"].includes(st)) return n < cur ? ["done", "Done"] : n === cur ? ["stop", STATUS[st][0]] : ["", "—"];
  if (st === "failed" && n === cur) return ["fail", "Failed"];
  if (n < cur) return n === 6 ? ["", "Not needed"] : ["done", "Done"];
  if (n === cur) {
    if (RUNNING.has(st)) return ["active", "Agent working"];
    if (["needs_you", "awaiting_approval", "needs_review"].includes(st)) return ["stop", "Needs you"];
    return ["active", STATUS[st]?.[0] || st];
  }
  return ["", ""];
}

function actionCard(d) {
  const a = d.action; if (!a && d.status !== "failed") return "";
  const r = {id: d.id, action: a, status: d.status, claim_no: d.claim_no, title: d.title, demand: d.demand, carrier: d.carrier};
  const tmp = document.createElement("div"); const keep = $("#needs").innerHTML; renderNeeds([r]); tmp.innerHTML = $("#needs").innerHTML; $("#needs").innerHTML = keep;
  return `<div class="actioncard">${tmp.innerHTML}</div>`;
}

function renderCase() {
  const d = state.detail; if (!d) return;
  const ex = d.extraction, det = d.detection, dec = d.decision, dem = d.demand, esc6 = d.escalation, rec = d.recovery;
  const running = RUNNING.has(d.status);
  const rail = STAGES.map((name, n) => { const [k, t] = stageState(d, n); return `<div class="step ${k}"><span class="n">${n}</span><b>${name}</b><span class="st">${esc(t)}</span></div>`; }).join("");
  let h = `<div><a href="/" data-nav="home" class="sub">‹ Back to desk</a></div>
    <div class="casehead"><div><span class="label">${esc(d.model || "")}</span><h2 class="mono">${esc(d.claim_no || "Reading file…")}</h2><span class="sub">${esc(d.title || "")}</span></div>
    <div class="actions">${pill(d.status)}
      ${d.stage <= 3 ? `<select id="rerunFrom" aria-label="Rerun from stage" ${running ? "disabled" : ""}><option value="0">Rerun from intake</option><option value="1">from detect</option><option value="2">from validate</option><option value="3">from build</option></select><button class="btn sm" data-act="rerun" ${running ? "disabled" : ""}>Rerun</button>` : ""}
      <a class="btn sm" href="/api/cases/${d.id}/files/upload" target="_blank" rel="noopener">Original PDF</a>
      <button class="btn sm danger" data-act="delete" ${running ? "disabled" : ""}>Delete</button></div></div>
    <div class="rail">${rail}</div>${actionCard(d)}
    <div class="work"><div class="panels">`;
  if (d.error) h += `<div class="panel"><div class="body"><div class="verdict v-route">${esc(d.error)}</div></div></div>`;

  // Stage 7 and 5-6 first when present: the latest state is what matters
  if (rec?.recovered) {
    h += `<section class="panel"><header><h3><span class="stagechip">7</span>Collected and reconciled</h3></header><div class="body">
      <div class="tiles num"><div class="tile"><span class="label">Recovered</span><b>${money2(rec.recovered)}</b><span class="sub">on ${esc(rec.recovered_on)}</span></div>
      <div class="tile"><span class="label">Deductible refund</span><b>${money2(rec.deductible_refund)}</b><span class="sub">queued to finance</span></div>
      <div class="tile"><span class="label">Cycle time</span><b>${rec.cycle_days} days</b><span class="sub">demand to cash</span></div>
      <div class="tile"><span class="label">Payments</span><b>${(rec.payments || []).length}</b><span class="sub">matched from the bank feed</span></div></div>
      <div class="sub">Refund rule: ${esc(rec.refund_rule)}. Booking and refund go through the payments adapter (stub in v1).</div></div></section>`;
  }
  if (esc6) {
    const arb = esc6.type === "arbitration";
    h += `<section class="panel"><header><h3><span class="stagechip">6</span>${arb ? "Arbitration filing" : "Litigation referral"}</h3><a class="btn sm" href="/api/cases/${d.id}/files/${esc6.type}" target="_blank" rel="noopener">Open PDF</a></header><div class="body">
      <div class="verdict v-route">${esc(esc6.reason)}</div>
      <div><span class="label">${arb ? "Contentions" : "Summary"}, drafted by the agent</span><p style="margin:4px 0 0">${esc(arb ? esc6.contentions : esc6.summary)}</p></div>
      ${!arb ? `<div><span class="label">Why litigation</span><p style="margin:4px 0 0">${esc(esc6.why_litigation)}</p></div>` : ""}
      <div class="tablewrap"><table><thead><tr><th>Exhibit</th><th>Pages</th><th>What it proves</th></tr></thead><tbody>${esc6.evidence_index.map(e => `<tr><td>${esc(e.label)}</td><td>${e.pages.map(p => `<span class="pg" data-page="${p}">${p}</span>`).join(" ")}</td><td>${esc(e.proves)}</td></tr>`).join("")}</tbody></table></div>
      ${arb && esc6.anticipated_defenses?.length ? `<div><span class="label">Anticipated defenses</span><ul style="margin:4px 0 0;padding-left:18px">${esc6.anticipated_defenses.map(x => `<li><b>${esc(x.defense)}</b> ${esc(x.response)}</li>`).join("")}</ul></div>` : ""}
      ${!arb && esc6.risks?.length ? `<div><span class="label">Risks for counsel</span><ul style="margin:4px 0 0;padding-left:18px">${esc6.risks.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
      ${esc6.award != null ? `<div class="verdict v-pursue">Award ${money2(esc6.award)}. ${esc(esc6.decision_text)}</div>` : ""}
      <div class="sub">Deadline to sue: ${esc(esc6.deadline.expires)} (${esc6.deadline.days_left} days left when prepared).</div></div></section>`;
  }
  if ((d.messages || []).length) {
    const n = d.neg || {};
    h += `<section class="panel"><header><h3><span class="stagechip">4–5</span>Correspondence</h3><span class="sub">${esc(d.channel === "hub" ? "E-Subro Hub (simulated)" : "Email (simulated)")} · demand ${money2(n.demand)}${n.agreed ? ` · agreed ${money2(n.agreed)}` : ""} · threshold ${d.settle_pct}%</span></header><div class="body">
      <ul class="thread">${d.messages.map(m => `<li class="${m.dir === "in" ? "in" : "out"}"><span class="meta">${esc(m.date)} · ${m.dir === "in" ? "Carrier" : "Agent"} · ${esc(m.kind.replace(/_/g, " "))}${m.amount ? ` · ${money2(m.amount)}` : ""}</span>${esc(m.text)}</li>`).join("")}</ul></div></section>`;
  }

  // Stage 0
  h += `<section class="panel" id="p0"><header><h3><span class="stagechip">0</span>Intake: what the file says</h3>${ex ? `<span class="sub">${d.pages.length} pages read</span>` : ""}</header><div class="body">`;
  if (d.pages.length) h += `<div><span class="label">Pages, labelled by the agent</span><div class="pages" style="margin-top:6px">${d.pages.map(p => `<span class="pagechip" data-page="${p.page_no}"><span class="pg">p${p.page_no}</span>${esc((p.doc_type || "…").replace(/_/g, " "))}</span>`).join("")}</div></div>`;
  if (ex) {
    const cite = f => { const c = (ex.citations || []).find(x => x.field === f); return c ? ` <span class="pg" data-page="${c.page}" title="${esc(c.quote)}">p${c.page}</span>${c.verified ? "" : ` <span class="bad">quote not found</span>`}` : ""; };
    const rows = [["Claim number", ex.claim_number, "claim_number"], ["Insured", ex.insured_name, "insured_name"], ["Date of loss", ex.loss_date, "loss_date"],
      ["Loss state", ex.loss_state, "loss_state"], ["What happened", ex.loss_description, "loss_description"], ["Other party", ex.other_party_name, "other_party_name"],
      ["Other carrier", ex.other_party_carrier, "other_party_carrier"], ["Their claim no.", ex.other_party_claim_number, "other_party_claim_number"],
      ["Police report", ex.police_report_number, "police_report_number"],
      ["Adjuster fault call", ex.adjuster_fault_insured_pct == null ? "Not stated" : `Insured ${ex.adjuster_fault_insured_pct}%`, "adjuster_fault_insured_pct"],
      ["Deductible", money2(ex.deductible), "deductible"]];
    h += `<div class="tablewrap"><table><tbody>${rows.map(([k, v, f]) => `<tr><td class="muted" style="width:160px">${k}</td><td>${esc(v ?? "—")}${cite(f)}</td></tr>`).join("")}</tbody></table></div>`;
    h += `<div class="tablewrap"><table><thead><tr><th>Paid by Northgate</th><th>Category</th><th>Page</th><th class="r">Amount</th></tr></thead><tbody>
      ${ex.line_items.map(i => `<tr><td>${esc(i.description)}</td><td class="muted">${esc(i.category.replace(/_/g, " "))}</td><td>${i.page ? `<span class="pg" data-page="${i.page}">p${i.page}</span>` : ""}</td><td class="r num">${money2(i.amount)}</td></tr>`).join("")}
      <tr><td colspan="3"><b>Company paid</b>${ex.ledger_total_paid != null ? ` <span class="okmark">matches ledger total</span>` : ""}</td><td class="r num"><b>${money2(ex.company_paid)}</b></td></tr></tbody></table></div>`;
  } else h += `<span class="sub">${running ? "The agent is reading the file…" : "No extraction yet."}</span>`;
  if (d.review?.length) h += `<ul class="warnlist">${d.review.map(r => `<li>${esc(r)}</li>`).join("")}</ul>`;
  h += `</div></section>`;

  if (det) {
    h += `<section class="panel"><header><h3><span class="stagechip">1</span>Detect</h3></header><div class="body">
      <div class="verdict ${det.third_party_liable ? "v-pursue" : "v-park"}">${det.third_party_liable ? `Liable: ${esc(det.liable_party)}${det.liable_party_carrier ? `, insured by ${esc(det.liable_party_carrier)}` : ""}` : `No liable third party. ${esc(det.reason || "")}`}</div>
      ${det.basis ? `<p style="margin:0">${esc(det.basis)}</p>` : ""}
      ${det.evidence?.length ? `<ul class="ev">${det.evidence.map(e => `<li><span>${esc(e.finding)} ${e.page ? `<span class="pg" data-page="${e.page}">p${e.page}</span>` : ""} ${e.verified ? `<span class="okmark">found in file</span>` : `<span class="bad">not found</span>`}</span><q>${esc(e.quote)}</q></li>`).join("")}</ul>` : ""}</div></section>`;
  }
  if (dec) {
    const label = {pursue: "Pursue", park: "Park", litigation: "Litigation"}[dec.decision] || dec.decision;
    h += `<section class="panel"><header><h3><span class="stagechip">2</span>Validate</h3></header><div class="body"><div class="verdict v-${dec.decision === "pursue" ? "pursue" : dec.decision === "park" ? "park" : "route"}">${label}: ${esc(dec.reason)}</div>`;
    if (dec.money) {
      const m = dec.money, dl = dec.deadline;
      h += `<div class="tiles num"><div class="tile"><span class="label">Total damages</span><b>${money(m.total_damages)}</b><span class="sub">paid + deductible</span></div>
        <div class="tile"><span class="label">Recoverable</span><b>${m.recoverable_share_pct}%</b><span class="sub">insured fault ${dec.insured_fault_pct}%</span></div>
        <div class="tile"><span class="label">Likelihood</span><b>${dec.likelihood_pct}%</b><span class="sub">agent's estimate</span></div>
        <div class="tile"><span class="label">Demand</span><b>${money(m.demand)}</b><span class="sub">expected ${money(m.expected_recovery)}</span></div></div>
        <div class="two"><div><span class="label">Deadline</span><div>${esc(dl.expires)} · ${dl.days_left} days left</div></div>
        <div><span class="label">Other carrier</span><div>${dec.carrier ? `${esc(dec.carrier.name)} · ${dec.carrier.af_member ? "AF member" : "not an AF member"}` : "None on file"}</div><div class="sub">${esc(dec.arbitration_note || "")}</div></div></div>`;
      if (dec.weaknesses?.length) h += `<div><span class="label">What the other side will argue</span><ul style="margin:4px 0 0;padding-left:18px">${dec.weaknesses.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
    }
    const guards = state.events.filter(e => e.kind === "guard" && e.stage === 2).length;
    if (guards) h += `<div class="sub">Guardrails blocked the agent ${guards} time${guards > 1 ? "s" : ""} before this was accepted.</div>`;
    h += `</div></section>`;
  }
  if (dem) {
    h += `<section class="panel"><header><h3><span class="stagechip">3</span>Demand package</h3><span class="actions"><a class="btn sm" href="/api/cases/${d.id}/files/letter" target="_blank" rel="noopener">Letter</a><a class="btn sm" href="/api/cases/${d.id}/files/bundle" target="_blank" rel="noopener">Evidence bundle</a></span></header><div class="body">
      <div><span class="label">Fault argument, written by the agent</span><p style="margin:4px 0 0">${esc(dem.liability_argument)}</p></div>
      <div class="two"><div><span class="label">Exhibits</span><ul>${dem.exhibits.map(e => `<li>${esc(e.label)} · ${e.pages.map(p => `<span class="pg" data-page="${p}">${p}</span>`).join(" ")}</li>`).join("")}</ul></div>
      <div><span class="label">Documents to obtain</span><ul>${(dem.documents_to_obtain || []).map(x => `<li>${esc(x)}</li>`).join("") || "<li>None</li>"}</ul></div></div>
      <details><summary class="label" style="cursor:pointer">E-Subro Hub demand fields</summary><pre class="json">${esc(JSON.stringify(dem.hub_payload, null, 2))}</pre></details></div></section>`;
  }
  h += `</div><aside class="panel feed"><header><h3>Agent activity</h3><span class="sub">live</span></header><div class="body" id="feed"></div></aside></div>`;
  $("#caseView").innerHTML = h;
  const feed = $("#feed"); state.events.forEach(ev => feed.appendChild(feedEl(ev))); feed.scrollTop = feed.scrollHeight;
}

/* ================================================================ feed */
const STAGE_NAMES = ["Intake", "Detect", "Validate", "Build", "Send", "Negotiate", "Escalate", "Collect"];
const short = (v, n = 240) => { const s = typeof v === "string" ? v : JSON.stringify(v); return s && s.length > n ? s.slice(0, n) + "…" : (s || ""); };
function feedEl(ev) {
  const el = document.createElement("div");
  el.className = `fe k-${ev.kind}`;
  const time = new Date(ev.ts).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  const p = ev.payload || {};
  const tag = {model: "Model", tool: "Tool", guard: "Guardrail", status: STAGE_NAMES[ev.stage] || "Status", human: "You", error: "Error", carrier: "Carrier", system: "System"}[ev.kind] || ev.kind;
  let body = "";
  if (ev.kind === "model" && ev.name === "says") body = `<div>${esc(p.text)}</div>`;
  else if (ev.kind === "model") body = `<div class="args">${ev.name === "run_started" ? `${esc(p.provider)} · ${esc(p.model)}` : `finished in ${p.rounds} round${p.rounds > 1 ? "s" : ""}`}</div>`;
  else if (ev.kind === "carrier" || (ev.kind === "tool" && p.text)) body = `<div>${esc(short(p.text, 420))}</div>`;
  else if (ev.kind === "tool" || ev.kind === "guard") body = `<div class="args">(${esc(short(p.args || {}, 200))})</div><div class="res">${esc(short(p.result, 420))}</div>`;
  else if (ev.kind === "system") body = `<div>${esc(p.text || (p.amount ? `${p.payer}: ${money2(p.amount)} (${p.source})` : short(p)))}</div>`;
  else body = p.note || p.message ? `<div>${esc(p.note || p.message)}</div>` : "";
  const title = ev.kind === "status" ? (STATUS[ev.name]?.[0] || ev.name) : ev.kind === "model" && ev.name === "says" ? "" : ev.name.replace(/_/g, " ");
  el.innerHTML = `<div class="h"><span class="tag">${esc(tag)}</span><span class="tn">${esc(title)}</span><span class="time">${p.date ? esc(p.date) + " · " : ""}${time}</span></div>${body}`;
  return el;
}
function appendFeed(ev) { const f = $("#feed"); if (!f) return; const near = f.scrollHeight - f.scrollTop - f.clientHeight < 80; f.appendChild(feedEl(ev)); if (near) f.scrollTop = f.scrollHeight; }

/* ================================================================ actions */
async function upload(f) {
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  try { const r = await api("/api/cases", {method: "POST", body: fd}); toast("Uploaded. The agent is reading it."); openCase(r.id); } catch (e) { toast(e.message); }
}
document.addEventListener("click", async e => {
  const nav = e.target.closest("[data-nav]");
  if (nav && !e.metaKey && !e.ctrlKey) { e.preventDefault(); return nav.dataset.nav === "home" ? goHome() : openCase(nav.dataset.id); }
  const pg = e.target.closest("[data-page]"); if (pg) return showPage(+pg.dataset.page);
  const clk = e.target.closest("[data-clock]");
  if (clk) { try { await post(`/api/clock/advance?days=${clk.dataset.clock}`); toast(`Moving the clock ${clk.dataset.clock} days. The agent handles whatever happens.`); loadHome(); } catch (err) { toast(err.message); } return; }
  const d = e.target.closest("[data-do]");
  if (d) {
    const card = d.closest("[data-id]"); const id = card.dataset.id; const verb = d.dataset.do;
    try {
      if (verb === "provide") {
        const fd = new FormData(); fd.append("note", card.querySelector(".note")?.value || ""); const f = card.querySelector(".file")?.files[0]; if (f) fd.append("file", f);
        await api(`/api/cases/${id}/do/provide`, {method: "POST", body: fd});
      } else if (verb === "cannot_provide") {
        await post(`/api/cases/${id}/do/cannot_provide`, {note: card.querySelector(".note")?.value || ""});
      } else await post(`/api/cases/${id}/do/${verb}`);
      toast({accept: "Settlement accepted", decline: "Declined. The agent is preparing the escalation.", file: "Arbitration filed", refer: "Referred to counsel",
             settle_offer: "Settled at the last offer", close: "Closed", provide: "Sent to the carrier", cannot_provide: "The agent will reply without it",
             send: "Demand sent", reject: "Demand rejected", retry: "Retrying"}[verb] || "Done");
      setTimeout(() => state.view === "home" ? loadHome() : refreshDetail(), 500);
    } catch (err) { toast(err.message); }
    return;
  }
  const a = e.target.closest("[data-act]"); if (!a) return;
  const id = state.sel;
  try {
    if (a.dataset.act === "rerun") { await api(`/api/cases/${id}/rerun?from_stage=${$("#rerunFrom").value}`, {method: "POST"}); toast("Rerunning"); scheduleRefresh(); }
    if (a.dataset.act === "delete") {
      if (a.dataset.confirm !== "1") { a.dataset.confirm = "1"; a.textContent = "Click again to delete"; return; }
      await api(`/api/cases/${id}`, {method: "DELETE"}); goHome();
    }
  } catch (err) { toast(err.message); }
});
function showPage(n) {
  const p = state.detail?.pages.find(x => x.page_no === n); if (!p) return;
  $("#modalTitle").textContent = `Page ${n} · ${(p.doc_type || "unlabelled").replace(/_/g, " ")}`;
  $("#modalBody").textContent = p.text || "(No text on this page. Open the original PDF.)";
  $("#modal").hidden = false;
}
$("#modalClose").onclick = () => $("#modal").hidden = true;
$("#modal").onclick = e => { if (e.target.id === "modal") $("#modal").hidden = true; };
document.addEventListener("keydown", e => { if (e.key === "Escape") $("#modal").hidden = true; });
$("#fileInput").onchange = e => upload(e.target.files[0]);
const drop = $("#drop");
["dragover", "dragenter"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => upload(e.dataTransfer.files[0]));
$("#settle").oninput = e => $("#settleVal").textContent = e.target.value + "%";
$("#settle").onchange = async e => { try { await api("/api/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({settle_pct: +e.target.value})}); toast(`The agent now settles at ${e.target.value}% or more`); } catch (err) { toast(err.message); } };
$("#saveAuto").onclick = async () => { try { await api("/api/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({auto_send_limit: +$("#autosend").value})}); toast("Auto-send limit saved"); } catch (err) { toast(err.message); } };
$("#sampleBtn").onclick = async () => { try { const r = await post(`/api/samples/${$("#sampleSel").value}`); openCase(r.id); } catch (e) { toast(e.message); } };
$("#loadAll").onclick = async () => { try { await post("/api/samples/all"); toast("Loaded the demo book. The agent is reading 11 files."); loadHome(); } catch (e) { toast(e.message); } };
$("#resetDemo").onclick = async e => {
  const b = e.target; if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "Click again to clear everything"; return; }
  b.dataset.confirm = ""; b.textContent = "Reset demo";
  try { await post("/api/reset"); toast("Demo reset"); loadHome(); } catch (err) { toast(err.message); }
};
window.addEventListener("popstate", () => { const m = location.pathname.match(/^\/cases\/(\w+)/); m ? openCase(m[1], false) : goHome(false); });

(async () => {
  try {
    state.cfg = await api("/api/config");
    $("#modelInfo").textContent = `Subrogation agent · ${state.cfg.provider}: ${state.cfg.model}`;
    const nice = s => s.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    $("#sampleSel").innerHTML = state.cfg.samples.map(s => `<option value="${s}">${nice(s)}</option>`).join("");
  } catch (e) { toast("Could not reach the server."); }
  const m = location.pathname.match(/^\/cases\/(\w+)/);
  loadHome();
  if (m) openCase(m[1], false);
  setInterval(() => {
    const busy = state.home && (state.home.advancing || state.home.pipeline.some(r => RUNNING.has(r.status)));
    if (busy || state.view === "home") loadHome();
  }, 2500);
})();
})();
