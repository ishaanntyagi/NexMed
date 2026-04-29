"""
Stage 4 HITL orchestrator.
- run_step: runs the NEXT selected agent once and stores its handoff. Caller drives the loop.
- run_synthesis: finalizes after all gates pass. Reuses physician's handoff if it ran;
                 otherwise bundles whatever specialist handoffs exist.
- run_workflow: legacy non-HITL helper for smoke testing the whole pipeline.
"""

import asyncio
import json

from agents.radiologist import run_radiologist
from agents.pharmacist import run_pharmacist
from agents.physician import run_physician

import workflow_state as ws


# Each entry maps to an async function. Physician needs prior_handoffs.
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
    """Dispatch to the right async agent function."""
    report = state["report_text"]
    revision_context = None
    if revision_feedback:
        revision_context = {
            "feedback": revision_feedback,
            "previous_handoff": state["handoffs"].get(agent, {}),
        }

    if agent == "radiologist":
        return await run_radiologist(report, verbose=True, revision_context=revision_context)
    if agent == "pharmacist":
        return await run_pharmacist(report, verbose=True, revision_context=revision_context)
    if agent == "physician":
        prior = _prior_handoffs_for(state, "physician")
        return await run_physician(
            report,
            verbose=True,
            prior_handoffs=prior,
            revision_context=revision_context,
        )
    raise ValueError(f"Unknown agent: {agent}")


def run_step(workflow_id: str, revision_feedback: str = "") -> dict:
    """
    Run the next-due agent exactly once. Sync entry point for the CLI / Flask.
    If revision_feedback is given, re-runs the CURRENT agent (does not advance).
    Returns the new handoff dict (also stored in state).
    """
    state = ws.get_state(workflow_id)
    agent = ws.current_agent(state)
    if agent is None:
        raise RuntimeError("All agents finished. Call run_synthesis instead.")

    handoff = asyncio.run(_run_agent_async(agent, state, revision_feedback))
    ws.store_handoff(state, agent, handoff)
    return handoff


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