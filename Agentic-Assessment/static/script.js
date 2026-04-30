// =================================================================
// NexMed AI · Agentic Assessment — frontend
// Polls /api/state every POLL_MS.
// Change-detection (lastSig) skips re-render when nothing meaningful changed.
// Append-only trace updater avoids flicker during agent runs.
// =================================================================

const POLL_MS = 1500;
const API = "/api";

const state = {
    workflowId: null,
    mode: "auto",
    selectedAgents: ["radiologist", "pharmacist", "physician"],
    pollHandle: null,
    lastSnapshot: null,
    lastSig: null,
    pendingEdit: null,
    pendingRevise: null,
    renderedTraceCounts: {},
};

// ───── DOM refs ────────────────────────────────────────────────
const el = {
    newRunBtn:        document.getElementById("newRunBtn"),
    modeBtns:         document.querySelectorAll(".mode-btn"),
    modeHelp:         document.getElementById("modeHelp"),
    manualSection:    document.getElementById("manualAgentsSection"),
    agentChecks:      document.querySelectorAll(".agent-check input"),
    workflowIdDisp:   document.getElementById("workflowIdDisplay"),
    statusBadge:      document.getElementById("statusBadge"),
    topStatusPill:    document.getElementById("topStatusPill"),
    intakeCard:       document.getElementById("intakeCard"),
    reportInput:      document.getElementById("reportInput"),
    startBtn:         document.getElementById("startBtn"),
    routingCard:      document.getElementById("routingCard"),
    routingStatus:    document.getElementById("routingStatus"),
    routingBody:      document.getElementById("routingBody"),
    agentsLane:       document.getElementById("agentsLane"),
    synthesisCard:    document.getElementById("synthesisCard"),
    synthesisBody:    document.getElementById("synthesisBody"),
    editModal:        document.getElementById("editModal"),
    editTextarea:     document.getElementById("editTextarea"),
    editSubmit:       document.getElementById("editSubmit"),
    editCancel:       document.getElementById("editCancel"),
    editModalClose:   document.getElementById("editModalClose"),
    reviseModal:      document.getElementById("reviseModal"),
    reviseTextarea:   document.getElementById("reviseTextarea"),
    reviseSubmit:     document.getElementById("reviseSubmit"),
    reviseCancel:     document.getElementById("reviseCancel"),
    reviseModalClose: document.getElementById("reviseModalClose"),
};

// ───── Utility ────────────────────────────────────────────────
async function api(path, body) {
    const opts = { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(API + path, opts);
    if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.error || r.statusText);
    }
    return r.json();
}

function escape(s) {
    return String(s).replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
}

function pretty(obj) {
    try { return JSON.stringify(obj, null, 2); } catch { return String(obj); }
}

function truncate(s, n) {
    s = String(s ?? "");
    return s.length > n ? s.slice(0, n) + "…" : s;
}

// Only call lucide.createIcons() inside a specific subtree to avoid
// re-scanning the whole document every render (was a flicker source).
function lucideIn(root) {
    if (!root) return;
    if (window.lucide && lucide.createIcons) {
        try { lucide.createIcons({ root }); } catch (_) { lucide.createIcons(); }
    }
}

