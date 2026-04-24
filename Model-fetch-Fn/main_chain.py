"""
==========================================================
  NexMed AI - main_chain.py
  Pure ML logic. No web server.
==========================================================
"""

import os
import re
import base64

import ollama
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


# ==========================================================
# PHASE 1 PROMPT - VISION
# ==========================================================
CLINICAL_EXTRACTION_PROMPT = """
Analyze this medical X-ray with extreme precision for a downstream Reasoning LLM. 
Extract and list the following attributes in a structured, technical format:
1. BONE(S) INVOLVED: Identify specific bones (e.g., Distal Radius, Fifth Metatarsal).
2. FRACTURE PRESENCE: [Yes/No/Inconclusive].
3. MORPHOLOGY: (e.g., Transverse, Oblique, Spiral, Comminuted, Greenstick).
4. LOCATION: Specific segment (e.g., Intra-articular, Mid-shaft, Proximal).
5. DISPLACEMENT: Mention percentage and direction (e.g., 2mm dorsal displacement).
6. ANGULATION: Degree and direction if visible.
7. SOFT TISSUE: Note any significant swelling or joint effusion.
8. CONFIDENCE SCORE: 0-100% Also state , based on comparision of both models.

Provide ONLY the technical extraction. Do not provide patient advice.
"""


# ==========================================================
# PHASE 2 PROMPT - REASONING
# ==========================================================
REASONING_SYSTEM_PROMPT = """
ACT AS: A Consultant Orthopedic Surgeon.
CONTEXT: You are reviewing a technical extraction report provided by a Radiology AI. 
YOU DO NOT HAVE ACCESS TO THE ORIGINAL IMAGE. Your task is to interpret the text-based 
clinical features to finalize a management plan.

INPUT DATA SOURCE: Automated Vision Extraction Report.

REQUIRED REPORT SECTIONS (in this exact order):
1. CLINICAL SYNTHESIS
2. TRIAGE CATEGORY
3. PATHOPHYSIOLOGY
4. STABILITY ASSESSMENT
5. SURGICAL VS. NON-SURGICAL

STRICT FORMATTING RULES:
- Each section MUST start on a new line with the exact header "N. SECTION NAME:" 
  (e.g., "1. CLINICAL SYNTHESIS:").
- Under each header, write every point as a separate bullet on its own line, 
  prefixed with "- ".
- Do NOT write paragraphs. Do NOT chain bullets together with " - " inline.
- Each bullet is one standalone sentence. Keep them short and clinical.
- Do NOT use markdown asterisks or bold.
- Do NOT repeat the section title inside the body.

TONE: Decisive, professional, and strictly data-driven.
"""


# ==========================================================
# PHASE 1 - VISION FUNCTIONS
# ==========================================================
def get_groq_vision(image_path):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    with open(image_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode("utf-8")

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": CLINICAL_EXTRACTION_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
            ],
        }],
        temperature=0.0,
    )
    return response.choices[0].message.content


def get_local_vision(image_path):
    response = ollama.chat(
        model="qwen3-vl:2b",
        keep_alive=0,
        messages=[{
            "role": "user",
            "content": CLINICAL_EXTRACTION_PROMPT,
            "images": [image_path],
        }],
    )
    return response["message"]["content"]


def vision_extractor_factory(image_path, model_choice="Groq"):
    if model_choice == "Groq":
        return get_groq_vision(image_path)
    return get_local_vision(image_path)


# ==========================================================
# PHASE 2 - REASONING FUNCTIONS
# ==========================================================
def get_groq_reasoning(features_text):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": REASONING_SYSTEM_PROMPT},
            {"role": "user",   "content": f"INPUT FEATURES:\n{features_text}"},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content


def get_local_reasoning(features_text):
    response = ollama.chat(
        model="gemma4:e4b",
        keep_alive=0,
        messages=[
            {"role": "system", "content": REASONING_SYSTEM_PROMPT},
            {"role": "user",   "content": f"INPUT FEATURES:\n{features_text}"},
        ],
    )
    return response["message"]["content"]


