"""
main_chain_rag.py
─────────────────
Agentic RAG consultation module for NexMed AI.

Exposes ONE entrypoint that app.py will import:

    run_agent(question: str, patient: dict, kb: str = "general") -> dict

Return shape:
    {
        "answer":    str,            # final assistant reply
        "trace":     list[dict],     # ordered tool calls: [{tool, args, result_preview}, ...]
        "citations": list[dict],     # [{file, chunk_preview}, ...]
    }

Design:
- 3 tools, chosen by Groq function-calling:
    1. retrieve_knowledge(query)   → semantic search over /knowledge markdown
    2. read_patient_context()      → structured intake fields
    3. answer_directly()           → LLM uses own knowledge; no grounding
- One while-loop handles multi-step tool use (up to MAX_STEPS).
- FAISS index + chunks are built once at import and cached to disk
  so Flask restarts stay fast.
"""

from __future__ import annotations

import os
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
MODULE_DIR     = Path(__file__).resolve().parent
KNOWLEDGE_DIR  = MODULE_DIR / "knowledge"
INDEX_CACHE    = MODULE_DIR / ".rag_cache"
INDEX_CACHE.mkdir(exist_ok=True)

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
GROQ_MODEL       = "llama-3.1-8b-instant"

CHUNK_SIZE    = 450    # characters, generous for short markdown
CHUNK_OVERLAP = 80
TOP_K         = 3
MAX_STEPS     = 4      # safety cap on tool-call loop

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY missing — add it to .env before importing this module.")

_groq = Groq(api_key=GROQ_API_KEY)


# ─────────────────────────────────────────────────────────────
# INDEX BUILD (runs once at import, cached to disk)
# ─────────────────────────────────────────────────────────────
def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Naive char-window chunker. Fine for short, clean markdown."""
    text = text.strip()
    if len(text) <= size:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i + size])
        i += size - overlap
    return chunks


def _load_corpus() -> list[dict]:
    """Read every .md file in /knowledge, split into chunks."""
    docs: list[dict] = []
    if not KNOWLEDGE_DIR.exists():
        raise FileNotFoundError(f"Knowledge folder missing: {KNOWLEDGE_DIR}")

    for md_file in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        for idx, chunk in enumerate(_chunk_text(text)):
            docs.append({
                "file":  md_file.name,
                "chunk_id": idx,
                "text":  chunk,
            })
    return docs


def _build_or_load_index() -> tuple[faiss.IndexFlatIP, list[dict], SentenceTransformer]:
    """Build FAISS index over all chunks, or load from disk if cached."""
    index_path  = INDEX_CACHE / "faiss.index"
    chunks_path = INDEX_CACHE / "chunks.pkl"

    embedder = SentenceTransformer(EMBED_MODEL_NAME)

    if index_path.exists() and chunks_path.exists():
        index = faiss.read_index(str(index_path))
        with open(chunks_path, "rb") as f:
            chunks = pickle.load(f)
        return index, chunks, embedder

    # Build fresh
    chunks = _load_corpus()
    if not chunks:
        raise RuntimeError(f"No knowledge files found in {KNOWLEDGE_DIR}")

    texts = [c["text"] for c in chunks]
    vecs  = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    vecs  = np.asarray(vecs, dtype="float32")

    index = faiss.IndexFlatIP(vecs.shape[1])   # cosine via normalized inner product
    index.add(vecs)

    faiss.write_index(index, str(index_path))
    with open(chunks_path, "wb") as f:
        pickle.dump(chunks, f)

    return index, chunks, embedder


# Build on import
_INDEX, _CHUNKS, _EMBEDDER = _build_or_load_index()


# ─────────────────────────────────────────────────────────────
# TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────
def _tool_retrieve_knowledge(query: str, kb: str = "general") -> dict:
    """Semantic search across all markdown chunks. kb reserved for future filtering."""
    if not query or not query.strip():
        return {"hits": [], "note": "empty query"}

    q_vec = _EMBEDDER.encode([query], normalize_embeddings=True)
    q_vec = np.asarray(q_vec, dtype="float32")
    scores, idxs = _INDEX.search(q_vec, TOP_K)

    hits = []
    for score, i in zip(scores[0], idxs[0]):
        if i < 0:
            continue
        c = _CHUNKS[i]
        hits.append({
            "file":  c["file"],
            "score": float(score),
            "text":  c["text"],
        })
    return {"hits": hits}


def _tool_read_patient_context(patient: dict) -> dict:
    """Return intake record as a flat, readable dict for the LLM to reason over."""
    if not patient:
        return {"note": "no patient context available"}
    # Filter out empty strings / False / None for cleaner prompt
    clean = {k: v for k, v in patient.items() if v not in ("", None, False)}
    return {"patient": clean}


def _tool_answer_directly() -> dict:
    """Signal tool. The LLM will write the answer from general knowledge."""
    return {"note": "proceed with direct answer using general clinical reasoning"}


# ─────────────────────────────────────────────────────────────
# TOOL SCHEMAS (Groq function-calling format)
# ─────────────────────────────────────────────────────────────
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "retrieve_knowledge",
            "description": (
                "Search the local orthopedic knowledge base (fracture classification, "
                "management, healing, surgical indications, red flags). "
                "Use this when the question is about clinical facts, guidelines, or concepts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise search query capturing the clinical concept.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_patient_context",
            "description": (
                "Return the structured intake record for this patient (age, sex, injury "
                "site, mechanism, symptoms, pain level, red-flag checkboxes, etc.). "
                "Use this whenever the question depends on patient specifics."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_directly",
            "description": (
                "Signal that no retrieval or patient lookup is needed and you will answer "
                "from general medical knowledge. Use sparingly — only for greetings, "
                "clarifications, or clearly off-topic questions."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are NexMed AI, a clinical consultation assistant for orthopedic cases.

You have three tools:
  • retrieve_knowledge — search the local orthopedic knowledge base
  • read_patient_context — read the current patient's intake record
  • answer_directly — skip tools and answer from general knowledge (rare)

Rules:
  1. For any clinical question, FIRST call retrieve_knowledge with a focused query.
  2. If the question is about THIS patient specifically, ALSO call read_patient_context.
  3. You may call multiple tools in sequence. When you have enough information, write the final answer.
  4. In the final answer:
     - Be concise and structured (short paragraphs or bullet points).
     - Ground clinical claims in the retrieved knowledge. Cite filenames inline like [01_fracture_classification.md].
     - If retrieval returned nothing relevant, say so honestly.
     - Never invent patient fields that were not provided.
  5. Always end with a one-line reminder: "For research use only. Not a medical advice."
"""


