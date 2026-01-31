<h1>ComfyUI "Combined Workflow"</h1>

Please check CivitAI for the older versions of this workflow at [https://civitai.com/models/2149956](https://civitai.com/models/2149956)

A few notes on requirements:
1. the workflow loads all base models from the `Load Checkpoint with name` node. This is due to the need for the `model_name` field to be available to save CiviAI compatible metadata. One method to enable this is to create the `extra_model_paths.yaml` file to use with ComfyUI. Details on a similar process can be found [https://github.com/mmartial/ComfyUI-Nvidia-Docker/wiki/Stability-Matrix-integration](https://github.com/mmartial/ComfyUI-Nvidia-Docker/wiki/Stability-Matrix-integration), the process is the same with a different target  (adapt `/ComfyUI_models_folder` and `Path_to` to match your setup):
```yaml
comfy_extend:
  base_path: /ComfyUI_models_folder
  checkpoints: |
    diffusion_models
```
Make sure to add `--extra-model-paths-config=Path_to/extra_model_paths.yaml` to your ComfyUI command line arguments. 
2. Detailers rely on Ultralytics models. Manual configuration is needed as detailed [https://github.com/ltdrdata/ComfyUI-Impact-Subpack](https://github.com/ltdrdata/ComfyUI-Impact-Subpack)

_(Looking for the older workflows? You can find them in the [Older](Older) folder)_

This folder contains a "Combined Workflow" (over 800KB) that does SDXL, Pony, Illustrious, Flux1D, Qwen and ZImageTurbo generation with an optional prompt extension using Ollama and Wildcards processing.

It will generate an upscaled 16MP image as the final result while staying as close as possible to the original generation and produce CivitAI compatible metadata for each stage of the image generation.
- Stage 1: Generate the regular image, pass it to a selector (can be bypassed for batch generation)
- Stage 2: Upscale to 4MP using HiResFix
- Stage 3: Use Ultimate SD Upscaler (No Upscale) to redefine the components of 4MP image using the original model and loras' specific characteristics. Faces, Hands and Eyes Detailer are then used on the resulting image.
- Stage 4: That result is sent to SeedVR2 to generate the final 16MP image and a color matching step is performed to make it as close as possible as the initial upscaled image.

The workflow contains a "READ ME FIRST" section that details some about how it came to be, what it does and how to use it. Please refer to it for more information.

FYSA: list of used custom nodes:
```bash
❯ fgrep cnr_id gkr-combined_v6.json | tr -s " " | sort | cut -d ":" -f 2 | uniq
 "cg-image-filter",
 "comfy-core",
 "comfy-image-saver",
 "ComfyLiterals",
 "ComfyMath",
 "ComfyUI_ADV_CLIP_emb",
 "ComfyUI_Comfyroll_CustomNodes",
 "comfyui_resolutionselectorplus",
 "comfyui_ultimatesdupscale",
 "comfyui-custom-scripts",
 "comfyui-easy-use",
 "comfyui-fbcnn",
 "comfyui-image-saver",
 "comfyui-impact-pack",
 "comfyui-impact-subpack",
 "comfyui-inspire-pack",
 "comfyui-kjnodes",
 "comfyui-lora-manager",
 "comfyui-ollama",
 "RES4LYF",
 "rgthree-comfy",
 "seedvr2_videoupscaler",
 ```

 Note: using the `nightly` version of [LoraManager](https://github.com/willmiao/ComfyUI-Lora-Manager) (once 0.9.14 is out, this message will be obsolete)
