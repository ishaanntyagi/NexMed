import torch
import os
os.environ["ACCELERATE_MIXED_PRECISION"] = "fp16"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer, SFTConfig 
from datasets import load_dataset
import os

# ===============================
# 🔥 GLOBAL SETTINGS (GTX 1650 SAFE)
# ===============================
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.set_float32_matmul_precision("high")

print("-" * 30)
print(f"DEVICE: {torch.cuda.get_device_name(0)}")
print("MODE: FP16 ONLY (BF16 DISABLED)")
print("-" * 30)

# ===============================
# PATHS
# ===============================
px_model_path = "./qwen3.5_2b_hf"
py_data_path  = "data.jsonl"
pz_output_dir = "./nexmed_final_gpu"

# ===============================
# 4-BIT CONFIG
# ===============================
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,  # ✅ FP16 compute
    bnb_4bit_use_double_quant=True,
)

# ===============================
# TOKENIZER
# ===============================
tokenizer = AutoTokenizer.from_pretrained(px_model_path)
tokenizer.pad_token = tokenizer.eos_token

# ===============================
# MODEL LOAD (DO NOT TOUCH DTYPE AFTER THIS)
# ===============================
model = AutoModelForCausalLM.from_pretrained(
    px_model_path,
    quantization_config=bnb_config,
    device_map="auto",
    low_cpu_mem_usage=True
)

# ===============================
# PREPARE MODEL (NO CASTING!)
# ===============================
model = prepare_model_for_kbit_training(model)

# ===============================
# LORA CONFIG
# ===============================
peft_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    task_type="CAUSAL_LM"
)

model = get_peft_model(model, peft_config)

# ===============================
# TRAIN CONFIG (STABLE)
# ===============================
sft_config = SFTConfig(
    output_dir="./tweak_results",
    dataset_text_field="text",
    max_length=512,

    per_device_train_batch_size=1,
    gradient_accumulation_steps=4,

    num_train_epochs=3,
    learning_rate=2e-4,

    fp16=True,        # ✅ REQUIRED
    bf16=False,       # ❌ MUST BE FALSE

    fp16_full_eval=True,
    torch_compile=False,

    optim="adamw_torch",
    gradient_checkpointing=True,
    logging_steps=1,
    save_strategy="epoch",
    report_to="none",
    gradient_checkpointing_kwargs={"use_reentrant": False}
)

# ===============================
# DATASET
# ===============================
dataset = load_dataset("json", data_files=py_data_path, split="train")

# ===============================
# TRAINER
# ===============================
trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    args=sft_config,
    processing_class=tokenizer
)

print(" Training started... If it passes 1%, you're good!")

trainer.train()

# ===============================
# SAVE MODEL
# ===============================
trainer.save_model(pz_output_dir)

print(f"\n SUCCESS! Model saved at: {os.path.abspath(pz_output_dir)}")