# ─────────────────────────────────────────────────────────────
# AGENT LOOP — public entrypoint
# ─────────────────────────────────────────────────────────────
def run_agent(question: str, patient: dict | None = None, kb: str = "general") -> dict:
    """
    Run the agent for a single user turn.

    Args:
        question: user's chat message
        patient:  intake record dict (session.patient from the frontend)
        kb:       knowledge base key (reserved; currently unused for filtering)

    Returns:
        {"answer": str, "trace": list[dict], "citations": list[dict]}
    """
    patient = patient or {}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": question},
    ]

    trace: list[dict] = []
    citations: list[dict] = []
    seen_files: set[str] = set()

    for step in range(MAX_STEPS):
        response = _groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
            temperature=0.2,
        )
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        # No tool calls → this is the final answer
        if not tool_calls:
            return {
                "answer":    msg.content or "",
                "trace":     trace,
                "citations": citations,
            }

        # Must append the assistant message (with tool_calls) before tool responses
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id":   tc.id,
                    "type": "function",
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        # Execute each tool call
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "retrieve_knowledge":
                result = _tool_retrieve_knowledge(
                    query=args.get("query", ""),
                    kb=kb,
                )
                for hit in result.get("hits", []):
                    f = hit["file"]
                    if f not in seen_files:
                        seen_files.add(f)
                        citations.append({
                            "file":           f,
                            "chunk_preview":  hit["text"][:160].replace("\n", " ") + "…",
                            "score":          round(hit["score"], 3),
                        })
                preview = f"{len(result.get('hits', []))} chunks retrieved"

            elif name == "read_patient_context":
                result = _tool_read_patient_context(patient)
                preview = f"{len(result.get('patient', {}))} fields returned"

            elif name == "answer_directly":
                result = _tool_answer_directly()
                preview = "no retrieval"

            else:
                result = {"error": f"unknown tool: {name}"}
                preview = "error"

            trace.append({
                "step":    step + 1,
                "tool":    name,
                "args":    args,
                "preview": preview,
            })

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "name":         name,
                "content":      json.dumps(result)[:4000],  # guard against huge payloads
            })

    # Loop exhausted without a clean answer → return what we have
    return {
        "answer":    "I was unable to finalize a response within the tool-call limit. Please rephrase your question.",
        "trace":     trace,
        "citations": citations,
    }