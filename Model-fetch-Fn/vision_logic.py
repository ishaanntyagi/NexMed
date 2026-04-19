import os
import base64
import ollama
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# --- HYPER-DETAILED VISION PROMPT ---
# This is stored here to ensure the vision models look for clinical depth.
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

def get_groq_vision(image_path):
    """Cloud-based high-speed feature extraction."""
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    
    with open(image_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode('utf-8')

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct", # Optimized for medical vision
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": CLINICAL_EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                ],
            }
        ],
        temperature=0.0, # Zero temp for clinical consistency
    )
    return response.choices[0].message.content

# def get_local_vision(image_path):
#     """Local extraction using your Ollama Qwen3-VL:2b manifest."""
#     # Using Ollama library to manage your 4GB VRAM/24GB RAM swap efficiently
#     response = ollama.chat(
#         model='qwen3-vl:2b',
#         messages=[{
#             'role': 'user',
#             'content': CLINICAL_EXTRACTION_PROMPT,
#             'images': [image_path]
#         }]
#     )
#     return response['message']['content']

def get_local_vision(image_path):
    response = ollama.chat(
        model='qwen3-vl:2b',
        keep_alive=0,  # This forces the model to unload immediately 
        messages=[{
            'role': 'user',
            'content': CLINICAL_EXTRACTION_PROMPT,
            'images': [image_path]
        }]
    )
    return response['message']['content']

def vision_extractor_factory(image_path, model_choice="Groq"):
    """
    Main entry point for the Frontend 'Start Feature Extraction' button.
    """
    if model_choice == "Groq":
        return get_groq_vision(image_path)
    else:
        return get_local_vision(image_path)
    
if __name__ == "__main__":
    test_path = r"C:\Users\ishaa\Downloads\images (1).jpg"
    model_to_test = "Qwen"  # Change this to "Groq" or "Qwen"
    
    # FIX: Use a f-string to reflect the actual choice
    print(f" Testing Vision Factory with {model_to_test}...") 
    
    result = vision_extractor_factory(test_path, model_choice=model_to_test) 
    print("\n--- RESULTS ---")
    print(result)






    