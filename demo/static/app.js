const $ = (sel) => document.querySelector(sel);

const state = {
  students: [],
  studentId: null,
  sessionId: null,
  seenNotes: new Set(),
  rendered: {},
  scenarios: null,
  paramInfo: null,
  params: null,
  activeScenario: "expected",
};

const INTENT_LABELS = {
  course_content: "Course content questions",
  video_issue: "Video / app issues",
  payment_issue: "Payment failed, money deducted",
  refund_request: "Refund requests",
  login_issue: "Login / password",
  human_handoff: "Asked for a person",
};

async function api(path, body) {
  if (window.localApi) return window.localApi(path, body);
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const rich = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
const money = (n) => "\u20b9" + Number(n).toLocaleString("en-IN");
const clock = (iso) => new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
const stamp = (iso) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
const pct = (x) => `${Math.round(x * 100)}%`;

// Re-render a container only when its HTML changes, keeping open <details> and typed input values.
function render(container, html) {
  const el = typeof container === "string" ? $(container) : container;
  if (state.rendered[el.id] === html) return;
  const open = new Set([...el.querySelectorAll("details[data-key][open]")].map((d) => d.dataset.key));
  const values = {};
  el.querySelectorAll("[data-key]:is(input, textarea, select)").forEach((i) => { values[i.dataset.key] = i.value; });
  el.innerHTML = html;
  el.querySelectorAll("details[data-key]").forEach((d) => { if (open.has(d.dataset.key)) d.open = true; });
  el.querySelectorAll("[data-key]:is(input, textarea, select)").forEach((i) => {
    if (i.dataset.key in values) i.value = values[i.dataset.key];
  });
  state.rendered[el.id] = html;
}

// ---------------- student side ----------------

function addBubble(role, html) {
  const chat = $("#chat");
  const div = document.createElement("div");
  div.className = role === "event" ? "event" : `bubble ${role}`;
  div.innerHTML = html;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function renderQuick(options) {
  const quick = $("#quick");
  quick.innerHTML = "";
  options.forEach((text) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.onclick = () => send(text);
    quick.appendChild(b);
  });
}

function renderBotReply(res) {
  res.messages.forEach((m) => addBubble("bot", rich(m.text)));
  (res.events || []).forEach((e) => addBubble("event", esc(e.label)));
  const last = res.messages[res.messages.length - 1];
  renderQuick(last ? last.quick_replies : []);
}

async function selectStudent(id) {
  state.studentId = id;
  const s = state.students.find((x) => x.id === id);
  $("#scenario").innerHTML = `<b>Demo scenario:</b> ${esc(s.scenario)}<br><span class="muted">Device: ${esc(s.device)}</span>`;
  $("#chat").innerHTML = "";
  renderQuick([]);
  state.seenNotes = new Set();
  state.rendered.inbox = null;
  const res = await api("/api/session", { student_id: id });
  state.sessionId = res.session_id;
  renderBotReply(res);
  await refreshInbox(true);
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  addBubble("student", esc(text));
  renderQuick([]);
  $("#input").value = "";
  const typing = addBubble("typing", "Assistant is typing...");
  typing.className = "typing";
  try {
    const res = await api("/api/chat", { session_id: state.sessionId, student_id: state.studentId, message: text });
    typing.remove();
    renderBotReply(res);
  } catch (err) {
    typing.remove();
    addBubble("event", esc("Error: " + err.message));
  }
  poll();
}

async function refreshInbox(initial = false) {
  if (!state.studentId) return;
  const { notifications } = await api(`/api/notifications?student_id=${state.studentId}`);
  const html = notifications.length
    ? notifications.map((n) => {
        const fresh = !initial && !state.seenNotes.has(n.id) ? " fresh" : "";
        return `<div class="note${fresh}"><div class="meta">${esc(n.channel)} \u00b7 ${clock(n.created_at)}</div>${esc(n.text)}</div>`;
      }).join("")
    : `<div class="empty">Nothing yet. Proactive alerts (payment reversals, reset links, refund decisions) appear here.</div>`;
  notifications.forEach((n) => state.seenNotes.add(n.id));
  render("#inbox", html);
}

// ---------------- staff side ----------------

function kvTable(obj) {
  const rows = Object.entries(obj || {}).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td>${esc(v)}</td></tr>`);
  return rows.length ? `<table class="kv">${rows.join("")}</table>` : "";
}

function transcript(items, key) {
  if (!items || !items.length) return "";
  const lines = items.map((m) => `<div class="${m.role}"><b>${m.role === "student" ? "Student" : "Assistant"}:</b> ${rich(m.text)}</div>`);
  return `<details data-key="${key}"><summary>Full conversation (${items.length} messages)</summary><div class="transcript">${lines.join("")}</div></details>`;
}

function renderTickets(tickets) {
  const html = tickets.length ? tickets.map((t) => `
    <div class="card ${t.priority === "high" && t.status === "open" ? "high" : ""} ${t.status !== "open" ? "done" : ""}">
      <div class="card-head">
        <span class="id">${esc(t.id)}</span>
        <span class="tag brand">${esc(t.queue)}</span>
        <span class="tag ${t.priority === "high" ? "high" : ""}">${t.priority === "high" ? "High priority" : "Normal"} \u00b7 SLA ${esc(t.sla)}</span>
        <span class="tag ${t.status === "open" ? "amber" : "green"}">${t.status === "open" ? "Open" : "Resolved by " + esc(t.resolved_by)}</span>
        <span class="muted">${esc(t.source)} \u00b7 ${clock(t.created_at)}</span>
      </div>
      <div class="card-title">${esc(t.student_name)}</div>
      <div class="summary"><b>AI summary:</b> ${esc(t.summary)}</div>
      ${kvTable(t.collected)}
      ${transcript(t.transcript, "tr-" + t.id)}
      ${t.status === "open" ? `<div class="actions"><button class="btn small" data-resolve="${esc(t.id)}">Mark resolved</button></div>` : ""}
    </div>`).join("")
    : `<div class="empty">No tickets yet. Everything the assistant resolves by itself never reaches this queue.
       Try answering "No, still not playing", ask for a human, or pick Meera and say "payment failed".</div>`;
  render("#tickets", html);
}

function riskClass(level) { return level === "Low" ? "green" : level === "Medium" ? "amber" : "red"; }

function renderRefunds(cases) {
  const html = cases.length ? cases.map((c) => {
    const policyClass = c.policy.eligible_amount ? "green" : "red";
    const pending = c.status === "pending_approval";
    const decided = pending ? "" : `
      <div class="check ${c.status === "approved" ? "green" : "red"}">
        <b>${c.status === "approved" ? "Approved " + money(c.approved_amount) : "Rejected"}</b>
        by ${esc(c.decided_by)} at ${stamp(c.decided_at)}${c.decision_note ? " \u2014 " + esc(c.decision_note) : ""}
      </div>`;
    const form = !pending ? "" : `
      <div class="decide">
        <input data-key="rev-${c.id}" placeholder="Reviewer name" value="Priya Menon (Finance)">
        <input data-key="amt-${c.id}" type="number" min="1" max="${c.amount_paid}" placeholder="Amount" value="${c.policy.eligible_amount || ""}">
        <input data-key="note-${c.id}" placeholder="Note to student / audit (optional)">
        <button class="btn green small" data-decide="approve" data-case="${c.id}">Approve</button>
        <button class="btn red small" data-decide="reject" data-case="${c.id}">Reject</button>
      </div>
      <div class="error" id="err-${c.id}"></div>`;
    return `
    <div class="card ${pending ? "" : "done"}">
      <div class="card-head">
        <span class="id">${esc(c.id)}</span>
        <span class="tag ${pending ? "amber" : c.status === "approved" ? "green" : "red"}">${pending ? "Waiting for human approval" : esc(c.status)}</span>
        <span class="muted">Prepared by AI assistant \u00b7 ${clock(c.created_at)}</span>
      </div>
      <div class="card-title">${esc(c.student_name)} \u2014 ${esc(c.course)}</div>
      <div class="grid3">
        <div class="fact"><div class="l">Amount paid</div><div class="v">${money(c.amount_paid)}</div></div>
        <div class="fact"><div class="l">Bought</div><div class="v">${c.policy.days_since_purchase} days ago</div></div>
        <div class="fact"><div class="l">Course watched</div><div class="v">${pct(c.progress)}</div></div>
        <div class="fact"><div class="l">Order</div><div class="v">${esc(c.order_id)}</div></div>
      </div>
      <div><b>Student's reason:</b> ${esc(c.reason)}</div>
      <div class="check ${policyClass}"><b>Policy check: ${esc(c.policy.verdict)}</b><br>${esc(c.policy.explanation)}</div>
      <div class="check ${riskClass(c.risk.level)}"><b>Fraud risk: ${esc(c.risk.level)}</b> (score ${c.risk.score})
        <ul>${c.risk.signals.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>
      ${c.notes.map((n) => `<div class="check amber">${esc(n)}</div>`).join("")}
      <div class="rec">AI recommendation: ${esc(c.recommendation)}</div>
      ${transcript(c.transcript, "rtr-" + c.id)}
      ${decided}${form}
    </div>`;
  }).join("")
    : `<div class="empty">No refund requests yet. Pick Karan (low risk) or Vikram (high risk) and say "I want a refund".</div>`;
  render("#refunds", html);
}

function renderAudit(log) {
  const html = log.length
    ? log.map((a) => `<div class="audit-row"><span class="muted">${clock(a.at)}</span><b>${esc(a.actor)}</b><span>${esc(a.action)}</span><span>${esc(a.detail)}</span></div>`).join("")
    : `<div class="empty">No actions yet.</div>`;
  render("#audit", html);
}

function renderInsights(c) {
  const m = c.metrics;
  const t = m.totals;
  const handled = t.bot_resolved + t.prevented + t.prepared_for_human + t.escalated;
  render("#kpis", `
    <div class="kpi"><div class="l">Resolved by assistant</div><div class="v">${t.bot_resolved}</div></div>
    <div class="kpi"><div class="l">Prevented (proactive alert)</div><div class="v">${t.prevented}</div></div>
    <div class="kpi"><div class="l">Prepared for human</div><div class="v">${t.prepared_for_human}</div></div>
    <div class="kpi"><div class="l">Escalated</div><div class="v">${t.escalated}</div></div>
    <div class="kpi"><div class="l">Est. agent minutes saved</div><div class="v">${m.minutes_saved}</div></div>`);

  const rows = Object.entries(m.by_intent).map(([k, v]) => `
    <tr><td>${esc(INTENT_LABELS[k] || k)}</td><td>${v.bot_resolved}</td><td>${v.prevented}</td><td>${v.prepared_for_human}</td><td>${v.escalated}</td></tr>`);
  render("#by-intent", `<table class="data"><tr><th>Ticket type</th><th>Resolved by assistant</th><th>Prevented</th><th>Prepared for human</th><th>Escalated</th></tr>${rows.join("")}</table>
    <p class="hint">${handled} conversations with an outcome so far. Minutes saved use Exhibit A handling times.</p>`);

  render("#known-issues", c.known_issues.map((k) => `
    <div class="card high">
      <div class="card-head"><span class="id">${esc(k.id)}</span><span class="tag red">${esc(k.status)}</span></div>
      <div class="card-title">${esc(k.title)}</div>
      <div class="grid3">
        <div class="fact"><div class="l">Linked in this demo</div><div class="v">${k.demo_hits}</div></div>
        <div class="fact"><div class="l">Tickets / month</div><div class="v">${k.monthly_tickets.toLocaleString("en-IN")}</div></div>
        <div class="fact"><div class="l">Agent hours / month</div><div class="v">${k.monthly_agent_hours.toLocaleString("en-IN")}</div></div>
        <div class="fact"><div class="l">Agent equivalent</div><div class="v">${k.agent_equivalent} people</div></div>
      </div>
      <div>Every video conversation is matched to known bugs by device. This one bug costs more support capacity
        than all course-content questions combined. <b>Recommendation: schedule the fix now; it is the single biggest
        lever for the headcount target.</b></div>
    </div>`).join(""));

  render("#followups", c.followups.length
    ? `<table class="data"><tr><th>Payment</th><th>Student</th><th>Amount</th><th>Check due</th><th>Status</th></tr>${c.followups.map((f) => `
        <tr><td>${esc(f.payment_id)}</td><td>${esc(f.student)}</td><td>${esc(f.amount)}</td><td>${stamp(f.due_at)}</td>
        <td>${f.status === "scheduled" ? "Scheduled" : esc(f.payment_status === "reversed" ? "Reversed, student told" : "Escalated to Payments")}</td></tr>`).join("")}</table>`
    : `<div class="empty">None yet. Ask about a failed payment as Arjun, or send a gateway webhook from the Support queue tab.</div>`);
}

async function poll() {
  try {
    const c = await api("/api/console");
    renderTickets(c.tickets);
    renderRefunds(c.refund_cases);
    renderAudit(c.audit_log);
    renderInsights(c);
    const open = c.metrics.open_tickets;
    const pending = c.metrics.pending_refunds;
    $("#b-queue").textContent = open;
    $("#b-queue").classList.toggle("on", open > 0);
    $("#b-finance").textContent = pending;
    $("#b-finance").classList.toggle("on", pending > 0);
    await refreshInbox();
  } catch (err) {
    console.error(err);
  }
}

// ---------------- impact model ----------------

const isRatio = (key) => !key.includes("aht") && !key.includes("fte");

function renderSliders() {
  const html = Object.entries(state.paramInfo).map(([key, desc]) => {
    const v = state.params[key];
    const [min, max, step] = isRatio(key) ? [0, 1, 0.05] : key.includes("fte") ? [0, 5, 0.5] : [1, 30, 1];
    const shown = isRatio(key) ? pct(v) : key.includes("fte") ? `${v} agents` : `${v} min`;
    return `<div class="slider"><label for="p-${key}"><span>${esc(desc)}</span><b id="pv-${key}">${shown}</b></label>
      <input type="range" id="p-${key}" data-param="${key}" min="${min}" max="${max}" step="${step}" value="${v}"></div>`;
  }).join("");
  $("#sliders").innerHTML = html;
  $("#sliders").querySelectorAll("input").forEach((input) => {
    input.oninput = () => {
      const key = input.dataset.param;
      const v = Number(input.value);
      state.params[key] = v;
      $(`#pv-${key}`).textContent = isRatio(key) ? pct(v) : key.includes("fte") ? `${v} agents` : `${v} min`;
      state.activeScenario = null;
      renderScenarioButtons();
      runModel();
    };
  });
}

