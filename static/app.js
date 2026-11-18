/* SaaSquatch Signal - vanilla JS front end (no build step). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { playbook: "acquisition", jobId: null, leads: [], seen: new Set(), poll: null, sort: "priority" };

const PLAYBOOK_HINTS = {
  acquisition: "Ranks established, owner-operated SMBs with succession & modernization upside.",
  sales: "Ranks reachable SMBs with the biggest tech gap - best fit for selling AI / software.",
};

// ---------------------------------------------------------------- setup panel
async function init() {
  try {
    const h = await (await fetch("/api/health")).json();
    const pill = $("#ai-status");
    pill.textContent = h.ai_enabled ? `AI briefs: ${h.model}` : "AI briefs: heuristic mode";
    pill.classList.toggle("on", h.ai_enabled);
    pill.title = h.ai_enabled ? `${h.provider} writes summaries & openers for top leads` : "Set GROQ_API_KEY or ANTHROPIC_API_KEY in .env to enable AI briefs";
  } catch { /* offline */ }
  loadHistory();
  try {
    const saved = JSON.parse(localStorage.getItem("signal.icp") || "null");
    if (saved) applyIcp(saved);
  } catch { /* storage blocked */ }
}

function applyIcp(icp) {
  setPlaybook(icp.playbook || "acquisition");
  $("#icp-industries").value = (icp.industries || []).join(", ");
  $("#icp-years").value = icp.min_years ?? 10;
  $("#icp-min-emp").value = icp.min_employees ?? 5;
  $("#icp-max-emp").value = icp.max_employees ?? 250;
  $("#icp-states").value = (icp.states || []).join(", ");
}

function readIcp() {
  const list = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
  return {
    playbook: state.playbook,
    industries: list($("#icp-industries").value),
    min_years: +$("#icp-years").value || 0,
    min_employees: +$("#icp-min-emp").value || 0,
    max_employees: +$("#icp-max-emp").value || 10000,
    states: list($("#icp-states").value).map((s) => s.toUpperCase()),
  };
}

function setPlaybook(pb) {
  state.playbook = pb;
  $$(".seg-btn").forEach((b) => {
    const on = b.dataset.playbook === pb;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on);
  });
  $("#playbook-hint").textContent = PLAYBOOK_HINTS[pb];
}

$$(".seg-btn").forEach((b) => b.addEventListener("click", () => setPlaybook(b.dataset.playbook)));

function updateLineCount() {
  const n = $("#leads-input").value.split("\n").filter((l) => l.trim()).length;
  $("#line-count").textContent = n ? `${n} line${n > 1 ? "s" : ""}` : "";
}
$("#leads-input").addEventListener("input", updateLineCount);

async function loadSample() {
  const txt = await (await fetch("/api/sample")).text();
  $("#leads-input").value = txt;
  updateLineCount();
  toast("Sample list loaded - includes duplicates & a dead domain on purpose");
}
$("#sample-btn").addEventListener("click", loadSample);
$("#empty-sample").addEventListener("click", async () => { await loadSample(); run(); });

$("#file-input").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("#leads-input").value = await f.text();
  updateLineCount();
  toast(`Loaded ${f.name}`);
});

// ---------------------------------------------------------------- run a job
async function run() {
  const leads = $("#leads-input").value.trim();
  if (!leads) { toast("Paste some leads or load the sample list first"); return; }
  const icp = readIcp();
  try { localStorage.setItem("signal.icp", JSON.stringify(icp)); } catch { /* ignore */ }
  const btn = $("#run-btn");
  btn.disabled = true; btn.firstElementChild.textContent = "Working…";
  try {
    const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ leads, icp }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Request failed");
    let msg = `Processing ${data.total} unique leads`;
    if (data.duplicates) msg += ` · merged ${data.duplicates} duplicate${data.duplicates > 1 ? "s" : ""}`;
    if (data.invalid) msg += ` · skipped ${data.invalid} invalid row${data.invalid > 1 ? "s" : ""}`;
    toast(msg);
    openJob(data.job_id);
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false; btn.firstElementChild.textContent = "Enrich & score leads";
  }
}
$("#run-btn").addEventListener("click", run);

