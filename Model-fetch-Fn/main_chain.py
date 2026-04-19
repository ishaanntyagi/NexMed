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
# PHASE 1 PROMPT - VISION (The Eyes)
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
8. CONFIDENCE SCORE: 0-100% based on image clarity.

Provide ONLY the technical extraction. Do not provide patient advice.
"""


# ==========================================================
# PHASE 2 PROMPT - REASONING (The Brain)
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
# MARKDOWN -> DICT PARSERS
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
    """
    AGGRESSIVELY break run-on prose into one-sentence-per-line bullets.

    The LLM often emits:
        "Point A. - Point B. - Point C. --- 2. NEXT SECTION..."
    We want:
        "- Point A.
         - Point B.
         - Point C."

    Strategy:
      1. Strip markdown noise (**, ---).
      2. Split on any ' - ' / ' — ' / ' • ' that looks like a bullet separator.
      3. Also split on sentence-ending period + capital-letter boundary as a
         fallback when the LLM uses no dashes at all.
      4. Normalize every resulting line into "- <sentence>".
    """
    # 1. Kill markdown noise
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"-{3,}", " ", text)          # "---" dividers -> space
    text = re.sub(r"[ \t]+", " ", text).strip()

    # 2. Break inline bullets. Any dash/bullet with whitespace on BOTH sides
    #    that sits in the middle of text is almost certainly a list separator.
    text = re.sub(r"(?<=\S)\s+[-–—•]\s+(?=\S)", "\n", text)

    # 3. Also break "Sentence. Next sentence." into two lines when the LLM
    #    refuses to use dashes at all. Requires a period followed by a space
    #    and a capital letter (not an abbreviation like "e.g.").
    text = re.sub(r"(?<=[a-z0-9\]\)])\.\s+(?=[A-Z][a-z])", ".\n", text)

    # 4. Break "Label: value. Next label: value." patterns that the LLM
    #    sometimes chains (e.g., "Displacement: None. Angulation: 5°.")
    text = re.sub(r";\s+", "\n", text)

    # Normalize each line -> prefix with "- "
    lines = [ln.strip(" -•\t") for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    # Drop duplicate "SECTION NAME:" echoes at the start of a body
    # (happens when LLM repeats the header inside its own content)
    lines = [ln for ln in lines if not re.match(r"^[A-Z][A-Z\s\.\/\-]{4,}:?\s*$", ln)]

    if not lines:
        return ""

    # If the body is short (1 sentence), return plain paragraph (no bullet)
    if len(lines) == 1 and len(lines[0]) < 140:
        return lines[0]

    return "\n".join(f"- {ln}" for ln in lines)


def parse_sections(raw_text, schema, prettify=False):
    combined = "|".join(f"(?P<k{i}>{pat})" for i, (_, pat) in enumerate(schema))
    header_pattern = re.compile(
        rf"(?i)(?:(?<=^)|(?<=[\s.\]\)\-—–]))"
        rf"(?:\d+\s*[\.\)]\s*)?"
        rf"\*{{0,2}}\s*"
        rf"(?:{combined})"
        rf"\s*\*{{0,2}}"
        rf"\s*[:\-–—]?\s*"
    )

    matches = list(header_pattern.finditer(raw_text))
    result = {}

    if not matches:
        if schema:
            body = _clean_value(raw_text) or "No data."
            result[schema[0][0]] = _prettify_body(body) if prettify else body
        for label, _ in schema:
            result.setdefault(label, "—")
        return {label: result[label] for label, _ in schema}

    for idx, match in enumerate(matches):
        matched_label = None
        for i, (label, _pat) in enumerate(schema):
            if match.group(f"k{i}"):
                matched_label = label
                break
        if matched_label is None:
            continue

        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
        body = raw_text[start:end].strip()

        body = re.sub(r"\*+", "", body)
        body = re.sub(r"^[\s\-\:•]+", "", body)
        body = body.strip()

        if prettify:
            body = _prettify_body(body)
        else:
            body = re.sub(r"\s+", " ", body).strip()

        if not body:
            body = "—"

        if matched_label in result and len(result[matched_label]) >= len(body):
            continue
        result[matched_label] = body

    for label, _ in schema:
        result.setdefault(label, "—")

    return {label: result[label] for label, _ in schema}


def parse_features(raw_text):
    return parse_sections(raw_text, FEATURE_KEYS, prettify=False)


def parse_report(raw_text):
    return parse_sections(raw_text, REPORT_KEYS, prettify=True)


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