# comfy-pipeline-tools

[![CI](https://github.com/Nebularumar/comfy-pipeline-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/Nebularumar/comfy-pipeline-tools/actions/workflows/ci.yml)

How I run a local ComfyUI pipeline for a recurring AI character: still images, edits, upscaling and
short video clips. It has the workflows and the generic scripts, the backup and lock files I use
so I can rebuild everything without internet, and a small MCP server so an assistant can launch
jobs without getting a shell.

**This is my personal setup, not a polished product.** It works on my machine. Comments and some
messages in the code are in Spanish. Take what's useful.

## Hardware and versions

- CPU: Intel i5-12600K
- RAM: 32 GB DDR5
- GPU: RTX 5060 Ti 16 GB
- OS: Linux Mint, Python 3.10.12 (system Python, no venv for ComfyUI)
- ComfyUI 0.27.0 (commit `51bf508`), torch 2.12.0 with CUDA 13.0
- Exact versions of every package and custom node are in `locks/`

Everything is sized for 16 GB of VRAM. Image and video models load entirely on the GPU. The
one exception is `make_wf_seedvr2.py`, which by default swaps 16 DiT blocks to the CPU so it
can upscale to 4096 px (set `SEEDVR2_BLOCKS=0` to keep it all on the GPU at lower resolutions).

## How the pieces fit

| Step | Tool | Where it runs |
|---|---|---|
| The character's face | Z-Image Turbo + a LoRA trained on the character (trigger like `my_character`) | Local, ComfyUI |
| Editing a finished photo (change clothes, put the character into another photo) | Nano Banana (2.1, Pro or 2) with reference images | fal.ai (paid) |
| Upscaling | SeedVR2 3B FP8 | Local, ComfyUI |
| Video | Wan 2.2 (image-to-video, first/last frame, VACE) locally, or Kling through fal.ai | Local / fal.ai |
| Cutting out a subject | BiRefNet | Local, ComfyUI |

The LoRA is what keeps the face the same from image to image. Everything after that (edits,
upscale, video) starts from those images. My LoRA, dataset and character files are not in this
repo.

Some things I learned along the way:
- Z-Image Turbo runs at CFG 1.0, so the negative prompt does nothing there.
- Running SeedVR2 on the whole image cracks smooth skin. I only use its output on hair, beard,
  brows and eyes, and keep the base image for the skin.
- Nano Banana keeps the identity well when it edits a photo of the character. When it builds a
  new scene from references only, the face drifts more.

## Models

Nothing is included. Download links:

| File | Folder in `ComfyUI/models/` | Link |
|---|---|---|
| `z_image_turbo_bf16.safetensors` (or `_nvfp4`) | `diffusion_models/` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/tree/main/split_files/diffusion_models) |
| `qwen_3_4b.safetensors` | `text_encoders/` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/tree/main/split_files/text_encoders) |
| `ae.safetensors` | `vae/` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/tree/main/split_files/vae) |
| `seedvr2_ema_3b_fp8_e4m3fn.safetensors`, `ema_vae_fp16.safetensors` | `SEEDVR2/` | [numz/SeedVR2_comfyUI](https://huggingface.co/numz/SeedVR2_comfyUI/tree/main) |
| `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`, `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | `diffusion_models/` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files/diffusion_models) |
| `wan2.2_i2v_lightx2v_4steps_lora_v1_{high,low}_noise.safetensors`, `wan2.2_t2v_lightx2v_4steps_lora_v1.1_{high,low}_noise.safetensors` | `loras/` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files/loras) |
| `wan_2.1_vae.safetensors` | `vae/` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files/vae) |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `text_encoders/` | [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/tree/main/split_files/text_encoders) |
| `Wan2.2-VACE-Fun-A14B-{high,low}-noise-Q4_K_M.gguf` | `unet/` | [QuantStack/Wan2.2-VACE-Fun-A14B-GGUF](https://huggingface.co/QuantStack/Wan2.2-VACE-Fun-A14B-GGUF/tree/main) |
| `birefnet.safetensors` | `background_removal/` | [Comfy-Org/BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet/tree/main/background_removal) |

Custom nodes needed: [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) (VACE GGUF),
[comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) (pose for VACE) and
[ComfyUI-SeedVR2_VideoUpscaler](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler). Exact
commits are in `locks/nodes.lock`.

## What's in the repo

| Folder | Contents |
|---|---|
| `workflows/` | ComfyUI API-format workflows (see below) |
| `scripts/` | `cf_client.py`: minimal ComfyUI API client (send a workflow, wait, list the images). `make_wf_seedvr2.py`: writes the SeedVR2 upscale workflow |
| `video/` | `kling_video.py`: Kling image-to-video through fal.ai (multi-shot, several clips with different seeds, saves the billing headers). `rife_60fps.py`: frame interpolation with rife-ncnn-vulkan that never interpolates across a cut |
| `backup/` | Backup to a second disk and offline restore (see below) |
| `locks/` | `requirements.lock` (pip freeze) and `nodes.lock` (ComfyUI and custom node commits) |
| `mcp/` | `pipeline_runner.py`: local MCP server with an allowlist |
| `systemd/` | User service and timer for the weekly backup |
| `tests/` | pytest suite for the MCP runner and script syntax |

### Workflows

They're in API format, with `@NAME@` placeholders that a script replaces before sending them to
ComfyUI. The placeholder names are in Spanish: `@PROMPT@`, `@NEGATIVO@` (negative), `@INICIO@`
(start frame), `@FINAL@` (end frame), `@W@`, `@H@`, `@FRAMES@`, `@SEED@`, `@PREFIJO@` (output
prefix).

| File | What it does |
|---|---|
| `wan22_i2v.json` | Wan 2.2 image-to-video, 14B fp8 high/low noise with the lightx2v 4-step LoRAs (4 steps, CFG 1) |
| `wan22_flf.json` | Same, but with a first and a last frame (`@INICIO@` → `@FINAL@`) |
| `wan22_vace.json` | Wan 2.2 VACE Fun (GGUF Q4_K_M): first frame + reference image (`@REF@`) + the motion of a control video (`@CONTROL@`, turned into a DWPose skeleton). `@UNET_HIGH@`/`@UNET_LOW@` are the two GGUF files and `@FRAMES_1@` is frames minus one. It also has a Depth Anything node that isn't connected to the output, so ComfyUI skips it |
| `recorte_birefnet.json` | BiRefNet background removal; saves the mask |

Filling one in:

```python
import json
wf = open("workflows/wan22_i2v.json").read()
for k, v in {"PROMPT": "she turns her head and smiles", "NEGATIVO": "blurry, distorted",
             "INICIO": "start.png", "PREFIJO": "clip"}.items():
    wf = wf.replace(f"@{k}@", v)
for k, v in {"W": 832, "H": 480, "FRAMES": 81, "SEED": 1234}.items():
    wf = wf.replace(f'"@{k}@"', str(v))            # numbers go in without quotes
json.dump(json.loads(wf), open("wf_ready.json", "w"))
# python3 scripts/cf_client.py wf_ready.json
```

The start image has to be in `ComfyUI/input/` first.

### Upscaling with SeedVR2

```bash
# the image has to be in ComfyUI/input/ ; args: image [short side, def 4096] [seed] [out_json]
python3 scripts/make_wf_seedvr2.py photo.png 3840
python3 scripts/cf_client.py ~/wf_seedvr2.json
```

### Kling (fal.ai)

Put your fal key in `~/.fal_key`. It costs money per clip.

```bash
python3 video/kling_video.py start.png "she walks towards the camera" --dur 5 --tier pro
```

## Backup and restore

```bash
cp backup/config.example.json backup/config.json   # disk UUID, paths, models with their SHA256
bash backup/backup.sh                              # stops if the disk isn't really mounted
export PIPELINE_BACKUP_DIR=/mnt/backup/backup_pipeline
bash backup/restore.sh --comprobar                 # check the backup has everything
bash backup/restore.sh /mnt/disk/ComfyUI_new       # rebuild without internet
```

`backup.sh` uses rsync to a second disk, checks model SHA256s, keeps hard-link snapshots and git
bundles. `restore.sh` rebuilds ComfyUI, the custom nodes and the Python environment from the
backup. `wheels.sh` downloads the exact wheels from `locks/requirements.lock` so the restore
doesn't need pip to reach the internet.

For the weekly timer, copy `systemd/pipeline-backup.*` to `~/.config/systemd/user/`, fix the path
in the service, and run `systemctl --user enable --now pipeline-backup.timer`.

## MCP server (`mcp/`)

This lets an assistant start generation jobs without a shell. Commands run through `subprocess`
with an argument list. Pipes, `;`, `&&`, redirections, `$` and backticks are rejected.

- **Allowed scripts** (any arguments): only those in `mcp/allowlist.json` (copy the `.example`),
  plus batch files matching `batch_dir`/`batch_glob`, minus `batch_exclude`.
- **Read-only**: `ls`, `cat`, `head`, `tail`, `wc`, `du`, `find` (no `-delete`/`-exec`),
  `nvidia-smi`, `git status/log/diff`. Every path is resolved (`~`, `..`, symlinks) and has to stay
  inside the pipeline root.
- **Writing**: only `write_batch_file`, which creates new `.csv`/`.json` files in `batch_dir` and
  never overwrites.
- `start_job`: at most 2 jobs at a time, 3 hours each. Job state is read from disk.

**Real limits:** arguments to allowed scripts are not validated, and those scripts do whatever
their code does. It's only safe as long as nobody edits the scripts on the list.

```bash
bash mcp/instalar.sh   # creates the venv and registers the server in the desktop app's MCP config (backs it up first)
```

## Requirements

Linux, Python 3.10, rsync and git. ComfyUI with the models and nodes above for the workflows.
`video/` needs a fal.ai key in `~/.fal_key`, and `rife_60fps.py` needs rife-ncnn-vulkan.

## Tests

```bash
pip install pytest
python -m pytest
```

They cover the MCP runner (shell rejection, allowlist, read-only paths, writing, the 2-job and
3-hour limits, stopping jobs, state from disk) and the syntax of every script. CI runs them on
Python 3.10 and 3.12 and runs ShellCheck (errors only).

## License

MIT (see `LICENSE`).
