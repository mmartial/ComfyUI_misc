<h1>ComfyUI "Combined Workflow"</h1>

# Full version

Please check CivitAI for the older versions of this workflow at [https://civitai.com/models/2149956](https://civitai.com/models/2149956)

This folder contains a "Combined Workflow" (over 2M and close to 800 nodes) that does SDXL, Pony, Illustrious, Flux1D, Qwen,ZImage Turbo/Base, Anima, Flux.2 Klein and Krea 2 Turbo generations with an optional prompt extension using LLMs and Wildcards processing.

It will generate an upscaled 16MP image as the final result while staying as close as possible to the original generation and produce CivitAI compatible metadata for each stage of the image generation.
- Stage 1: Generate the regular image using [Detail Daemon](https://github.com/Jonseed/ComfyUI-Detail-Daemon) sampler, pass it to a selector (can be bypassed for batch generation)
- Stage 2: Upscale to 4MP using a model
- Stage 3: Use "Ultimate SD Upscale" to redefine the components of the 4MP image using the original model and LoRAs' specific characteristics. An optional Flux.1D resampler is available followed by a Cleanup stage, then Faces, Hands and Eyes Detailer are then used on the resulting image.
- Stage 4: That result is sent to SeedVR2 or HighresFix to generate the final 16MP image and a color matching step is performed to make it as close as possible as the initial upscaled image.

The workflow contains a "READ ME FIRST" section that details some about how it came to be, what it does and how to use it. Please refer to it for more information.

FYSA: list (and count) of used custom nodes:
```bash
❯ fgrep cnr_id gkr_combined_v9.1.json | tr '[:upper:]' '[:lower:]' | tr -s " " | sort | cut -d ":" -f 2 | uniq -c
   1  "cg-image-filter",
 271  "comfy-core",
   4  "comfy-image-saver",
   2  "comfy-mtb",
  21  "comfyliterals",
   1  "comfyui_controlnet_aux",
  11  "comfyui_essentials",
   9  "comfyui_llm_party",
   2  "comfyui_ultimatesdupscale",
  17  "comfyui-crystools",
  78  "comfyui-custom-scripts",
   9  "comfyui-detail-daemon",
  91  "comfyui-easy-use",
   5  "comfyui-fbcnn",
  64  "comfyui-image-saver",
  41  "comfyui-impact-pack",
   1  "comfyui-inspire-pack",
  40  "comfyui-kjnodes",
  18  "comfyui-lora-manager",
   6  "comfyui-ollama",
   4  "comfyui-qwenvl",
   1  "comfyui-resolution-master",
  13  "comfyui-rmbg",
  87  "rgthree-comfy",
   3  "seedvr2_videoupscaler",
 ```

# Simpler version(s)

The simpler (`_simple`) version removes Stage 3 and Stage 4 from the Full version, and is therefore much lighter to run at only about 300 nodes (most hidden within subgraphs) and 18 custom nodes to obtain.

## extras

### _simple_detailer

Stage3's detailers made generic detailer with manual mask (and drawing for inpainting) selection.

### _simple_detailer_sam3

Stage 3's detailers with automatic detection (SAM3 based) to automatically detail the mask found with the provided prompt.

### _simple_resampler

Stage3's Initial and Detailer resamplers.

### _simple_Flus_resampler

Stage3's Flus resampler.
