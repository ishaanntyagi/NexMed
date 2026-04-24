from huggingface_hub import snapshot_download
import os

# 1. Destination folder
model_dir = "./qwen3.5_2b_hf"

print(" Initializing download of Qwen 3.5 - 2B (approx 4-5GB)...")

# 2. The Actual Download Logic
snapshot_download(
    repo_id="Qwen/Qwen3.5-2B", 
    local_dir=model_dir,
    local_dir_use_symlinks=False, # Important for Windows/OneDrive
    ignore_patterns=["*.msgpack", "*.h5", "*.ot"]
)

print(f" DONE! Real weights are now in: {os.path.abspath(model_dir)}")