def reasoning_factory(features_text, model_choice="Groq"):
    if model_choice == "Groq":
        return get_groq_reasoning(features_text)
    return get_local_reasoning(features_text)


# ==========================================================
# FULL PIPELINE
# ==========================================================
def nexmed_pipeline(image_path, vision_choice="Groq", reasoning_choice="Groq"):
    print(f"Phase 1: Extracting features using {vision_choice}...")
    features = vision_extractor_factory(image_path, vision_choice)
    print(f"Phase 2: Generating report using {reasoning_choice}...")
    report = reasoning_factory(features, reasoning_choice)
    return features, report


# ==========================================================
# PARSERS
# ==========================================================
FEATURE_KEYS = [
    ("BONE",         r"BONE\(?S?\)?\s*INVOLVED"),
    ("FRACTURE",     r"FRACTURE\s*PRESENCE"),
    ("MORPHOLOGY",   r"MORPHOLOGY"),
    ("LOCATION",     r"LOCATION"),
    ("DISPLACEMENT", r"DISPLACEMENT"),
    ("ANGULATION",   r"ANGULATION"),
    ("SOFT TISSUE",  r"SOFT\s*TISSUE"),
    ("CONFIDENCE",   r"CONFIDENCE\s*SCORE"),
]

REPORT_KEYS = [
    ("CLINICAL SYNTHESIS",        r"CLINICAL\s*SYNTHESIS"),
    ("TRIAGE CATEGORY",           r"TRIAGE\s*CATEGORY"),
    ("PATHOPHYSIOLOGY",           r"PATHOPHYSIOLOGY"),
    ("STABILITY ASSESSMENT",      r"STABILITY\s*ASSESSMENT"),
    ("SURGICAL VS. NON-SURGICAL", r"SURGICAL\s*VS\.?\s*NON[- ]?SURGICAL"),
]


