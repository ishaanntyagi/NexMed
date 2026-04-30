"""
Physician agent: analyzes ED reports, calls tools, produces assessment.
Stage 4: prior_handoffs (any subset) + revision_context.
Stage 4.5: trace_cb captures tool calls, LLM messages, synthesis events.
Stage 5: run_physician_routing — phase-1 router that decides which specialists to consult.
Returns dict: {from, to, assessment, urgency, display_text}.
"""

import asyncio
import json
import os
import time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from groq import Groq

from llm_helper import groq_complete, GROQ_BIG, parse_json_safe
from agents.tool_bridge import mcp_to_groq_tools, filter_tools

PHYSICIAN_TOOLS = [
    "extract_vitals",
    "compute_risk_score",
    "lookup_icd_codes",
]


SYSTEM_PROMPT_BASE = """You are an experienced emergency medicine physician.

Your job:
- Read any specialist handoffs provided (some specialists may have been skipped — reason only over what is given)
- Use your own tools to fill in clinical gaps (vitals, risk score, ICD codes)
- Do NOT re-run imaging or medication tools — if a specialist handoff is present, trust it

Tools available:
- extract_vitals: get vitals
- compute_risk_score: needs vitals + symptoms
- lookup_icd_codes: find codes for diagnoses

Call tools you need. When done, respond with: DONE_GATHERING

Do not write the final assessment yet. Just gather data."""


SYNTHESIS_PROMPT = """You are an experienced emergency medicine physician writing a structured clinical assessment.

You will receive: the original patient report, plus any specialist handoffs available, plus structured data extracted by tools.

Write a concise assessment with these sections:
- Primary Diagnosis
- Differential Diagnoses (top 2-3)
- Severity (low/medium/high) with reasoning
- Drug Interaction Concerns (if pharmacist handoff present)
- Recommended ICD-10 codes
- Next Steps (3-5 items, evidence-based)

Be concise. Use only the data given. Do not invent labs, treatments, or specialist input that was not provided."""

REVISION_BLOCK = """

IMPORTANT — REVISION REQUEST:
Your previous attempt was rejected by a human reviewer.
Reviewer feedback: {feedback}
Your previous assessment was:
{previous_handoff}

Read the feedback carefully. Produce a corrected assessment. Do not repeat the same mistakes."""

MAX_TURNS = 8


def _format_prior_handoffs(prior_handoffs: dict | None) -> str:
    if not prior_handoffs:
        return "No specialist handoffs available — reason directly from the patient report."
    parts = ["SPECIALIST HANDOFFS:"]
    for name, h in prior_handoffs.items():
        parts.append(f"\n--- {name.upper()} ---\n{json.dumps(h, indent=2)}")
    return "\n".join(parts)


async def run_physician(
    report: str,
    verbose: bool = True,
    prior_handoffs: dict | None = None,
    revision_context: dict | None = None,
    trace_cb=None,
):
    """
    Run Physician agent. Returns dict handoff.

    Stage 4:
      - prior_handoffs: dict of {agent_name: handoff_dict} from earlier specialists.
      - revision_context: {"feedback": str, "previous_handoff": dict} for Revise gate.
    Stage 4.5:
      - trace_cb(event_type: str, data: dict) — receives trace events live.
    """
    def _trace(event_type: str, data: dict):
        if trace_cb:
            trace_cb(event_type, data)

    system_prompt = SYSTEM_PROMPT_BASE
    if revision_context:
        system_prompt += REVISION_BLOCK.format(
            feedback=revision_context.get("feedback", ""),
            previous_handoff=json.dumps(revision_context.get("previous_handoff", {}), indent=2),
        )

    handoffs_block = _format_prior_handoffs(prior_handoffs)

    server_params = StdioServerParameters(
        command="python", args=["mcp_server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools_resp = await session.list_tools()
            all_tools = mcp_to_groq_tools(mcp_tools_resp.tools)
            tools = filter_tools(all_tools, PHYSICIAN_TOOLS)

            if verbose:
                print(f"\n[Physician] Loaded {len(tools)} tools\n")

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"PATIENT REPORT:\n\n{report}\n\n{handoffs_block}"
                )}
            ]

            groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            tool_results = []

            for turn in range(MAX_TURNS):
                if verbose:
                    print(f"--- [Physician] Turn {turn + 1} ---")
                _trace("turn_start", {"turn": turn + 1})

                resp = groq_client.chat.completions.create(
                    model=GROQ_BIG,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                )
                msg = resp.choices[0].message

                if not msg.tool_calls:
                    if msg.content:
                        _trace("llm_message", {"role": "assistant", "content": msg.content})
                    if verbose:
                        print("[Physician] Tool gathering complete.\n")
                    break

                if msg.content:
                    _trace("llm_message", {"role": "assistant", "content": msg.content})

                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        } for tc in msg.tool_calls
                    ]
                })

                for tc in msg.tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments)

                    if verbose:
                        print(f"[Physician] → {name}")

                    _trace("tool_call", {"tool": name, "args": args})
                    _t0 = time.time()

                    try:
                        result = await session.call_tool(name, args)
                        result_text = result.content[0].text
                    except Exception as e:
                        result_text = json.dumps({"error": str(e)})

                    _trace("tool_result", {
                        "tool": name,
                        "result": result_text,
                        "duration_ms": int((time.time() - _t0) * 1000),
                    })

                    tool_results.append({"tool": name, "result": result_text})

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": result_text,
                    })

            # ---- SYNTHESIS ----
            tool_summary = "\n\n".join(
                f"### {t['tool']}\n{t['result']}" for t in tool_results
            )
            synthesis_input = (
                f"PATIENT REPORT:\n{report}\n\n"
                f"{handoffs_block}\n\n"
                f"TOOL RESULTS:\n{tool_summary}"
            )

            if verbose:
                print("[Physician] Writing assessment...\n")
            _trace("synthesis_start", {})

            synth_prompt = SYNTHESIS_PROMPT
            if revision_context:
                synth_prompt += REVISION_BLOCK.format(
                    feedback=revision_context.get("feedback", ""),
                    previous_handoff=json.dumps(revision_context.get("previous_handoff", {}), indent=2),
                )

            assessment_text = groq_complete(synth_prompt, synthesis_input, model=GROQ_BIG)

            handoff = {
                "from": "physician",
                "to": "final",
                "assessment": assessment_text,
                "urgency": _guess_urgency(assessment_text),
                "display_text": assessment_text[:300],
            }

            _trace("synthesis_built", {
                "assessment": assessment_text,
                "urgency": handoff["urgency"],
            })
            _trace("handoff_built", {"handoff": handoff})
            return handoff


