from llm_helper import (
    groq_complete, ollama_complete, ollama_unload,
    GROQ_SMALL, GROQ_BIG
)

print("--- Groq small ---")
print(groq_complete("Be concise.", "Say hi in 5 words.", model=GROQ_SMALL))

print("\n--- Groq big ---")
print(groq_complete("Be concise.", "Explain MI in 1 sentence.", model=GROQ_BIG))

print("\n--- Ollama gemma4:e4b ---")
print(ollama_complete("Be concise.", "Say hi in 5 words."))

print("\n--- Unloading Ollama ---")
ollama_unload()
print("Done. Gemma4 should be out of RAM now.")