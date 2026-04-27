"""
End-to-end test: all 7 MCP tools on the sample report.
"""

import asyncio
import json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def pretty(obj):
    """Pretty-print tool result."""
    try:
        return json.dumps(obj, indent=2)
    except Exception:
        return str(obj)


async def call(session, name, args):
    """Call a tool, parse result."""
    print(f"\n{'='*60}")
    print(f"TOOL: {name}")
    print(f"{'='*60}")
    result = await session.call_tool(name, args)
    text = result.content[0].text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = text
    print(pretty(parsed))
    return parsed


async def main():
    # Load report
    report = Path("sample_report.txt").read_text(encoding="utf-8")

    # Launch MCP server
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. Extract vitals (Ollama)
            vitals = await call(session, "extract_vitals", {"report": report})

            # 2. Extract medications (Ollama)
            meds = await call(session, "extract_medications", {"report": report})

            # 3. Extract imaging findings (Groq)
            findings = await call(session, "extract_imaging_findings", {"report": report})

            # 4. Compute risk score (rule-based)
            await call(session, "compute_risk_score", {
                "vitals": vitals if isinstance(vitals, dict) else {},
                "symptoms": "crushing substernal chest pain, diaphoresis, shortness of breath"
            })

            # 5. Lookup ICD codes (JSON)
            await call(session, "lookup_icd_codes", {"diagnosis": "NSTEMI"})
            await call(session, "lookup_icd_codes", {"diagnosis": "atrial fibrillation"})

            # 6. Check drug interactions (JSON)
            await call(session, "check_drug_interactions", {
                "drugs": meds if isinstance(meds, list) else []
            })

            # 7. Lookup imaging pattern (JSON)
            await call(session, "lookup_imaging_pattern", {"finding": "ST depression"})
            await call(session, "lookup_imaging_pattern", {"finding": "cardiomegaly"})

            print("\n" + "="*60)
            print("ALL TOOLS TESTED ✅")
            print("="*60)


asyncio.run(main())