def _guess_urgency(text: str) -> str:
    """Cheap heuristic for the urgency pill in UI. No LLM call."""
    t = text.lower()
    if any(k in t for k in ["high severity", "high risk", "critical", "emergent"]):
        return "high"
    if any(k in t for k in ["medium severity", "moderate", "concerning"]):
        return "moderate"
    return "low"


# ============================================================
# Stage 5: Routing phase — Physician as router/planner.
# Lightweight call, no MCP tools, outputs a JSON plan.
# ============================================================

ROUTING_PROMPT = """You are an experienced emergency medicine physician triaging a new patient report.

Your job RIGHT NOW is NOT to diagnose. Your job is to decide which specialists to consult.

Available specialists:
- Radiologist: reviews imaging studies (X-ray, CT, MRI, echo) and ECG findings.
- Pharmacist: reviews medication list and flags drug-drug interactions.

Read the report. Decide which specialists are needed. Be conservative — only request a specialist if the report contains material relevant to their scope. If a specialist has nothing to work with (e.g. no medications listed → skip pharmacist), do not request them.

Output ONLY valid JSON in this exact shape:

{
  "need_radiologist": true | false,
  "need_pharmacist": true | false,
  "reasoning": "1-2 sentences explaining your routing decision, citing specific report content.",
  "confidence": "high" | "medium" | "low"
}

No other output. No markdown fences. No commentary."""


async def run_physician_routing(
    report: str,
    verbose: bool = True,
    revision_context: dict | None = None,
    trace_cb=None,
) -> dict:
    """
    Stage 5: Phase-1 Physician routing decision.
    No MCP tools. Single LLM call. Returns routing plan dict.
    """
    def _trace(event_type: str, data: dict):
        if trace_cb:
            trace_cb(event_type, data)

    routing_prompt = ROUTING_PROMPT
    if revision_context:
        routing_prompt += REVISION_BLOCK.format(
            feedback=revision_context.get("feedback", ""),
            previous_handoff=json.dumps(revision_context.get("previous_handoff", {}), indent=2),
        )

    if verbose:
        print("\n[Physician-Router] Analyzing report to plan specialists...\n")

    _trace("routing_start", {})

    raw = groq_complete(
        routing_prompt,
        f"PATIENT REPORT:\n\n{report}",
        model=GROQ_BIG,
    )

    plan = parse_json_safe(raw)

    if not isinstance(plan, dict) or "need_radiologist" not in plan:
        plan = {
            "need_radiologist": True,
            "need_pharmacist": True,
            "reasoning": f"Routing parse failed — defaulting to full workup. Raw: {raw[:200]}",
            "confidence": "low",
        }

    plan["need_radiologist"] = bool(plan.get("need_radiologist"))
    plan["need_pharmacist"] = bool(plan.get("need_pharmacist"))
    plan.setdefault("reasoning", "")
    plan.setdefault("confidence", "medium")

    if verbose:
        print(f"[Physician-Router] Plan: {json.dumps(plan, indent=2)}\n")

    _trace("routing_built", {"plan": plan})
    return plan


async def _test():
    from pathlib import Path
    report = Path("sample_report.txt").read_text(encoding="utf-8")
    result = await run_physician(report)
    print("\n" + "=" * 60)
    print("PHYSICIAN ASSESSMENT")
    print("=" * 60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(_test())