function renderScenarioButtons() {
  $("#scenario-buttons").innerHTML = Object.entries(state.scenarios).map(([k, s]) =>
    `<button class="btn ${state.activeScenario === k ? "active" : ""}" data-scenario="${k}">${esc(s.label)}</button>`).join("");
}

let modelTimer = null;
function runModel() {
  clearTimeout(modelTimer);
  modelTimer = setTimeout(async () => {
    const r = await api("/api/model", { params: state.params });
    const box = $("#kpi-agents").parentElement;
    $("#kpi-agents").textContent = r.agents_needed;
    box.classList.toggle("over", r.agents_needed > r.target_agents && r.agents_needed <= r.target_agents + 6);
    box.classList.toggle("far", r.agents_needed > r.target_agents + 6);
    $("#kpi-sub").innerHTML = `Today ${r.current_agents} \u00b7 CEO target ${r.target_agents}<br>
      Agent hours/month: ${Math.round(r.baseline_hours).toLocaleString("en-IN")} \u2192 ${Math.round(r.new_hours).toLocaleString("en-IN")}<br>
      Tickets reaching a human: 25,000 \u2192 ${r.human_tickets.toLocaleString("en-IN")}`;
    const max = Math.max(...r.rows.map((x) => x.baseline_hours));
    $("#bars").innerHTML = r.rows.map((x) => `
      <div class="bar-row"><span>${esc(x.label)}</span>
        <div class="bar-track"><div class="bar-base" style="width:${(x.baseline_hours / max) * 100}%"></div>
          <div class="bar-after" style="width:${(x.new_hours / max) * 100}%"></div></div>
        <span class="muted">${Math.round(x.baseline_hours).toLocaleString("en-IN")} \u2192 <b>${Math.round(x.new_hours).toLocaleString("en-IN")}</b> h</span></div>`).join("");
    let running = r.current_agents;
    const steps = r.waterfall.map((w) => {
      running += w.fte_change;
      const cls = w.fte_change < 0 ? "neg" : w.fte_change > 0 ? "pos" : "";
      return `<div class="wf-row"><span>${esc(w.lever)}</span><span class="num ${cls}">${w.fte_change > 0 ? "+" : ""}${w.fte_change.toFixed(1)}</span><span class="num">${running.toFixed(1)}</span></div>`;
    }).join("");
    $("#waterfall").innerHTML = `<div class="wf-row total"><span>Agents today</span><span class="num"></span><span class="num">${r.current_agents.toFixed(1)}</span></div>${steps}
      <div class="wf-row total"><span>Team size needed (rounded up)</span><span class="num"></span><span class="num">${r.agents_needed}</span></div>`;
  }, 120);
}

