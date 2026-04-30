"""
Stage 6 Flask backend for the Agentic Assessment workflow.
- Persistent asyncio worker loop on a daemon thread (fixes Windows TaskGroup
  crashes from asyncio.run cleanup races between sequential MCP sessions).
- Single-workflow-at-a-time (rejects new starts while one is running).
- Frontend polls GET /api/state/<id>; trace events stream into state file as agents run.
"""

import asyncio
import threading
import traceback
from concurrent.futures import Future
from pathlib import Path

from flask import Flask, request, jsonify, render_template, abort

import workflow_state as ws


app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)


# ─── Persistent asyncio worker loop ───────────────────────────────────────
# One loop, one daemon thread, lives for the life of the process.
# Solves Windows asyncio.run cleanup races between sequential agent runs:
# spawning a new event loop per agent caused MCP stdio subprocess pipes to
# close in inconsistent states, surfacing as TaskGroup crashes mid-workflow.
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_RUNNING = {"workflow_id": None, "busy_for": None}


def _start_worker_loop():
    global _LOOP, _LOOP_THREAD
    if _LOOP is not None:
        return

    # Windows: ProactorEventLoop is required for subprocess support (MCP stdio).
    # Default policy on modern Python already picks Proactor; we set it explicitly.
    if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    _LOOP = asyncio.new_event_loop()

    def _runner():
        asyncio.set_event_loop(_LOOP)
        _LOOP.run_forever()

    _LOOP_THREAD = threading.Thread(target=_runner, daemon=True, name="asyncio-worker")
    _LOOP_THREAD.start()


def _submit_async(coro) -> Future:
    """Submit a coroutine to the persistent worker loop. Returns a Future."""
    if _LOOP is None:
        _start_worker_loop()
    return asyncio.run_coroutine_threadsafe(coro, _LOOP)


def _is_busy() -> bool:
    return _RUNNING["workflow_id"] is not None


def _mark_busy(workflow_id: str, busy_for: str) -> None:
    _RUNNING["workflow_id"] = workflow_id
    _RUNNING["busy_for"] = busy_for


def _mark_idle() -> None:
    _RUNNING["workflow_id"] = None
    _RUNNING["busy_for"] = None


def _set_status(workflow_id: str, status: str) -> None:
    state = ws.get_state(workflow_id)
    state["status"] = status
    ws.save_state(state)


def _set_error(workflow_id: str, exc: Exception) -> None:
    traceback.print_exc()
    try:
        state = ws.get_state(workflow_id)
        state["status"] = "error"
        state["error"] = f"{type(exc).__name__}: {exc}"
        ws.save_state(state)
    except Exception as e:
        print(f"[error-handler] could not write error to state: {e}")


# ─── Done callbacks (run on the worker thread when a future completes) ────

def _on_routing_done(workflow_id: str, fut: Future) -> None:
    try:
        fut.result()
        _set_status(workflow_id, "awaiting_human")
    except Exception as e:
        _set_error(workflow_id, e)
    finally:
        with _LOCK:
            _mark_idle()


def _on_step_done(workflow_id: str, fut: Future) -> None:
    try:
        fut.result()
        _set_status(workflow_id, "awaiting_human")
    except Exception as e:
        _set_error(workflow_id, e)
    finally:
        with _LOCK:
            _mark_idle()


def _on_synthesis_done(workflow_id: str, fut: Future) -> None:
    try:
        fut.result()
        # run_synthesis itself sets status=complete via ws.complete()
    except Exception as e:
        _set_error(workflow_id, e)
    finally:
        with _LOCK:
            _mark_idle()


# ─── Background runners (submit coroutines to the worker loop) ───────────

def _kick_routing(workflow_id: str, revision_feedback: str = "") -> None:
    from agents.orchestrator import run_routing_async
    _set_status(workflow_id, "running")
    fut = _submit_async(run_routing_async(workflow_id, revision_feedback))
    fut.add_done_callback(lambda f: _on_routing_done(workflow_id, f))


