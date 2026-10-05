#!/usr/bin/env python3
"""PASE 2: upscale SeedVR2 (3B fp8) de una imagen base. Se ejecuta APARTE del pase de
generacion para no tener FLUX.2 + SeedVR2 en VRAM a la vez (evita OOM en 16GB).

Uso:
  python3 make_wf_seedvr2.py <nombre_en_input.png> [resolution] [seed] [out_json]
La imagen debe estar en ~/ComfyUI/input/ (LoadImage la lee de ahi).
resolution = lado corto objetivo (def 4096). blocks_to_swap se ajusta para caber en 16GB.
"""
import json, sys, random

if len(sys.argv) < 2:
    print("ERROR: uso: make_wf_seedvr2.py <img_en_input.png> [resolution] [seed] [out_json]")
    sys.exit(1)

img_name = sys.argv[1]
resolution = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].strip() else 4096
seed = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].strip() else random.randint(1, 2**31 - 1)
out_json = sys.argv[4] if len(sys.argv) > 4 else str(__import__("pathlib").Path.home() / "wf_seedvr2.json")

# blocks_to_swap: descarga bloques del DiT a CPU para caber en 16GB al hacer 4x.
# 0 = todo en GPU (peta a 4096). Subir si OOM; bajar si va sobrado (mas rapido).
BLOCKS_TO_SWAP = int(__import__("os").environ.get("SEEDVR2_BLOCKS", "16"))

wf = {
    "1": {"class_type": "LoadImage", "inputs": {"image": img_name}},
    # DiT con blockswap: clave para 4x en 16GB. offload a CPU + cache off para liberar al terminar.
    "2": {"class_type": "SeedVR2LoadDiTModel", "inputs": {
        "model": "seedvr2_ema_3b_fp8_e4m3fn.safetensors", "device": "cuda:0",
        "blocks_to_swap": BLOCKS_TO_SWAP, "swap_io_components": False,
        "offload_device": "cpu", "cache_model": False, "attention_mode": "sdpa"}},
    # VAE con decode/encode tiled: evita OOM en el (de)codificado de la imagen grande.
    "3": {"class_type": "SeedVR2LoadVAEModel", "inputs": {
        "model": "ema_vae_fp16.safetensors", "device": "cuda:0",
        "encode_tiled": True, "encode_tile_size": 1024, "encode_tile_overlap": 128,
        "decode_tiled": True, "decode_tile_size": 1024, "decode_tile_overlap": 128,
        "offload_device": "cpu", "cache_model": False}},
    "4": {"class_type": "SeedVR2VideoUpscaler", "inputs": {
        "image": ["1", 0], "dit": ["2", 0], "vae": ["3", 0],
        "seed": seed, "resolution": resolution, "max_resolution": 0,
        "batch_size": 1, "uniform_batch_size": False, "color_correction": "lab"}},
    "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "t2i_flux2"}},
}

json.dump(wf, open(out_json, "w"), indent=1)
print(f"workflow SeedVR2 escrito -> {out_json}  (img={img_name} res={resolution} blocks_swap={BLOCKS_TO_SWAP})")
