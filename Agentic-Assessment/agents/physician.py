"""
Physician agent: analyzes ED reports, calls tools, produces assessment.
All Groq. Fast end-to-end.
"""

import asyncio
import json
import os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from groq import Groq

from llm_helper import groq_complete, GROQ_BIG
from agents.tool_bridge import mcp_to_groq_tools, filter_tools

PHYSICIAN_TOOLS = [
    "extract_vitals",
    "compute_risk_score",
    "lookup_icd_codes",
]


SYSTEM_PROMPT = """You are an experienced emergency medicine physician.

You have received reports from a Radiologist and a Pharmacist. Your job:
- Read their handoffs carefully (provided in user message)
- Use your own tools to fill in clinical gaps (vitals, risk score, ICD codes)
- Do NOT re-run imaging or medication tools — specialists already did that

Tools available:
- extract_vitals: get vitals
- compute_risk_score: needs vitals + symptoms
- lookup_icd_codes: find codes for diagnoses

Call tools you need. When done, respond with: DONE_GATHERING

Do not write the final assessment yet. Just gather data."""


SYNTHESIS_PROMPT = """You are an experienced emergency medicine physician writing a structured clinical assessment.

You will receive: the original patient report, plus structured data already extracted by tools.

Write a concise assessment with these sections:
- Primary Diagnosis
- Differential Diagnoses (top 2-3)
- Severity (low/medium/high) with reasoning
- Drug Interaction Concerns
- Recommended ICD-10 codes
- Next Steps (3-5 items, evidence-based)

Be concise. Use only the data given. Do not invent labs or treatments not supported by the data."""

MAX_TURNS = 8


async def run_physician(report: str, verbose: bool = True):
    """
    Run Physician agent on a report. Returns final assessment string.
    """
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"PATIENT REPORT:\n\n{report}"}
            ]

            groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            tool_results = []

            # ---- TOOL CALLING LOOP ----
            for turn in range(MAX_TURNS):
                if verbose:
                    print(f"--- Turn {turn + 1} ---")

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
                        print(f"[Physician] Tool gathering complete.\n")
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
                        print(f"[Physician] → calling {name}")

                    try:
                        result = await session.call_tool(name, args)
                        result_text = result.content[0].text
                    except Exception as e:
                        result_text = json.dumps({"error": str(e)})

                    tool_results.append({"tool": name, "result": result_text})

                    if verbose:
                        preview = result_text[:120].replace("\n", " ")
                        print(f"[Physician] ← {preview}...\n")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": result_text,
                    })

            # ---- FINAL SYNTHESIS (Groq big) ----
            if verbose:
                print("[Physician] Synthesizing assessment...\n")

            tool_summary = "\n\n".join(
                f"### {t['tool']}\n{t['result']}" for t in tool_results
            )

            synthesis_input = (
                f"PATIENT REPORT:\n{report}\n\n"
                f"TOOL RESULTS:\n{tool_summary}"
            )

            final = groq_complete(SYNTHESIS_PROMPT, synthesis_input, model=GROQ_BIG)
            return final


async def _test():
    from pathlib import Path
    report = Path("sample_report.txt").read_text(encoding="utf-8")
    result = await run_physician(report)
    print("\n" + "=" * 60)
    print("FINAL ASSESSMENT")
    print("=" * 60)
    print(result)


if __name__ == "__main__":
    asyncio.run(_test())