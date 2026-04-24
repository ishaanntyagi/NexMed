from mcp.server.fastmcp import FastMCP

mcp = FastMCP("nexmed-tools")

@mcp.tool()
def extract_vitals(report: str) -> dict:
    """
    Extract vital signs from a medical report text.
    Use this when the report mentions BP, heart rate, temperature, or respiratory rate.
    Returns a dict with vitals found.
    """
    return {
        "blood_pressure": "140/90",
        "heart_rate": 95,
        "temperature_f": 99.2,
        "respiratory_rate": 18,
        "oxygen_saturation": 96
    }

@mcp.tool()
def compute_risk_score(vitals: dict, symptoms: str) -> dict:
    """
    Compute clinical risk score from vitals and symptoms.
    Use after extract_vitals when you need severity assessment.
    Returns risk level (low/medium/high) and reasoning.
    """
    return {
        "risk_level": "high",
        "score": 7,
        "reasoning": "Elevated BP + tachycardia + chest pain = cardiac risk"
    }


@mcp.tool()
def lookup_icd_codes(diagnosis: str) -> list:
    """
    Look up ICD-10 codes for a diagnosis.
    Use when you need billing/classification codes for a condition.
    Returns list of matching ICD-10 codes with descriptions.
    """
    return [
        {"code": "I10", "description": "Essential hypertension"},
        {"code": "R07.9", "description": "Chest pain, unspecified"}
    ]


@mcp.tool()
def extract_imaging_findings(report: str) -> list:
    """
    Extract imaging/radiology findings from report text.
    Use when report mentions X-ray, CT, MRI, or ultrasound results.
    Returns list of findings with location and description.
    """
    return [
        {"location": "right lower lobe", "finding": "consolidation"},
        {"location": "pleural space", "finding": "small effusion"}
    ]


@mcp.tool()
def lookup_imaging_pattern(finding: str) -> dict:
    """
    Look up likely conditions for a given imaging finding.
    Use after extract_imaging_findings to interpret each finding.
    Returns differential diagnosis list.
    """
    return {
        "likely_conditions": ["pneumonia", "atelectasis", "pulmonary edema"],
        "confidence": "moderate"
    }


@mcp.tool()
def extract_medications(report: str) -> list:
    """
    Extract medication list from report text.
    Use when report mentions current medications or prescriptions.
    Returns list of drugs with dose if available.
    """
    return [
        {"drug": "lisinopril", "dose": "10mg daily"},
        {"drug": "aspirin", "dose": "81mg daily"},
        {"drug": "warfarin", "dose": "5mg daily"}
    ]


@mcp.tool()
def check_drug_interactions(drugs: list) -> dict:
    """
    Check for drug-drug interactions in a medication list.
    Use after extract_medications to flag dangerous combinations.
    Returns interaction warnings with severity.
    """
    return {
        "interactions_found": 1,
        "warnings": [
            {
                "drugs": ["aspirin", "warfarin"],
                "severity": "high",
                "reason": "Increased bleeding risk"
            }
        ]
    }

if __name__ == "__main__":
    mcp.run()