function openJob(jobId) {
  state.jobId = jobId; state.leads = []; state.seen = new Set();
  $("#empty-state").hidden = true; $("#results-view").hidden = false;
  $("#progress-wrap").hidden = false;
  clearInterval(state.poll);
  refresh();
  state.poll = setInterval(refresh, 1200);
}

async function refresh() {
  const r = await fetch(`/api/jobs/${state.jobId}`);
  if (!r.ok) return;
  const { job, leads } = await r.json();
  state.leads = leads;
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 100;
  $("#progress-bar").style.width = pct + "%";
  $("#progress-pct").textContent = pct + "%";
  $("#progress-text").textContent =
    job.status === "running" ? `Crawling & verifying… ${job.done}/${job.total}` :
    job.status === "ai" ? "Writing AI briefs for top-ranked leads…" : `Done - ${job.total} leads ranked`;
  $("#k-dups").textContent = job.duplicates ? ` · ${job.duplicates} duplicates merged` : "";
  if (job.status === "done") {
    clearInterval(state.poll);
    setTimeout(() => ($("#progress-wrap").hidden = true), 1500);
    loadHistory();
  }
  renderKpis();
  renderTable();
}

// ---------------------------------------------------------------- results
function renderKpis() {
  const L = state.leads;
  const verified = L.filter((l) => l.emails.some((e) => e.status === "valid")).length;
  const live = L.filter((l) => l.scores.tier !== "X").length;
  $("#k-total").textContent = L.length;
  $("#k-a").textContent = L.filter((l) => l.scores.tier === "A").length;
  $("#k-verified").textContent = verified;
  $("#k-verified-pct").textContent = live ? ` · ${Math.round((verified / live) * 100)}% of live` : "";
  $("#k-people").textContent = L.filter((l) => l.contacts.length).length;
  $("#k-dq").textContent = L.filter((l) => l.scores.tier === "X").length;
}

function filtered() {
  const tiers = new Set($$("#tier-chips .chip.on").map((c) => c.dataset.tier));
  const q = $("#search").value.toLowerCase().trim();
  const needVerified = $("#f-verified").checked;
  const needPerson = $("#f-person").checked;
  return state.leads
    .filter((l) => tiers.has(l.scores.tier))
    .filter((l) => !needVerified || l.emails.some((e) => e.status === "valid"))
    .filter((l) => !needPerson || l.contacts.length)
    .filter((l) => !q || [l.company_name, l.domain, ...l.contacts.map((c) => c.name)].join(" ").toLowerCase().includes(q))
    .sort((a, b) => state.sort === "founded"
      ? (a.profile.founded_year || 9999) - (b.profile.founded_year || 9999)
      : b.scores.priority - a.scores.priority);
}

function emailBadge(e) {
  if (!e) return '<span class="badge neutral">No email</span>';
  const cls = e.status === "valid" ? "good" : e.status === "risky" ? "warn" : "bad";
  const icon = e.status === "valid" ? "✓" : e.status === "risky" ? "!" : "✕";
  return `<div class="email">${esc(e.email)}</div><span class="badge ${cls}" title="${esc(e.reason)}">${icon} ${e.status}${e.type === "role" ? " · role inbox" : ""}</span>`;
}

function keySignals(l) {
  const p = l.profile || {};
  const out = [];
  if (l.scores.disqualified) return `<span class="badge bad">${esc(l.scores.disqualified)}</span>`;
  (p.owner_signals || []).slice(0, 2).forEach((s) => out.push(`<span class="badge good">${esc(s)}</span>`));
  (p.succession_signals || []).forEach((s) => out.push(`<span class="badge warn">${esc(s)}</span>`));
  if (p.recurring_hits >= 2) out.push('<span class="badge good">Recurring revenue</span>');
  const cats = Object.values(p.tech || {});
  if (!cats.includes("crm") && !cats.includes("marketing")) out.push('<span class="badge neutral">No CRM</span>');
  if (p.copyright_year && new Date().getFullYear() - p.copyright_year >= 2) out.push(`<span class="badge neutral">Site © ${p.copyright_year}</span>`);
  if (l.crawl?.status === "blocked") out.push('<span class="badge warn">Bot-protected · verify</span>');
  if (l.crawl?.status === "thin") out.push('<span class="badge warn">JS site · partial data</span>');
  if ((p.enterprise_hits || 0) >= 1) out.push('<span class="badge bad">Franchise / enterprise</span>');
  return out.slice(0, 4).join("") || '<span class="muted tiny">—</span>';
}

