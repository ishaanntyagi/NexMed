"""
Radiologist agent: reads imaging/ECG findings, interprets patterns.
Outputs structured handoff for next agent + display text for UI.
"""

import asyncio
import json
import os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from groq import Groq

from llm_helper import groq_complete, GROQ_BIG, parse_json_safe
from agents.tool_bridge import mcp_to_groq_tools, filter_tools

RADIOLOGIST_TOOLS = [
    "extract_imaging_findings",
    "lookup_imaging_pattern",
]

SYSTEM_PROMPT = """You are an expert radiologist reviewing a patient report.

Your scope: imaging studies (chest x-ray, CT, MRI, echo) and ECG.
You do NOT diagnose clinical conditions, recommend treatments, or comment on labs.

Use tools strategically:
- extract_imaging_findings: pull findings from the report
- lookup_imaging_pattern: get differential for each finding

Call tools you need. When done, respond with: DONE_GATHERING

Do not write the handoff yet. Just gather data."""

HANDOFF_PROMPT = """You are a radiologist writing a structured handoff to the physician.

Based on the imaging findings and pattern lookups gathered, output ONLY valid JSON:

{
  "from": "radiologist",
  "to": "physician",
  "key_findings": ["short bullet 1", "short bullet 2", ...],
  "differential": ["likely condition 1", "likely condition 2"],
  "urgency": "low" | "moderate" | "high" | "critical",
  "display_text": "2-3 sentence natural language handoff for UI display"
}

Use only the data given. Do not invent findings."""

# === Stage 4 HITL: revision context block ===
REVISION_BLOCK = """

IMPORTANT — REVISION REQUEST:
Your previous attempt was rejected by a human reviewer.
Reviewer feedback: {feedback}
Your previous output was:
{previous_handoff}

Read the feedback carefully. Produce a corrected handoff. Do not repeat the same mistakes."""

MAX_TURNS = 6


async def run_radiologist(report: str, verbose: bool = True, revision_context: dict | None = None):
    """Run Radiologist agent. Returns handoff dict.

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
            tools = filter_tools(all_tools, RADIOLOGIST_TOOLS)

            if verbose:
                print(f"\n[Radiologist] Loaded {len(tools)} tools\n")

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"PATIENT REPORT:\n\n{report}"}
            ]

            groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            tool_results = []

            for turn in range(MAX_TURNS):
                if verbose:
                    print(f"--- [Radiologist] Turn {turn + 1} ---")

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
                        print("[Radiologist] Tool gathering complete.\n")
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
                        print(f"[Radiologist] → {name}")

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
                print("[Radiologist] Building handoff...\n")

            # === Stage 4: also append revision context to handoff prompt if present ===
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
                    "from": "radiologist",
                    "to": "physician",
                    "key_findings": ["handoff parse failed"],
                    "differential": [],
                    "urgency": "unknown",
                    "display_text": raw[:300]
                }

            return handoff


async def _test():
    from pathlib import Path
    report = Path("sample_report.txt").read_text(encoding="utf-8")
    result = await run_radiologist(report)
    print("\n" + "=" * 60)
    print("RADIOLOGIST HANDOFF")
    print("=" * 60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(_test())