async function loadImpact() {
  const data = await api("/api/scenarios");
  state.scenarios = data.scenarios;
  state.paramInfo = data.param_info;
  selectScenario("expected");
  $("#scenario-buttons").onclick = (e) => {
    const k = e.target.dataset.scenario;
    if (k) selectScenario(k);
  };
}

function selectScenario(key) {
  state.activeScenario = key;
  state.params = Object.fromEntries(Object.keys(state.paramInfo).map((k) => [k, state.scenarios[key][k]]));
  renderScenarioButtons();
  renderSliders();
  runModel();
}

// ---------------- wiring ----------------

function setupTabs() {
  $("#tabs").onclick = (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (!btn) return;
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + btn.dataset.tab));
  };
}

function setupStaffActions() {
  $("#tickets").onclick = async (e) => {
    const id = e.target.dataset.resolve;
    if (!id) return;
    await api(`/api/tickets/${id}/resolve`, { agent: "Rahul (Support)" });
    poll();
  };
  $("#refunds").onclick = async (e) => {
    const decision = e.target.dataset.decide;
    if (!decision) return;
    const id = e.target.dataset.case;
    const get = (k) => document.querySelector(`[data-key="${k}-${id}"]`).value;
    try {
      await api(`/api/refunds/${id}/decision`, { decision, reviewer: get("rev"), amount: get("amt"), note: get("note") });
      poll();
    } catch (err) {
      $(`#err-${id}`).textContent = err.message;
    }
  };
  $("#wh-send").onclick = async () => {
    const r = await api("/api/simulate/payment-webhook", { student_id: $("#wh-student").value });
    $("#sim-result").textContent = `Gateway webhook received for ${r.student}: ${money(r.payment.amount)} failed but debited. ` +
      `Student alerted by SMS + WhatsApp and a 48h reversal check was scheduled. Switch to that student to see the message.`;
    poll();
  };
  $("#ff").onclick = async () => {
    const r = await api("/api/simulate/fast-forward", {});
    $("#sim-result").textContent = r.results.length
      ? "48 hours later: " + r.results.map((x) => `${x.payment_id} (${x.student}) ${x.result}`).join("; ")
      : "No scheduled follow-ups to check yet.";
    poll();
  };
  $("#reset").onclick = async () => {
    await api("/api/reset", {});
    state.rendered = {};
    $("#sim-result").textContent = "";
    await selectStudent(state.studentId);
    poll();
  };
}

async function init() {
  const cfg = await api("/api/config");
  $("#engine").textContent = "Intent engine: " + cfg.engine;
  state.students = cfg.students;
  const options = cfg.students.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  $("#persona").innerHTML = options;
  $("#wh-student").innerHTML = options;
  $("#persona").onchange = (e) => selectStudent(e.target.value);
  $("#composer").onsubmit = (e) => { e.preventDefault(); send($("#input").value); };
  setupTabs();
  setupStaffActions();
  await selectStudent(cfg.students[0].id);
  loadImpact();
  poll();
  setInterval(poll, 2000);
}

init();