function bar(v, title) { return `<i title="${title}: ${v}"><b style="width:${v}%"></b></i>`; }

function renderTable() {
  const rows = filtered();
  $("#no-rows").hidden = rows.length > 0;
  $("#rows").innerHTML = rows.map((l) => {
    const s = l.scores, c = l.contacts[0], e = l.emails[0];
    const isNew = !state.seen.has(l.domain);
    state.seen.add(l.domain);
    return `<tr data-domain="${esc(l.domain)}" class="${s.tier === "X" ? "dq" : ""} ${isNew ? "new" : ""}">
      <td><span class="tier ${s.tier}">${s.tier === "X" ? "✕" : s.tier}</span></td>
      <td><div class="score">${s.priority}</div>
        <div class="subbars">${bar(s.reachability, "Reachability")}${bar(s.acquisition_fit, "Acquisition fit")}${bar(s.digital_gap, "Digital gap")}</div></td>
      <td><div class="co-name">${esc(l.company_name)}</div><div class="co-domain">${esc(l.domain)}</div></td>
      <td>${c ? `<div>${esc(c.name)}</div><div class="muted tiny">${esc(c.title)}</div>` : '<span class="muted tiny">Not found</span>'}</td>
      <td>${emailBadge(e)}${l.phones[0] ? `<div class="muted tiny">${esc(l.phones[0])}</div>` : ""}</td>
      <td>${l.profile.founded_year ? `${l.profile.founded_year}<div class="muted tiny">${new Date().getFullYear() - l.profile.founded_year} yrs</div>` : '<span class="muted tiny">—</span>'}</td>
      <td>${keySignals(l)}</td>
    </tr>`;
  }).join("");
}

$("#rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (tr) openDrawer(tr.dataset.domain);
});
$("#search").addEventListener("input", renderTable);
$$("#tier-chips .chip").forEach((c) => c.addEventListener("click", () => { c.classList.toggle("on"); renderTable(); }));
$("#f-verified").addEventListener("change", renderTable);
$("#f-person").addEventListener("change", renderTable);
$$("th.sortable").forEach((th) => th.addEventListener("click", () => { state.sort = th.dataset.sort; renderTable(); }));

