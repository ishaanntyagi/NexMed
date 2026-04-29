"""
Pharmacist agent: reviews medications, flags drug interactions.
Outputs structured handoff for physician.
"""

import asyncio
import json
import os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from groq import Groq

from llm_helper import groq_complete, GROQ_BIG, parse_json_safe
from agents.tool_bridge import mcp_to_groq_tools, filter_tools

PHARMACIST_TOOLS = [
    "extract_medications",
    "check_drug_interactions",
]

SYSTEM_PROMPT = """You are an experienced clinical pharmacist reviewing a patient report.

Your scope: medication review and drug-drug interactions.
You do NOT diagnose conditions, interpret imaging, or assess vitals.

Use tools strategically:
- extract_medications: pull the medication list from the report
- check_drug_interactions: flag dangerous combinations

Call tools you need. When done, respond with: DONE_GATHERING

Do not write the handoff yet. Just gather data."""

HANDOFF_PROMPT = """You are a pharmacist writing a structured handoff to the physician.

Based on the medication list and interaction findings gathered, output ONLY valid JSON:

{
  "from": "pharmacist",
  "to": "physician",
  "medications_reviewed": <number of meds>,
  "high_severity_interactions": ["drug1 + drug2: short reason", ...],
  "moderate_severity_interactions": ["drug1 + drug2: short reason", ...],
  "concerns": ["clinical concern 1", "clinical concern 2"],
  "urgency": "low" | "moderate" | "high",
  "display_text": "2-3 sentence natural language handoff for UI display"
}

Use only the data given. Do not invent interactions or warnings."""

# === Stage 4 HITL: revision context block ===
REVISION_BLOCK = """

IMPORTANT — REVISION REQUEST:
Your previous attempt was rejected by a human reviewer.
Reviewer feedback: {feedback}
Your previous output was:
{previous_handoff}

Read the feedback carefully. Produce a corrected handoff. Do not repeat the same mistakes."""

MAX_TURNS = 4


async def run_pharmacist(report: str, verbose: bool = True, revision_context: dict | None = None):
    """Run Pharmacist agent. Returns handoff dict.

    Stage 4: optional revision_context = {"feedback": str, "previous_handoff": dict}
    """
    system_prompt = SYSTEM_PROMPT
    if revision_context:
        system_prompt += REVISION_BLOCK.format(
            feedback=revision_context.get("feedback", ""),
            previous_handoff=json.dumps(revision_context.get("previous_handoff", {}), indent=2),
        )

    server_params = StdioServerParameters(
        command="python", args=["mcp_server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools_resp = await session.list_tools()
            all_tools = mcp_to_groq_tools(mcp_tools_resp.tools)
            tools = filter_tools(all_tools, PHARMACIST_TOOLS)

            if verbose:
                print(f"\n[Pharmacist] Loaded {len(tools)} tools\n")

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"PATIENT REPORT:\n\n{report}"}
            ]

            groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            tool_results = []

            for turn in range(MAX_TURNS):
                if verbose:
                    print(f"--- [Pharmacist] Turn {turn + 1} ---")

                resp = groq_client.chat.completions.create(
                    model=GROQ_BIG,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                )
                msg = resp.choices[0].message

                if not msg.tool_calls:
                    if verbose:
                        print("[Pharmacist] Tool gathering complete.\n")
                    break

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
                        print(f"[Pharmacist] → {name}")

                    try:
                        result = await session.call_tool(name, args)
                        result_text = result.content[0].text
                    except Exception as e:
                        result_text = json.dumps({"error": str(e)})

                    tool_results.append({"tool": name, "result": result_text})

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": result_text,
                    })

            # ---- HANDOFF GENERATION ----
            tool_summary = "\n\n".join(
                f"### {t['tool']}\n{t['result']}" for t in tool_results
            )
            handoff_input = (
                f"PATIENT REPORT:\n{report}\n\n"
                f"TOOL RESULTS:\n{tool_summary}"
            )

            if verbose:
                print("[Pharmacist] Building handoff...\n")

            handoff_prompt = HANDOFF_PROMPT
            if revision_context:
                handoff_prompt += REVISION_BLOCK.format(
                    feedback=revision_context.get("feedback", ""),
                    previous_handoff=json.dumps(revision_context.get("previous_handoff", {}), indent=2),
                )

            raw = groq_complete(handoff_prompt, handoff_input, model=GROQ_BIG)
            handoff = parse_json_safe(raw)

            if not isinstance(handoff, dict) or "from" not in handoff:
                handoff = {
                    "from": "pharmacist",
                    "to": "physician",
                    "medications_reviewed": 0,
                    "high_severity_interactions": [],
                    "moderate_severity_interactions": [],
                    "concerns": ["handoff parse failed"],
                    "urgency": "unknown",
                    "display_text": raw[:300]
                }

            return handoff


async def _test():
    from pathlib import Path
    report = Path("sample_report.txt").read_text(encoding="utf-8")
    result = await run_pharmacist(report)
    print("\n" + "=" * 60)
    print("PHARMACIST HANDOFF")
    print("=" * 60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(_test())