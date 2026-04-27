"""
Hybrid LLM helper: Groq (cloud) + Ollama gemma4:e4b (local).
Ollama auto-unloads after 2min idle to save RAM.
"""

import os
import json
import requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ============== Groq ==============
_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

GROQ_SMALL = "llama-3.1-8b-instant"
GROQ_BIG   = "llama-3.3-70b-versatile"

def groq_complete(system_prompt: str, user_prompt: str,
                  model: str = GROQ_SMALL,
                  temperature: float = 0.1,
                  stream: bool = False):
    """Groq call. String (non-stream) or generator (stream)."""
    resp = _groq.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        stream=stream,
    )
    if not stream:
        return resp.choices[0].message.content.strip()

    def gen():
        for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    return gen()


# ============== Ollama (gemma4:e4b) ==============
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma4:e4b"
OLLAMA_KEEP_ALIVE = "2m"   # auto-unload after 2 min idle = saves RAM

def ollama_complete(system_prompt: str, user_prompt: str,
                    temperature: float = 0.1,
                    stream: bool = False):
    """Local gemma4:e4b. Auto-unloads after 2min idle."""
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": full_prompt,
        "stream": stream,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {"temperature": temperature, "num_predict": 1024},
    }

    if not stream:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        return r.json()["response"].strip()

    def gen():
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=120) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if "response" in data:
                    yield data["response"]
    return gen()


def ollama_unload():
    """Force-unload gemma4 from RAM right now."""
    requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "keep_alive": 0
    }, timeout=10)


# ============== JSON parser ==============
def parse_json_safe(text: str):
    """Strip fences, parse JSON. Returns {} on fail."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    for s, e in [("{", "}"), ("[", "]")]:
        i, j = text.find(s), text.rfind(e)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j+1])
            except json.JSONDecodeError:
                continue
    return {}