def _clean_value(text):
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"^[\s\-\:•]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _prettify_body(text):
    """Break run-on prose into one bullet per line."""
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"-{3,}", " ", text)
    text = re.sub(r"[ \t]+", " ", text).strip()

    # Break inline bullet separators
    text = re.sub(r"(?<=\S)\s+[-–—•]\s+(?=\S)", "\n", text)

    # Break "Sentence. Next sentence." on capital-letter boundary
    text = re.sub(r"(?<=[a-z0-9\]\)])\.\s+(?=[A-Z][a-z])", ".\n", text)

    # Break chained clauses at semicolons
    text = re.sub(r";\s+", "\n", text)

    lines = [ln.strip(" -•\t") for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    # Drop naked section-title echoes (e.g., "CLINICAL SYNTHESIS:")
    lines = [ln for ln in lines if not re.match(r"^[A-Z][A-Z\s\.\/\-]{4,}:?\s*$", ln)]

    if not lines:
        return ""

    if len(lines) == 1 and len(lines[0]) < 140:
        return lines[0]

    return "\n".join(f"- {ln}" for ln in lines)


def _strip_trailing_header_leak(body, schema, current_label):
    """
    If an LLM's body for section A has run past the "1. B:" header of
    section B and included B's bullets inline, chop the leak off.
    Returns (clean_body_for_A, leaked_text_for_downstream) where leaked_text
    still contains the "N. B:" header so the outer split can recapture it.
    """
    for i, (label, pat) in enumerate(schema):
        if label == current_label:
            continue
        # Match a subsequent header anywhere in the body
        leak_re = re.compile(
            rf"(?i)(?:(?<=^)|(?<=[\s.\]\)\-—–]))"
            rf"(?:\d+\s*[\.\)]\s*)?\*{{0,2}}\s*(?:{pat})\s*\*{{0,2}}\s*[:\-–—]?"
        )
        m = leak_re.search(body)
        if m:
            # Keep everything before the leak for the current section,
            # return the leaked slice so the caller can re-parse it.
            before = body[:m.start()].rstrip(" -–—•:,;\t\n")
            leak   = body[m.start():]
            return before, leak
    return body, ""


def parse_sections(raw_text, schema, prettify=False):
    """Robust section splitter with leak recovery and empty-section pruning."""

    combined = "|".join(f"(?P<k{i}>{pat})" for i, (_, pat) in enumerate(schema))
    header_pattern = re.compile(
        rf"(?i)(?:(?<=^)|(?<=[\s.\]\)\-—–]))"
        rf"(?:\d+\s*[\.\)]\s*)?"
        rf"\*{{0,2}}\s*"
        rf"(?:{combined})"
        rf"\s*\*{{0,2}}"
        rf"\s*[:\-–—]?\s*"
    )

    # Collect all header matches, filter obvious false positives where the
    # "header" is actually just the section name appearing inside prose
    # (allowed) but not at a plausible section boundary.
    matches = list(header_pattern.finditer(raw_text))

    # De-duplicate: for the same label, keep only the FIRST occurrence that
    # looks like a real section boundary. The first occurrence is almost
    # always the intended header; later occurrences tend to be the LLM
    # echoing the title inside its own content.
    seen = set()
    deduped = []
    for m in matches:
        label = None
        for i, (lbl, _pat) in enumerate(schema):
            if m.group(f"k{i}"):
                label = lbl
                break
        if label is None or label in seen:
            continue
        seen.add(label)
        deduped.append((m, label))

    result = {}

    if not deduped:
        if schema:
            body = _clean_value(raw_text) or ""
            val  = _prettify_body(body) if prettify else body
            if val:
                result[schema[0][0]] = val
        return result

    for idx, (match, matched_label) in enumerate(deduped):
        start = match.end()
        end = deduped[idx + 1][0].start() if idx + 1 < len(deduped) else len(raw_text)
        body = raw_text[start:end].strip()

        body = re.sub(r"\*+", "", body)
        body = re.sub(r"^[\s\-\:•]+", "", body)
        body = body.strip()

        # Leak recovery: if a downstream header snuck into this body
        # (and it WASN'T already caught by dedupe above), chop it.
        cleaned, _leak = _strip_trailing_header_leak(body, schema, matched_label)
        body = cleaned

        if prettify:
            body = _prettify_body(body)
        else:
            body = re.sub(r"\s+", " ", body).strip()

        if not body:
            continue  # drop empty sections entirely — no "—" placeholders

        result[matched_label] = body

    return result


def parse_features(raw_text):
    """Features keep all 8 keys even if empty (table layout depends on them)."""
    parsed = parse_sections(raw_text, FEATURE_KEYS, prettify=False)
    # For features, fill missing keys with em-dash so the UI table stays aligned
    for label, _ in FEATURE_KEYS:
        parsed.setdefault(label, "—")
    return {label: parsed[label] for label, _ in FEATURE_KEYS}


def parse_report(raw_text):
    """Report drops empty sections so the UI doesn't show hollow blocks."""
    parsed = parse_sections(raw_text, REPORT_KEYS, prettify=True)
    # Preserve schema order but only include keys that have real content
    return {label: parsed[label] for label, _ in REPORT_KEYS if label in parsed and parsed[label]}


# ==========================================================
# STANDALONE TEST
# ==========================================================
if __name__ == "__main__":
    test_path = r"C:\Users\ishaa\Downloads\images (1).jpg"

    features, report = nexmed_pipeline(
        test_path, vision_choice="Groq", reasoning_choice="Groq"
    )

    print("\n" + "=" * 50)
    print("PHASE 1 - FEATURES (raw):\n", features)
    print("\n" + "=" * 50)
    print("PHASE 2 - REPORT (raw):\n", report)
    print("\n" + "=" * 50)
    print("PARSED REPORT (for UI):")
    for k, v in parse_report(report).items():
        print(f"\n[{k}]")
        print(v)