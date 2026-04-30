"""
Stage 4 HITL CLI harness.
Stage 4.5: prints trace summary so you can verify capture.
Stage 5: mode picker + routing gate (auto mode).
"""

import json
import sys
from pathlib import Path

import workflow_state as ws
from agents.orchestrator import run_step, run_synthesis, run_routing


AGENT_KEYS = {
    "r": "radiologist",
    "p": "pharmacist",
    "h": "physician",
}


def pick_mode() -> str:
    print("\n=== MODE GATE ===")
    print("  [m] Manual — you pick which agents run")
    print("  [a] Auto   — Physician decides which specialists to consult")
    raw = input("Mode (m/a): ").strip().lower()
    return "auto" if raw == "a" else "manual"


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


def show_routing_plan(plan: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  PHYSICIAN ROUTING PLAN")
    print("=" * 60)
    print(json.dumps(plan, indent=2))
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


def routing_gate(workflow_id: str) -> bool:
    """
    Run routing + interactive gate. Returns True if approved (workflow continues),
    False if rejected.
    """
    print("\n>>> Physician routing pass...")
    plan = run_routing(workflow_id)
    show_routing_plan(plan)

    while True:
        state = ws.get_state(workflow_id)
        revises_left = ws.MAX_REVISE - state["revise_count"].get("physician_routing", 0)
        opts = "[A]pprove  [E]dit plan  [R]eject"
        if revises_left > 0:
            opts += f"  re[V]ise ({revises_left} left)"
        choice = input(f"\n{opts}\n> ").strip().lower()

        if choice == "a":
            ws.store_routing_plan(state, plan, approved=True)
            ws.log_action(state, "physician_routing", "approve")
            ws.apply_routing_to_selection(state)
            fresh = ws.get_state(workflow_id)
            print(f"\nRouting approved. Will run: {fresh['selected_agents']}")
            return True

        elif choice == "e":
            print("\nPaste edited routing plan JSON. End with a line 'END':")
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
                edited = json.loads("\n".join(lines))
                ws.store_routing_plan(state, edited, approved=True)
                ws.log_action(state, "physician_routing", "edit")
                ws.apply_routing_to_selection(state)
                fresh = ws.get_state(workflow_id)
                print(f"\nEdited routing applied. Will run: {fresh['selected_agents']}")
                return True
            except json.JSONDecodeError as e:
                print(f"Invalid JSON ({e}). Try again.")

        elif choice == "r":
            ws.log_action(state, "physician_routing", "reject")
            ws.reject(state)
            print("\nRouting rejected. Workflow halted.")
            return False

        elif choice == "v":
            if revises_left <= 0:
                print("Revise limit hit. Pick A / E / R.")
                continue
            feedback = input("Feedback for the router: ").strip()
            if not feedback:
                print("Empty feedback. Cancelled.")
                continue
            ws.bump_revise(state, "physician_routing")
            ws.log_action(state, "physician_routing", "revise", feedback)
            print("\n>>> Re-running routing with feedback...")
            plan = run_routing(workflow_id, revision_feedback=feedback)
            show_routing_plan(plan)

        else:
            print("Unknown. Use A / E / R / V.")


def print_trace_summary(workflow_id: str) -> None:
    state = ws.get_state(workflow_id)
    traces = state.get("traces", {})
    if not traces:
        return
    print("\n=== TRACE SUMMARY ===")
    for ag, events in traces.items():
        if not events:
            continue
        type_counts = {}
        for ev in events:
            t = ev.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        breakdown = ", ".join(f"{k}={v}" for k, v in type_counts.items())
        print(f"  {ag}: {len(events)} events  ({breakdown})")


def main():
    report_path = Path(__file__).parent / "sample_report.txt"
    if not report_path.exists():
        print(f"Missing {report_path}")
        sys.exit(1)
    report_text = report_path.read_text(encoding="utf-8")

    mode = pick_mode()

    if mode == "manual":
        selected = pick_agents()
        workflow_id = ws.create_run(report_text, selected, mode="manual")
    else:
        workflow_id = ws.create_run(
            report_text,
            ["radiologist", "pharmacist", "physician"],
            mode="auto",
        )

    print(f"\nWorkflow ID: {workflow_id}")
    print(f"State file: runs/{workflow_id}.json")
    print(f"Mode: {mode}")

    if mode == "auto":
        ok = routing_gate(workflow_id)
        if not ok:
            print_trace_summary(workflow_id)
            return

    while True:
        state = ws.get_state(workflow_id)
        agent = ws.current_agent(state)
        if agent is None:
            break

        print(f"\n>>> Running {agent}...")
        handoff = run_step(workflow_id)
        show_handoff(agent, handoff)

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
                print_trace_summary(workflow_id)
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

            else:
                print("Unknown. Use A / E / R / V.")

    print("\n>>> Final synthesis...")
    synthesis = run_synthesis(workflow_id)
    print("\n" + "=" * 60)
    print("  FINAL SYNTHESIS")
    print("=" * 60)
    print(json.dumps(synthesis, indent=2))
    print("=" * 60)
    print(f"\nState saved at: runs/{workflow_id}.json")
    print_trace_summary(workflow_id)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)