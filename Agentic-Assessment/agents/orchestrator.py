"""
Stage 4 HITL orchestrator.
Stage 4.5: trace_cb wired so each agent's events stream into the state file as it runs.
Stage 5: run_routing — phase-1 physician decides which specialists to consult.
Stage 6 fix: async siblings (run_step_async, run_routing_async) so Flask can submit
             them to a persistent worker loop instead of asyncio.run per call.
             Fixes Windows asyncio TaskGroup crash between sequential agents.
"""

import asyncio
import json

from agents.radiologist import run_radiologist
from agents.pharmacist import run_pharmacist
from agents.physician import run_physician, run_physician_routing

import workflow_state as ws


AGENT_NAMES = {"radiologist", "pharmacist", "physician"}


def _prior_handoffs_for(state: dict, agent: str) -> dict:
    """Return handoffs from agents that ran BEFORE `agent` in the selected order."""
    selected = state["selected_agents"]
    if agent not in selected:
        return {}
    idx = selected.index(agent)
    prior = {}
    for earlier in selected[:idx]:
        if earlier in state["handoffs"]:
            prior[earlier] = state["handoffs"][earlier]
    return prior


async def _run_agent_async(agent: str, state: dict, revision_feedback: str) -> dict:
    """Dispatch to the right async agent function, with trace callback wired."""
    report = state["report_text"]
    revision_context = None
    if revision_feedback:
        revision_context = {
            "feedback": revision_feedback,
            "previous_handoff": state["handoffs"].get(agent, {}),
        }
        ws.reset_trace(state, agent)

    workflow_id = state["workflow_id"]

    def trace_cb(event_type: str, data: dict):
        try:
            fresh = ws.get_state(workflow_id)
            ws.append_trace(fresh, agent, event_type, data)
        except Exception as e:
            print(f"[trace_cb] failed: {e}")

    if agent == "radiologist":
        return await run_radiologist(
            report,
            verbose=True,
            revision_context=revision_context,
            trace_cb=trace_cb,
        )
    if agent == "pharmacist":
        return await run_pharmacist(
            report,
            verbose=True,
            revision_context=revision_context,
            trace_cb=trace_cb,
        )
    if agent == "physician":
        prior = _prior_handoffs_for(state, "physician")
        return await run_physician(
            report,
            verbose=True,
            prior_handoffs=prior,
            revision_context=revision_context,
            trace_cb=trace_cb,
        )
    raise ValueError(f"Unknown agent: {agent}")


# === Sync wrappers (used by CLI / smoke tests) ===

def run_step(workflow_id: str, revision_feedback: str = "") -> dict:
    """Sync entry point for the CLI. Spawns its own event loop via asyncio.run."""
    state = ws.get_state(workflow_id)
    agent = ws.current_agent(state)
    if agent is None:
        raise RuntimeError("All agents finished. Call run_synthesis instead.")
    handoff = asyncio.run(_run_agent_async(agent, state, revision_feedback))
    fresh = ws.get_state(workflow_id)
    ws.store_handoff(fresh, agent, handoff)
    return handoff


def run_routing(workflow_id: str, revision_feedback: str = "") -> dict:
    """Sync entry point for the CLI."""
    state = ws.get_state(workflow_id)
    report = state["report_text"]

    revision_context = None
    if revision_feedback:
        revision_context = {
            "feedback": revision_feedback,
            "previous_handoff": state.get("routing_plan") or {},
        }
        ws.reset_trace(state, "physician_routing")

    workflow_id_local = state["workflow_id"]

    def trace_cb(event_type: str, data: dict):
        try:
            fresh = ws.get_state(workflow_id_local)
            ws.append_trace(fresh, "physician_routing", event_type, data)
        except Exception as e:
            print(f"[trace_cb routing] failed: {e}")

    plan = asyncio.run(run_physician_routing(
        report=report,
        verbose=True,
        revision_context=revision_context,
        trace_cb=trace_cb,
    ))

    fresh = ws.get_state(workflow_id_local)
    ws.store_routing_plan(fresh, plan, approved=False)
    return plan


# === Async siblings (used by Flask via the persistent worker loop) ===

async def run_step_async(workflow_id: str, revision_feedback: str = "") -> dict:
    """
    Flask submits this to the persistent asyncio worker loop.
    Avoids spawning a new event loop per agent — the source of Windows
    TaskGroup crashes between sequential MCP stdio sessions.
    """
    state = ws.get_state(workflow_id)
    agent = ws.current_agent(state)
    if agent is None:
        raise RuntimeError("All agents finished. Call run_synthesis instead.")
    handoff = await _run_agent_async(agent, state, revision_feedback)
    fresh = ws.get_state(workflow_id)
    ws.store_handoff(fresh, agent, handoff)
    return handoff


async def run_routing_async(workflow_id: str, revision_feedback: str = "") -> dict:
    """Flask submits this to the persistent asyncio worker loop."""
    state = ws.get_state(workflow_id)
    report = state["report_text"]

    revision_context = None
    if revision_feedback:
        revision_context = {
            "feedback": revision_feedback,
            "previous_handoff": state.get("routing_plan") or {},
        }
        ws.reset_trace(state, "physician_routing")

    workflow_id_local = state["workflow_id"]

    def trace_cb(event_type: str, data: dict):
        try:
            fresh = ws.get_state(workflow_id_local)
            ws.append_trace(fresh, "physician_routing", event_type, data)
        except Exception as e:
            print(f"[trace_cb routing] failed: {e}")

    plan = await run_physician_routing(
        report=report,
        verbose=True,
        revision_context=revision_context,
        trace_cb=trace_cb,
    )

    fresh = ws.get_state(workflow_id_local)
    ws.store_routing_plan(fresh, plan, approved=False)
    return plan


# === Synthesis (sync — no MCP/async involved) ===

def run_synthesis(workflow_id: str) -> dict:
    """
    Finalize the workflow.
    - If physician was selected and ran, its handoff IS the synthesis.
    - Otherwise return a lightweight bundle of whatever handoffs exist.
    """
    state = ws.get_state(workflow_id)

    if "physician" in state["handoffs"]:
        synth = state["handoffs"]["physician"]
    else:
        synth = {
            "from": "system",
            "to": "final",
            "assessment": "Physician was not selected. Specialist handoffs only.",
            "urgency": "unknown",
            "handoffs": state["handoffs"],
            "display_text": "No physician synthesis — physician was not part of the selected agents.",
        }
    ws.complete(state, synth)
    return synth


# === Legacy non-HITL helper (kept for smoke testing the full pipeline) ===
def run_workflow(report_text: str, agents: list[str] | None = None) -> dict:
    agents = agents or ["radiologist", "pharmacist", "physician"]
    wid = ws.create_run(report_text, agents)
    while ws.current_agent(ws.get_state(wid)) is not None:
        run_step(wid)
        ws.advance(ws.get_state(wid))
    return run_synthesis(wid)


if __name__ == "__main__":
    from pathlib import Path
    report = Path("sample_report.txt").read_text(encoding="utf-8")
    result = run_workflow(report)
    print("\n" + "=" * 60)
    print("FULL PIPELINE (no HITL)")
    print("=" * 60)
    print(json.dumps(result, indent=2))