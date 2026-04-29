"""
Stage 4 HITL CLI harness.
Runs the workflow with terminal Approve / Edit / Reject / Revise gates.
"""

import json
import sys
from pathlib import Path

import workflow_state as ws
from agents.orchestrator import run_step, run_synthesis


AGENT_KEYS = {
    "r": "radiologist",
    "p": "pharmacist",
    "h": "physician",
}


def pick_agents() -> list[str]:
    print("\n=== AGENT SELECTION GATE ===")
    print("  [r] Radiologist")
    print("  [p] Pharmacist")
    print("  [h] Physician")
    raw = input("Pick agents (e.g. 'rph' or 'rh' or 'h'): ").strip().lower()
    chosen = []
    for c in raw:
        if c in AGENT_KEYS and AGENT_KEYS[c] not in chosen:
            chosen.append(AGENT_KEYS[c])
    if not chosen:
        print("No agents picked. Defaulting to all three.")
        chosen = ["radiologist", "pharmacist", "physician"]
    print(f"Selected: {chosen}")
    return chosen


def show_handoff(agent: str, handoff: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {agent.upper()} HANDOFF")
    print("=" * 60)
    print(json.dumps(handoff, indent=2))
    print("=" * 60)


def gate_prompt(state: dict, agent: str) -> str:
    revises_left = ws.MAX_REVISE - state["revise_count"].get(agent, 0)
    options = "[A]pprove  [E]dit  [R]eject"
    if revises_left > 0:
        options += f"  re[V]ise ({revises_left} left)"
    return f"\n{options}\n> "


def handle_edit(handoff: dict) -> dict:
    print("\nPaste edited JSON. End with a single line containing only 'END':")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    try:
        return json.loads("\n".join(lines))
    except json.JSONDecodeError as e:
        print(f"Invalid JSON ({e}). Keeping original.")
        return handoff


def main():
    report_path = Path(__file__).parent / "sample_report.txt"
    if not report_path.exists():
        print(f"Missing {report_path}")
        sys.exit(1)
    report_text = report_path.read_text(encoding="utf-8")

    selected = pick_agents()
    workflow_id = ws.create_run(report_text, selected)
    print(f"\nWorkflow ID: {workflow_id}")
    print(f"State file: runs/{workflow_id}.json")

    while True:
        state = ws.get_state(workflow_id)
        agent = ws.current_agent(state)
        if agent is None:
            break

        print(f"\n>>> Running {agent}...")
        handoff = run_step(workflow_id)
        show_handoff(agent, handoff)

        # gate loop — stays here until non-revise action
        while True:
            state = ws.get_state(workflow_id)
            choice = input(gate_prompt(state, agent)).strip().lower()

            if choice == "a":
                ws.log_action(state, agent, "approve")
                ws.advance(state)
                break

            elif choice == "e":
                edited = handle_edit(handoff)
                ws.store_handoff(state, agent, edited)
                ws.log_action(state, agent, "edit")
                ws.advance(state)
                break

            elif choice == "r":
                ws.log_action(state, agent, "reject")
                ws.reject(state)
                print("\nWorkflow rejected. Halting.")
                return

            elif choice == "v":
                if not ws.can_revise(state, agent):
                    print("Revise limit hit. Pick A / E / R.")
                    continue
                feedback = input("Feedback for the agent: ").strip()
                if not feedback:
                    print("Empty feedback. Cancelled.")
                    continue
                ws.bump_revise(state, agent)
                ws.log_action(state, agent, "revise", feedback)
                print(f"\n>>> Re-running {agent} with feedback...")
                handoff = run_step(workflow_id, revision_feedback=feedback)
                show_handoff(agent, handoff)
                # loop back, present gate on the new handoff

            else:
                print("Unknown. Use A / E / R / V.")

    # === Final synthesis ===
    print("\n>>> Final synthesis...")
    synthesis = run_synthesis(workflow_id)
    print("\n" + "=" * 60)
    print("  FINAL SYNTHESIS")
    print("=" * 60)
    print(json.dumps(synthesis, indent=2))
    print("=" * 60)
    print(f"\nState saved at: runs/{workflow_id}.json")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)