// ───── Mode toggle ────────────────────────────────────────────
el.modeBtns.forEach(btn => {
    btn.addEventListener("click", () => {
        el.modeBtns.forEach(b => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        state.mode = btn.dataset.mode;
        if (state.mode === "manual") {
            el.manualSection.style.display = "flex";
            el.modeHelp.textContent = "You pick which specialists run, in order.";
        } else {
            el.manualSection.style.display = "none";
            el.modeHelp.textContent = "Physician decides which specialists to consult.";
        }
    });
});

el.agentChecks.forEach(cb => {
    cb.addEventListener("change", () => {
        state.selectedAgents = Array.from(el.agentChecks)
            .filter(c => c.checked)
            .map(c => c.value);
    });
});

// ───── New run ────────────────────────────────────────────────
el.newRunBtn.addEventListener("click", () => {
    if (state.pollHandle) clearInterval(state.pollHandle);
    state.workflowId = null;
    state.lastSnapshot = null;
    state.lastSig = null;
    state.renderedTraceCounts = {};
    el.workflowIdDisp.textContent = "—";
    el.statusBadge.innerHTML = `<i data-lucide="circle"></i> IDLE`;
    el.routingCard.classList.add("hidden");
    el.synthesisCard.classList.add("hidden");
    el.agentsLane.innerHTML = "";
    el.intakeCard.classList.remove("hidden");
    el.reportInput.value = "";
    el.startBtn.disabled = false;
    setTopStatus("ready");
    lucideIn(el.statusBadge);
});

// ───── Start ──────────────────────────────────────────────────
el.startBtn.addEventListener("click", async () => {
    const text = el.reportInput.value.trim();
    if (!text) {
        el.reportInput.focus();
        return;
    }
    el.startBtn.disabled = true;
    try {
        const body = { report_text: text, mode: state.mode };
        if (state.mode === "manual") body.selected_agents = state.selectedAgents;
        const r = await api("/start", body);
        state.workflowId = r.workflow_id;
        state.lastSig = null;
        state.renderedTraceCounts = {};
        el.workflowIdDisp.textContent = r.workflow_id;
        el.intakeCard.classList.add("hidden");

        if (state.mode === "auto") {
            await api("/run_routing", { workflow_id: r.workflow_id });
        } else {
            await api("/run_step", { workflow_id: r.workflow_id });
        }
        startPolling();
    } catch (e) {
        alert("Start failed: " + e.message);
        el.startBtn.disabled = false;
    }
});

// ───── Polling ────────────────────────────────────────────────
function startPolling() {
    if (state.pollHandle) clearInterval(state.pollHandle);
    poll();
    state.pollHandle = setInterval(poll, POLL_MS);
}

async function poll() {
    if (!state.workflowId) return;
    try {
        const s = await api(`/state/${state.workflowId}`);
        const sig = stateSignature(s);

        if (sig !== state.lastSig) {
            // Structural change — full render
            state.lastSnapshot = s;
            state.lastSig = sig;
            render(s);
        } else if (s._busy) {
            // No structural change but agent is running — append any new trace events
            state.lastSnapshot = s;
            appendNewTraceEvents(s);
        }

        if (s.status === "complete" || s.status === "rejected" || s.status === "error") {
            clearInterval(state.pollHandle);
            state.pollHandle = null;
        }
    } catch (e) {
        console.error("poll", e);
    }
}

// Signature of structural fields. Trace LENGTH (not contents) is enough —
// appendNewTraceEvents handles trace deltas without a full re-render.
function stateSignature(s) {
    const traceLens = {};
    for (const k of Object.keys(s.traces || {})) {
        traceLens[k] = (s.traces[k] || []).length;
    }
    return JSON.stringify({
        status: s.status,
        busy: s._busy,
        busyFor: s._busy_for,
        idx: s.current_index,
        selected: s.selected_agents,
        handoffKeys: Object.keys(s.handoffs || {}),
        routingApproved: s.routing_approved,
        hasRoutingPlan: !!s.routing_plan,
        // Trace lengths intentionally OMITTED — trace growth alone shouldn't
        // trigger a full re-render. We only re-render when status/structure changes.
    });
}

// ───── Status badges ─────────────────────────────────────────
function setStatusBadge(status, busyFor) {
    let label = "IDLE", cls = "", icon = "circle";
    if (status === "running")                   { label = busyFor ? `RUNNING · ${busyFor}` : "RUNNING"; cls = "is-running"; icon = "loader"; }
    else if (status === "awaiting_human")       { label = "AWAITING REVIEW"; cls = "is-awaiting"; icon = "user-check"; }
    else if (status === "ready_for_synthesis")  { label = "AWAITING REVIEW"; cls = "is-awaiting"; icon = "user-check"; }
    else if (status === "complete")             { label = "COMPLETE"; cls = "is-complete"; icon = "check-circle"; }
    else if (status === "rejected")             { label = "REJECTED"; cls = "is-rejected"; icon = "x-circle"; }
    else if (status === "error")                { label = "ERROR"; cls = "is-error"; icon = "alert-triangle"; }

    const targetHTML = `<i data-lucide="${icon}"></i> ${label}`;
    if (el.statusBadge.dataset.label === label) return;  // avoid redundant DOM thrash
    el.statusBadge.dataset.label = label;
    el.statusBadge.className = `context-badge ${cls}`;
    el.statusBadge.innerHTML = targetHTML;
    lucideIn(el.statusBadge);
}

function setTopStatus(kind) {
    const label = kind === "running" ? "PROCESSING" : kind === "awaiting" ? "AWAITING REVIEW" : "READY";
    if (el.topStatusPill.dataset.kind === kind) return;
    el.topStatusPill.dataset.kind = kind;
    el.topStatusPill.className = "status-pill " + (kind === "running" ? "is-running" : kind === "awaiting" ? "is-awaiting" : "");
    el.topStatusPill.querySelector(".status-label").textContent = label;
}

// ───── Main render (only on structural change) ────────────────
function render(s) {
    setStatusBadge(s.status, s._busy_for);
    setTopStatus(
        s.status === "running" ? "running" :
        (s.status === "awaiting_human" || s.status === "ready_for_synthesis") ? "awaiting" :
        "ready"
    );

    if (s.mode === "auto" && (s.routing_plan || s._busy_for === "routing")) {
        renderRoutingCard(s);
    }

    renderAgents(s);

    if (s.status === "complete" && s.final_synthesis) {
        renderSynthesis(s.final_synthesis);
    }

    if (s.status === "ready_for_synthesis" && !s._busy && state.workflowId) {
        triggerSynthesis();
    }

    // After a full render, sync renderedTraceCounts to whatever's now in DOM
    syncRenderedTraceCounts(s);
}

function syncRenderedTraceCounts(s) {
    const traces = s.traces || {};
    for (const agent of Object.keys(traces)) {
        state.renderedTraceCounts[agent] = (traces[agent] || []).length;
    }
}

async function triggerSynthesis() {
    try {
        await api("/synthesis", { workflow_id: state.workflowId });
    } catch (e) {
        if (!String(e.message).includes("running")) console.error(e);
    }
}

// ───── Routing render ─────────────────────────────────────────
function renderRoutingCard(s) {
    el.routingCard.classList.remove("hidden");
    const plan = s.routing_plan;
    const isRunning = s._busy_for === "routing";
    const isApproved = s.routing_approved;

    if (isRunning) {
        el.routingStatus.className = "agent-status is-running";
        el.routingStatus.textContent = "ANALYZING";
    } else if (isApproved) {
        el.routingStatus.className = "agent-status is-done";
        el.routingStatus.textContent = "APPROVED";
    } else {
        el.routingStatus.className = "agent-status is-awaiting";
        el.routingStatus.textContent = "AWAITING REVIEW";
    }

    if (!plan) {
        el.routingBody.innerHTML = `
            <p class="card-help">Physician analyzing report to plan specialist consultations…</p>
            ${renderTrace(s.traces?.physician_routing)}`;
        lucideIn(el.routingBody);
        return;
    }

    const radPill = plan.need_radiologist
        ? `<span class="routing-pill is-yes">CONSULT</span>`
        : `<span class="routing-pill is-no">SKIP</span>`;
    const phPill  = plan.need_pharmacist
        ? `<span class="routing-pill is-yes">CONSULT</span>`
        : `<span class="routing-pill is-no">SKIP</span>`;

    let html = `
        <div class="routing-plan">
            <div class="routing-cell">
                <div class="routing-cell-head">
                    <span class="routing-cell-name">Radiologist</span>
                    ${radPill}
                </div>
                <div class="routing-confidence">Imaging · ECG</div>
            </div>
            <div class="routing-cell">
                <div class="routing-cell-head">
                    <span class="routing-cell-name">Pharmacist</span>
                    ${phPill}
                </div>
                <div class="routing-confidence">Meds · Interactions</div>
            </div>
        </div>
        <div class="routing-reasoning">
            <span class="routing-reasoning-label">Reasoning</span>
            ${escape(plan.reasoning || "—")}
        </div>
        <div class="routing-confidence" style="margin-bottom:10px;">Confidence: ${escape(plan.confidence || "—")}</div>
        ${renderTrace(s.traces?.physician_routing)}
    `;

    if (!isApproved && !isRunning && s.status === "awaiting_human") {
        html += renderGateButtons("routing", null);
    }

    el.routingBody.innerHTML = html;
    wireGateButtons(el.routingBody, "routing", null);
    lucideIn(el.routingBody);
}

// ───── Agents render ──────────────────────────────────────────
function renderAgents(s) {
    const order = s.selected_agents || [];

    el.agentsLane.innerHTML = "";

    // In auto mode, show skipped specialists as placeholders
    if (s.mode === "auto" && s.routing_plan && s.routing_approved) {
        if (!s.routing_plan.need_radiologist && !order.includes("radiologist")) {
            el.agentsLane.appendChild(skippedCard("Radiologist", "Skipped by routing decision"));
        }
        if (!s.routing_plan.need_pharmacist && !order.includes("pharmacist")) {
            el.agentsLane.appendChild(skippedCard("Pharmacist", "Skipped by routing decision"));
        }
    }

    order.forEach((agent, idx) => {
        const card = document.createElement("section");
        card.className = "card";

        let status, statusClass;
        if (s.handoffs[agent]) {
            status = "DONE"; statusClass = "is-done";
        } else if (idx === s.current_index && s._busy_for === "step") {
            status = "RUNNING"; statusClass = "is-running";
        } else if (idx === s.current_index && s.status === "awaiting_human") {
            status = "AWAITING REVIEW"; statusClass = "is-awaiting";
        } else if (idx < s.current_index) {
            status = "DONE"; statusClass = "is-done";
        } else {
            status = "QUEUED"; statusClass = "";
        }

        const icon = agent === "radiologist" ? "scan-eye" : agent === "pharmacist" ? "pill" : "stethoscope";
        const label = agent.charAt(0).toUpperCase() + agent.slice(1);

        card.innerHTML = `
            <div class="card-head">
                <i data-lucide="${icon}"></i>
                <span class="card-h">${label}</span>
                <span class="agent-status ${statusClass}">${status}</span>
            </div>
            <div class="card-body" data-agent-body="${agent}">
                ${renderTrace(s.traces?.[agent])}
                ${s.handoffs[agent] ? renderHandoffBox(agent, s.handoffs[agent]) : ""}
                ${(idx === s.current_index && s.status === "awaiting_human" && s.handoffs[agent]) ? renderGateButtons("agent", agent) : ""}
            </div>`;
        el.agentsLane.appendChild(card);
        wireGateButtons(card.querySelector(`[data-agent-body="${agent}"]`), "agent", agent);
    });

    lucideIn(el.agentsLane);
}

function skippedCard(name, reason) {
    const div = document.createElement("div");
    div.className = "agent-skipped";
    div.innerHTML = `<i data-lucide="minus-circle"></i> ${name} · ${reason}`;
    return div;
}

// ───── Trace render — full + per-event ────────────────────────
function renderTrace(events) {
    if (!events || events.length === 0) {
        return `<div class="agent-trace"><div class="trace-empty">No reasoning events yet.</div></div>`;
    }
    return `<div class="agent-trace">${events.map(renderTraceEventHTML).join("")}</div>`;
}

function renderTraceEventHTML(ev) {
    const t = ev.type;
    const d = ev.data || {};
    const marker = `<div class="trace-marker t-${t}">${markerLetter(t)}</div>`;

    if (t === "turn_start") {
        return `<div class="trace-turn-divider">— Turn ${d.turn} —</div>`;
    }

    let inner;
    if (t === "tool_call") {
        inner = `
            <div class="trace-content">
                <div class="trace-title">Calling tool · <span class="trace-tool-name">${escape(d.tool || "?")}</span></div>
                <div class="trace-detail">${escape(pretty(d.args || {}))}</div>
            </div>`;
    } else if (t === "tool_result") {
        inner = `
            <div class="trace-content">
                <div class="trace-title">Result · <span class="trace-tool-name">${escape(d.tool || "?")}</span><span class="trace-duration">${d.duration_ms ?? "?"}ms</span></div>
                <div class="trace-detail">${escape(truncate(d.result, 800))}</div>
            </div>`;
    } else if (t === "llm_message") {
        inner = `
            <div class="trace-content">
                <div class="trace-title">Reasoning</div>
                <div class="trace-llm-quote">${escape(truncate(d.content, 600))}</div>
            </div>`;
    } else if (t === "handoff_start") {
        inner = `<div class="trace-content"><div class="trace-title">Building handoff…</div></div>`;
    } else if (t === "handoff_built") {
        inner = `<div class="trace-content"><div class="trace-title">Handoff produced</div></div>`;
    } else if (t === "synthesis_start") {
        inner = `<div class="trace-content"><div class="trace-title">Writing assessment…</div></div>`;
    } else if (t === "synthesis_built") {
        inner = `<div class="trace-content"><div class="trace-title">Assessment complete · urgency: ${escape(d.urgency || "?")}</div></div>`;
    } else if (t === "routing_start") {
        inner = `<div class="trace-content"><div class="trace-title">Analyzing report…</div></div>`;
    } else if (t === "routing_built") {
        inner = `<div class="trace-content"><div class="trace-title">Routing plan built</div></div>`;
    } else {
        inner = `<div class="trace-content"><div class="trace-title">${escape(t)}</div></div>`;
    }

    return `<div class="trace-event">${marker}${inner}</div>`;
}

function markerLetter(t) {
    return ({
        tool_call: "T", tool_result: "✓", llm_message: "✎",
        turn_start: "·", handoff_start: "→", handoff_built: "✓",
        synthesis_start: "→", synthesis_built: "✓",
        routing_start: "→", routing_built: "✓",
    })[t] || "·";
}

// Append-only trace updater. Only renders events the DOM doesn't already have.
// This is what removes the flicker — instead of nuking and rebuilding the whole
// agentsLane every poll, we just append the new events to the existing container.
function appendNewTraceEvents(s) {
    const traces = s.traces || {};
    Object.keys(traces).forEach(agent => {
        const events = traces[agent] || [];
        const rendered = state.renderedTraceCounts[agent] || 0;
        if (events.length <= rendered) return;

        const container = document.querySelector(
            agent === "physician_routing"
                ? "#routingBody .agent-trace"
                : `[data-agent-body="${agent}"] .agent-trace`
        );
        if (!container) {
            // Container doesn't exist yet — it'll get rendered on next structural change
            return;
        }

        // Clear the empty placeholder if present
        const empty = container.querySelector(".trace-empty");
        if (empty) empty.remove();

        const newEvents = events.slice(rendered);
        newEvents.forEach(ev => {
            container.insertAdjacentHTML("beforeend", renderTraceEventHTML(ev));
        });
        state.renderedTraceCounts[agent] = events.length;

        // Auto-scroll to bottom only if user was already near the bottom
        if (container.scrollHeight - container.scrollTop - container.clientHeight < 80) {
            container.scrollTop = container.scrollHeight;
        }
    });
}

// ───── Handoff render ────────────────────────────────────────
function renderHandoffBox(agent, handoff) {
    if (agent === "physician" && handoff.assessment) {
        return `
            <span class="handoff-label">Final Assessment</span>
            <div class="synthesis-text">${escape(handoff.assessment)}</div>
            <details style="margin-top:10px;">
                <summary style="cursor:pointer;font-family:var(--mono);font-size:10.5px;color:var(--text-3);letter-spacing:0.08em;text-transform:uppercase;">Raw handoff JSON</summary>
                <div class="handoff-box" style="margin-top:8px;">${escape(pretty(handoff))}</div>
            </details>`;
    }
    return `
        <span class="handoff-label">Handoff payload</span>
        <div class="handoff-box">${escape(pretty(handoff))}</div>`;
}

// ───── Synthesis ──────────────────────────────────────────────
function renderSynthesis(syn) {
    el.synthesisCard.classList.remove("hidden");
    el.synthesisBody.innerHTML = `
        ${syn.assessment ? `<div class="synthesis-text">${escape(syn.assessment)}</div>` : ""}
        <details style="margin-top:14px;">
            <summary style="cursor:pointer;font-family:var(--mono);font-size:10.5px;color:var(--text-3);letter-spacing:0.08em;text-transform:uppercase;">Raw synthesis JSON</summary>
            <div class="handoff-box" style="margin-top:8px;">${escape(pretty(syn))}</div>
        </details>`;
}

// ───── Gate buttons ───────────────────────────────────────────
function renderGateButtons(gateType, agent) {
    return `
        <div class="gate-row">
            <span class="gate-row-label">Human-in-the-loop · review and approve</span>
            <button class="btn-primary" data-gate-action="approve">
                <i data-lucide="check"></i> Approve
            </button>
            <button class="btn-ghost" data-gate-action="edit">
                <i data-lucide="edit-3"></i> Edit
            </button>
            <button class="btn-ghost btn-warn" data-gate-action="revise">
                <i data-lucide="refresh-cw"></i> Revise
            </button>
            <button class="btn-ghost btn-danger" data-gate-action="reject">
                <i data-lucide="x"></i> Reject
            </button>
        </div>`;
}

function wireGateButtons(container, gateType, agent) {
    if (!container) return;
    container.querySelectorAll("[data-gate-action]").forEach(btn => {
        btn.addEventListener("click", () => onGate(gateType, agent, btn.dataset.gateAction));
    });
}

async function onGate(gateType, agent, action) {
    const s = state.lastSnapshot;
    if (!s) return;

    if (action === "approve") {
        await sendGate(gateType, agent, "approve");
        return;
    }
    if (action === "reject") {
        if (!confirm("Reject this run? This will halt the workflow.")) return;
        await sendGate(gateType, agent, "reject");
        return;
    }
    if (action === "edit") {
        const current = gateType === "routing" ? s.routing_plan : s.handoffs[agent];
        el.editTextarea.value = pretty(current);
        state.pendingEdit = { gateType, agent };
        el.editModal.classList.remove("hidden");
        return;
    }
    if (action === "revise") {
        el.reviseTextarea.value = "";
        state.pendingRevise = { gateType, agent };
        el.reviseModal.classList.remove("hidden");
        return;
    }
}

async function sendGate(gateType, agent, action, extras = {}) {
    try {
        const body = { workflow_id: state.workflowId, gate_type: gateType, action, ...extras };
        if (agent) body.agent = agent;
        const r = await api("/gate", body);

        // Reset trace counts for the agent we're about to re-run, so the
        // append-only updater starts from zero against the wiped state trace.
        if (r.feedback) {
            const key = gateType === "routing" ? "physician_routing" : agent;
            state.renderedTraceCounts[key] = 0;
        }

        if (r.next === "run_step") {
            const extra = r.feedback ? { revision_feedback: r.feedback } : {};
            await api("/run_step", { workflow_id: state.workflowId, ...extra });
        } else if (r.next === "run_routing") {
            const extra = r.feedback ? { revision_feedback: r.feedback } : {};
            await api("/run_routing", { workflow_id: state.workflowId, ...extra });
        } else if (r.next === "run_synthesis") {
            await api("/synthesis", { workflow_id: state.workflowId });
        }
        // Force a full render next poll
        state.lastSig = null;
        poll();
    } catch (e) {
        alert("Action failed: " + e.message);
    }
}

// ───── Edit modal wiring ──────────────────────────────────────
el.editSubmit.addEventListener("click", async () => {
    let edited;
    try {
        edited = JSON.parse(el.editTextarea.value);
    } catch (e) {
        alert("Invalid JSON: " + e.message);
        return;
    }
    el.editModal.classList.add("hidden");
    const { gateType, agent } = state.pendingEdit;
    await sendGate(gateType, agent, "edit", { edited });
});
[el.editCancel, el.editModalClose].forEach(b =>
    b.addEventListener("click", () => el.editModal.classList.add("hidden")));

// ───── Revise modal wiring ────────────────────────────────────
el.reviseSubmit.addEventListener("click", async () => {
    const feedback = el.reviseTextarea.value.trim();
    if (!feedback) { alert("Feedback required."); return; }
    el.reviseModal.classList.add("hidden");
    const { gateType, agent } = state.pendingRevise;
    try {
        const body = { workflow_id: state.workflowId, gate_type: gateType, action: "revise", feedback };
        if (agent) body.agent = agent;
        const r = await api("/gate", body);

        // Wipe the rendered count for whichever agent re-runs, so trace appends from zero
        const key = gateType === "routing" ? "physician_routing" : agent;
        state.renderedTraceCounts[key] = 0;

        if (r.next === "run_routing") {
            await api("/run_routing", { workflow_id: state.workflowId, revision_feedback: r.feedback });
        } else if (r.next === "run_step") {
            await api("/run_step", { workflow_id: state.workflowId, revision_feedback: r.feedback });
        }
        state.lastSig = null;
        poll();
    } catch (e) {
        alert("Revise failed: " + e.message);
    }
});
[el.reviseCancel, el.reviseModalClose].forEach(b =>
    b.addEventListener("click", () => el.reviseModal.classList.add("hidden")));

// ───── Boot ───────────────────────────────────────────────────
lucideIn(document.body);