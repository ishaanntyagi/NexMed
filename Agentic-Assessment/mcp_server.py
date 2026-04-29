"""
MCP server with 7 medical tools.
LLM tools all on Groq for speed/reliability.
"""

import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from llm_helper import groq_complete, parse_json_safe, GROQ_SMALL

mcp = FastMCP("nexmed-tools")
DATA_DIR = Path(__file__).parent / "data"


# ============== LLM EXTRACTION TOOLS ==============

@mcp.tool()
def extract_vitals(report: str) -> dict:
    """
    Extract vital signs from a medical report.
    Use when report contains BP, HR, temp, RR, SpO2.
    Returns dict with vitals as numbers/strings.
    """
    system = (
        "You extract vital signs from medical reports. "
        "Output ONLY valid JSON. No explanation, no markdown."
    )
    user = (
        f"Extract vitals. Return JSON with keys: "
        f"blood_pressure (str), heart_rate (int), "
        f"temperature_f (float), respiratory_rate (int), oxygen_saturation (int). "
        f"Use null if missing.\n\nREPORT:\n{report}"
    )
    raw = groq_complete(system, user, model=GROQ_SMALL)
    return parse_json_safe(raw) or {"error": "extraction failed", "raw": raw[:200]}


@mcp.tool()
def extract_medications(report: str) -> list:
    """
    Extract medication list from a medical report.
    Use when report mentions current meds, prescriptions, or drug history.
    Returns list of dicts with drug, dose, frequency.
    """
    system = (
        "You extract medication lists from medical reports. "
        "Output ONLY valid JSON array. No explanation."
    )
    user = (
        f"Extract all medications. Return JSON array of objects with keys: "
        f"drug (str, lowercase generic name), dose (str), frequency (str).\n\n"
        f"REPORT:\n{report}"
    )
    raw = groq_complete(system, user, model=GROQ_SMALL)
    result = parse_json_safe(raw)
    return result if isinstance(result, list) else []


@mcp.tool()
def extract_imaging_findings(report: str) -> list:
    """
    Extract imaging and ECG findings from a medical report.
    Use when report mentions X-ray, CT, MRI, echo, or ECG results.
    Returns list of findings with modality and description.
    """
    system = (
        "You extract imaging and ECG findings from medical reports. "
        "Output ONLY valid JSON array. No explanation."
    )
    user = (
        f"Extract ALL findings as JSON ARRAY. "
        f"Each item: {{modality (chest x-ray/ecg/echocardiogram/ct/mri), "
        f"finding (short lowercase term like 'cardiomegaly' or 'st depression')}}. "
        f"Return at minimum 5 items.\n\nREPORT:\n{report}"
    )
    raw = groq_complete(system, user, model=GROQ_SMALL)
    result = parse_json_safe(raw)
    return result if isinstance(result, list) else []


# ============== RULE-BASED TOOL ==============

@mcp.tool()
def compute_risk_score(vitals: dict, symptoms: str) -> dict:
    """
    Compute clinical risk score from vitals and symptoms.
    Use after extract_vitals when severity assessment is needed.
    Returns risk level (low/medium/high) with reasoning.
    """
    score = 0
    reasons = []

    bp = vitals.get("blood_pressure", "")
    if isinstance(bp, str) and "/" in bp:
        try:
            sys_bp = int(bp.split("/")[0])
            if sys_bp >= 180:
                score += 3; reasons.append(f"BP {bp} severe HTN")
            elif sys_bp >= 140:
                score += 2; reasons.append(f"BP {bp} elevated")
            elif sys_bp < 90:
                score += 3; reasons.append(f"BP {bp} hypotension")
        except ValueError:
            pass

    hr = vitals.get("heart_rate")
    if isinstance(hr, (int, float)):
        if hr > 100:
            score += 2; reasons.append(f"HR {hr} tachycardia")
        elif hr < 50:
            score += 2; reasons.append(f"HR {hr} bradycardia")

    spo2 = vitals.get("oxygen_saturation")
    if isinstance(spo2, (int, float)):
        if spo2 < 90:
            score += 3; reasons.append(f"SpO2 {spo2}% hypoxia")
        elif spo2 < 95:
            score += 1; reasons.append(f"SpO2 {spo2}% mild hypoxia")

    symptoms_lower = symptoms.lower()
    red_flags = ["chest pain", "shortness of breath", "syncope",
                 "altered mental status", "severe", "crushing"]
    for flag in red_flags:
        if flag in symptoms_lower:
            score += 1; reasons.append(f"symptom: {flag}")

    if score >= 6:
        level = "high"
    elif score >= 3:
        level = "medium"
    else:
        level = "low"

    return {
        "risk_level": level,
        "score": score,
        "reasoning": "; ".join(reasons) if reasons else "no significant findings"
    }


# ============== LOOKUP TOOLS (JSON-backed) ==============

def _load_json(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@mcp.tool()
def lookup_icd_codes(diagnosis: str) -> list:
    """
    Look up ICD-10 codes for a diagnosis.
    Use when billing/classification codes are needed.
    Returns list of matching codes with descriptions.
    """
    db = _load_json("icd10.json")
    diagnosis_lower = diagnosis.lower().strip()

    if diagnosis_lower in db:
        return db[diagnosis_lower]

    matches = []
    for key, codes in db.items():
        if key in diagnosis_lower or diagnosis_lower in key:
            matches.extend(codes)

    return matches if matches else [{"code": "unknown", "description": f"No match for '{diagnosis}'"}]


@mcp.tool()
def check_drug_interactions(drugs: list) -> dict:
    """
    Check drug-drug interactions in a medication list.
    Use after extract_medications to flag dangerous combinations.
    Returns warnings with severity and reasoning.
    """
    db = _load_json("drug_interactions.json")
    interactions_db = db.get("interactions", [])

    drug_set = set()
    for d in drugs:
        if isinstance(d, dict):
            name = d.get("drug", "")
        else:
            name = str(d)
        drug_set.add(name.lower().strip())

    warnings = []
    for entry in interactions_db:
        pair = set(d.lower() for d in entry["drugs"])
        if pair.issubset(drug_set):
            warnings.append(entry)

    return {
        "interactions_found": len(warnings),
        "warnings": warnings
    }


@mcp.tool()
def lookup_imaging_pattern(finding: str) -> dict:
    """
    Look up likely conditions for an imaging or ECG finding.
    Use after extract_imaging_findings to interpret each finding.
    Returns differential diagnosis, next steps, urgency.
    """
    db = _load_json("imaging_patterns.json")
    finding_lower = finding.lower().strip()

    if finding_lower in db:
        return db[finding_lower]

    for key, info in db.items():
        if key in finding_lower or finding_lower in key:
            return info

    return {
        "modality": "unknown",
        "likely_conditions": [],
        "next_steps": [],
        "urgency": "unknown",
        "note": f"No pattern found for '{finding}'"
    }


if __name__ == "__main__":
    mcp.run()