def _kick_step(workflow_id: str, revision_feedback: str = "") -> None:
    from agents.orchestrator import run_step_async
    _set_status(workflow_id, "running")
    fut = _submit_async(run_step_async(workflow_id, revision_feedback))
    fut.add_done_callback(lambda f: _on_step_done(workflow_id, f))


def _kick_synthesis(workflow_id: str) -> None:
    from agents.orchestrator import run_synthesis
    # run_synthesis is sync — wrap in a coroutine so it goes through the worker loop.
    async def _wrap():
        return run_synthesis(workflow_id)
    _set_status(workflow_id, "running")
    fut = _submit_async(_wrap())
    fut.add_done_callback(lambda f: _on_synthesis_done(workflow_id, f))


# ─── HTML page ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─── API routes ───────────────────────────────────────────────────────────

@app.route("/api/start", methods=["POST"])
def api_start():
    """
    Body: {report_text: str, mode: 'manual'|'auto', selected_agents?: [str]}
    Returns: {workflow_id}
    """
    data = request.get_json(force=True) or {}
    report_text = (data.get("report_text") or "").strip()
    mode = data.get("mode", "manual")
    selected = data.get("selected_agents") or ["radiologist", "pharmacist", "physician"]

    if not report_text:
        return jsonify({"error": "report_text required"}), 400
    if mode not in ("manual", "auto"):
        return jsonify({"error": "mode must be manual or auto"}), 400

    if mode == "auto":
        # Auto mode: pre-seed with all three; routing will overwrite.
        selected = ["radiologist", "pharmacist", "physician"]

    workflow_id = ws.create_run(report_text, selected, mode=mode)
    return jsonify({"workflow_id": workflow_id})


@app.route("/api/run_routing", methods=["POST"])
def api_run_routing():
    """Body: {workflow_id, revision_feedback?}. Submits routing to worker loop."""
    data = request.get_json(force=True) or {}
    workflow_id = data.get("workflow_id")
    revision_feedback = data.get("revision_feedback", "")
    if not workflow_id:
        return jsonify({"error": "workflow_id required"}), 400

    with _LOCK:
        if _is_busy():
            return jsonify({"error": "another workflow is running"}), 409
        _mark_busy(workflow_id, "routing")

    _kick_routing(workflow_id, revision_feedback)
    return jsonify({"ok": True, "status": "running"})


@app.route("/api/run_step", methods=["POST"])
def api_run_step():
    """Body: {workflow_id, revision_feedback?}. Submits next agent to worker loop."""
    data = request.get_json(force=True) or {}
    workflow_id = data.get("workflow_id")
    revision_feedback = data.get("revision_feedback", "")
    if not workflow_id:
        return jsonify({"error": "workflow_id required"}), 400

    state = ws.get_state(workflow_id)
    if ws.current_agent(state) is None:
        return jsonify({"error": "no more agents to run"}), 400

    with _LOCK:
        if _is_busy():
            return jsonify({"error": "another workflow is running"}), 409
        _mark_busy(workflow_id, "step")

    _kick_step(workflow_id, revision_feedback)
    return jsonify({"ok": True, "status": "running"})


