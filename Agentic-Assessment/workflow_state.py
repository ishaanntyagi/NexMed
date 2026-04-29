"""
Stage 4 HITL — workflow state persistence.
Pure functions. No LLM. No Flask. Just JSON on disk.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path(__file__).parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)

MAX_REVISE = 3


def _path(workflow_id: str) -> Path:
    return RUNS_DIR / f"{workflow_id}.json"


def create_run(report_text: str, selected_agents: list[str]) -> str:
    """Create new workflow. Returns workflow_id."""
    workflow_id = str(uuid.uuid4())[:8]
    state = {
        "workflow_id": workflow_id,
        "created_at": datetime.utcnow().isoformat(),
        "report_text": report_text,
        "selected_agents": selected_agents,
        "current_index": 0,
        "status": "awaiting_human",
        "handoffs": {},
        "human_actions": [],
        "revise_count": {a: 0 for a in selected_agents},
        "final_synthesis": None,
    }
    save_state(state)
    return workflow_id


def get_state(workflow_id: str) -> dict:
    p = _path(workflow_id)
    if not p.exists():
        raise FileNotFoundError(f"No run: {workflow_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    _path(state["workflow_id"]).write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )


def current_agent(state: dict) -> str | None:
    """Which agent is up next. None if all selected agents are done."""
    idx = state["current_index"]
    agents = state["selected_agents"]
    if idx >= len(agents):
        return None
    return agents[idx]


def advance(state: dict) -> None:
    """Move to next agent. Marks ready_for_synthesis if last."""
    state["current_index"] += 1
    if state["current_index"] >= len(state["selected_agents"]):
        state["status"] = "ready_for_synthesis"
    save_state(state)


def log_action(state: dict, agent: str, action: str, feedback: str = "") -> None:
    state["human_actions"].append({
        "agent": agent,
        "action": action,
        "feedback": feedback,
        "ts": datetime.utcnow().isoformat(),
    })
    save_state(state)


def store_handoff(state: dict, agent: str, handoff: dict) -> None:
    state["handoffs"][agent] = handoff
    save_state(state)


def can_revise(state: dict, agent: str) -> bool:
    return state["revise_count"].get(agent, 0) < MAX_REVISE


def bump_revise(state: dict, agent: str) -> None:
    state["revise_count"][agent] = state["revise_count"].get(agent, 0) + 1
    save_state(state)


def reject(state: dict) -> None:
    state["status"] = "rejected"
    save_state(state)


def complete(state: dict, synthesis: dict) -> None:
    state["final_synthesis"] = synthesis
    state["status"] = "complete"
    save_state(state)