// ---------------------------------------------------------------- drawer
function openDrawer(domain) {
  const l = state.leads.find((x) => x.domain === domain);
  if (!l) return;
  const s = l.scores, p = l.profile || {}, ins = l.insights || {};
  const reasons = [...s.reasons].sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
  const bucketName = { reachability: "Reachability", acquisition_fit: "Acq. fit", digital_gap: "Digital gap", data: "Data" };
  $("#drawer-body").innerHTML = `
    <div class="muted tiny">${esc(l.domain)} · <a href="https://${esc(l.domain)}" target="_blank" rel="noopener">open site ↗</a></div>
    <h2>${esc(l.company_name)}</h2>
    <span class="tier ${s.tier}">${s.tier === "X" ? "✕" : s.tier}</span>
    ${s.disqualified ? `<span class="badge bad">${esc(s.disqualified)}</span>` : ""}
    <div class="scorecards">
      <div class="scorecard"><strong>${s.priority}</strong><small>Priority</small></div>
      <div class="scorecard"><strong>${s.reachability}</strong><small>Reachability</small></div>
      <div class="scorecard"><strong>${s.acquisition_fit}</strong><small>Acquisition fit</small></div>
      <div class="scorecard"><strong>${s.digital_gap}</strong><small>Digital gap</small></div>
    </div>

    <h3>Suggested opener <span class="src">(${ins.source === "claude" ? "written by Claude" : ins.source === "groq" ? "written by AI (Groq)" : "template"})</span></h3>
    <div class="opener" id="opener-text">${esc(ins.opener || "")}</div>
    <div class="row gap"><button class="btn ghost small" id="copy-opener">Copy opener</button>
      ${l.emails[0] ? `<a class="btn ghost small" href="mailto:${esc(l.emails[0].email)}?body=${encodeURIComponent(ins.opener || "")}">Draft email</a>` : ""}</div>

    <h3>Brief</h3>
    <p>${esc(ins.summary || "—")}</p>
    ${ins.pain_points?.length ? `<b>Likely pain points</b><ul class="list">${ins.pain_points.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${ins.ai_opportunities?.length ? `<b>Value-creation levers</b><ul class="list">${ins.ai_opportunities.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${ins.red_flags?.length ? `<b>Red flags</b><ul class="list">${ins.red_flags.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}

    <h3>Why this score</h3>
    ${reasons.map((r) => `<div class="reason"><span class="pts ${r.points > 0 ? "pos" : r.points < 0 ? "neg" : ""}">${r.points > 0 ? "+" : ""}${r.points || "·"}</span><span>${esc(r.text)}</span><span class="bucket">${bucketName[r.bucket] || ""}</span></div>`).join("")}

    <h3>Decision makers</h3>
    ${l.contacts.length ? l.contacts.map((c) => `<div>${esc(c.name)} <span class="muted">– ${esc(c.title)}</span> <span class="src">via ${c.source}</span></div>`).join("") : '<p class="muted">None named on the website.</p>'}

    <h3>Contact data</h3>
    ${l.emails.map((e) => `<div class="row gap">${emailBadge(e)}</div>`).join("") || '<p class="muted">No emails found.</p>'}
    ${l.phones.length ? `<p>${l.phones.map(esc).join(" · ")}</p>` : ""}
    ${Object.entries(p.socials || {}).map(([k, v]) => `<a href="${esc(v)}" target="_blank" rel="noopener" class="badge neutral">${esc(k)} ↗</a>`).join("")}

    <h3>Company facts</h3>
    <div class="kv">
      <span>Founded</span><span>${p.founded_year || "—"}</span>
      <span>Employees</span><span>${p.employees || l.input?.employees || "—"}</span>
      <span>Address</span><span>${esc(p.json_ld?.address || [l.input?.city, l.input?.state].filter(Boolean).join(", ") || "—")}</span>
      <span>Tech stack</span><span>${esc(Object.keys(p.tech || {}).join(", ") || "None detected")}</span>
      <span>Site updated</span><span>${p.copyright_year ? "© " + p.copyright_year : "—"}</span>
      <span>Pages read</span><span>${(p.pages_crawled || []).map((u) => `<div class="tiny"><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//, ""))}</a></div>`).join("") || "—"}</span>
      <span>Crawl</span><span>${esc(l.crawl?.status)} ${l.crawl?.error ? "· " + esc(l.crawl.error) : ""}</span>
    </div>`;
  $("#copy-opener").addEventListener("click", () => {
    navigator.clipboard.writeText(ins.opener || "").then(() => toast("Opener copied"));
  });
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
}
$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ---------------------------------------------------------------- export
$("#export-btn").addEventListener("click", (e) => { e.stopPropagation(); $("#export-menu").hidden = !$("#export-menu").hidden; });
document.addEventListener("click", (e) => { if (!e.target.closest(".export")) $("#export-menu").hidden = true; });
$$("#export-menu button").forEach((b) => b.addEventListener("click", () => {
  const tiers = $("#exp-ab").checked ? "A,B" : "A,B,C";
  const verified = $("#exp-verified").checked;
  window.location = `/api/jobs/${state.jobId}/export?fmt=${b.dataset.fmt}&tiers=${tiers}&verified_only=${verified}`;
  $("#export-menu").hidden = true;
}));

// ---------------------------------------------------------------- history
async function loadHistory() {
  try {
    const jobs = await (await fetch("/api/jobs")).json();
    $("#history-section").hidden = !jobs.length;
    $("#history").innerHTML = jobs.slice(0, 6).map((j) =>
      `<li data-id="${j.id}"><span>${new Date(j.created_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span><span class="muted">${j.total} leads · ${j.status}</span></li>`).join("");
    $$("#history li").forEach((li) => li.addEventListener("click", () => openJob(li.dataset.id)));
  } catch { /* ignore */ }
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

init();