@app.route("/api/gate", methods=["POST"])
def api_gate():
    """
    Body: {
      workflow_id, gate_type: 'routing'|'agent', action: 'approve'|'edit'|'reject'|'revise',
      agent?: str (required for gate_type=agent),
      edited?: dict (for action=edit),
      feedback?: str (for action=revise)
    }
    Sync. Updates state. Caller decides what to call next (run_step, run_routing, etc).
    """
    data = request.get_json(force=True) or {}
    workflow_id = data.get("workflow_id")
    gate_type = data.get("gate_type")
    action = data.get("action")
    if not all([workflow_id, gate_type, action]):
        return jsonify({"error": "workflow_id, gate_type, action required"}), 400

    state = ws.get_state(workflow_id)

    # ─── Routing gate ────────────────────────────────────────────────────
    if gate_type == "routing":
        agent_key = "physician_routing"

        if action == "approve":
            ws.store_routing_plan(state, state["routing_plan"], approved=True)
            ws.log_action(state, agent_key, "approve")
            ws.apply_routing_to_selection(state)
            return jsonify({"ok": True, "next": "run_step"})

        if action == "edit":
            edited = data.get("edited") or {}
            ws.store_routing_plan(state, edited, approved=True)
            ws.log_action(state, agent_key, "edit")
            ws.apply_routing_to_selection(state)
            return jsonify({"ok": True, "next": "run_step"})

        if action == "reject":
            ws.log_action(state, agent_key, "reject")
            ws.reject(state)
            return jsonify({"ok": True, "next": "halt"})

        if action == "revise":
            if not ws.can_revise(state, agent_key):
                return jsonify({"error": "revise limit hit"}), 400
            feedback = data.get("feedback", "").strip()
            if not feedback:
                return jsonify({"error": "feedback required"}), 400
            ws.bump_revise(state, agent_key)
            ws.log_action(state, agent_key, "revise", feedback)
            return jsonify({"ok": True, "next": "run_routing", "feedback": feedback})

        return jsonify({"error": f"unknown action: {action}"}), 400

    # ─── Agent handoff gate ──────────────────────────────────────────────
    if gate_type == "agent":
        agent = data.get("agent")
        if not agent:
            return jsonify({"error": "agent required for gate_type=agent"}), 400

        if action == "approve":
            ws.log_action(state, agent, "approve")
            ws.advance(state)
            fresh = ws.get_state(workflow_id)
            nxt = "run_synthesis" if ws.current_agent(fresh) is None else "run_step"
            return jsonify({"ok": True, "next": nxt})

        if action == "edit":
            edited = data.get("edited") or {}
            ws.store_handoff(state, agent, edited)
            ws.log_action(state, agent, "edit")
            ws.advance(state)
            fresh = ws.get_state(workflow_id)
            nxt = "run_synthesis" if ws.current_agent(fresh) is None else "run_step"
            return jsonify({"ok": True, "next": nxt})

        if action == "reject":
            ws.log_action(state, agent, "reject")
            ws.reject(state)
            return jsonify({"ok": True, "next": "halt"})

        if action == "revise":
            if not ws.can_revise(state, agent):
                return jsonify({"error": "revise limit hit"}), 400
            feedback = data.get("feedback", "").strip()
            if not feedback:
                return jsonify({"error": "feedback required"}), 400
            ws.bump_revise(state, agent)
            ws.log_action(state, agent, "revise", feedback)
            return jsonify({"ok": True, "next": "run_step", "feedback": feedback})

        return jsonify({"error": f"unknown action: {action}"}), 400

    return jsonify({"error": f"unknown gate_type: {gate_type}"}), 400


@app.route("/api/synthesis", methods=["POST"])
def api_synthesis():
    """Body: {workflow_id}. Triggers final synthesis."""
    data = request.get_json(force=True) or {}
    workflow_id = data.get("workflow_id")
    if not workflow_id:
        return jsonify({"error": "workflow_id required"}), 400

    with _LOCK:
        if _is_busy():
            return jsonify({"error": "another workflow is running"}), 409
        _mark_busy(workflow_id, "synthesis")

    _kick_synthesis(workflow_id)
    return jsonify({"ok": True, "status": "running"})


@app.route("/api/state/<workflow_id>", methods=["GET"])
def api_state(workflow_id):
    """Polling endpoint. Returns the full state JSON."""
    try:
        state = ws.get_state(workflow_id)
    except FileNotFoundError:
        abort(404)
    state["_busy"] = (_RUNNING["workflow_id"] == workflow_id)
    state["_busy_for"] = _RUNNING["busy_for"] if state["_busy"] else None
    return jsonify(state)


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"ok": True, "busy": _is_busy(), "running": _RUNNING})


if __name__ == "__main__":
    _start_worker_loop()
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)