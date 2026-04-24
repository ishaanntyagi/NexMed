import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

model_path = "./qwen3.5_2b_hf"

print("Loading model into RAM...")

tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    torch_dtype=torch.bfloat16,
    low_cpu_mem_usage=True,
    device_map="cpu"
)

print(" Qwen-3.5-2b-P , Model loaded successfully! NEXMED Engine Ready.")