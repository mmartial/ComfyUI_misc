<h1>ComfyUI "Combined Workflow"</h1>

Please check CivitAI for the older versions of this workflow at [https://civitai.com/models/2149956](https://civitai.com/models/2149956)

This folder contains a "Combined Workflow" (about 2M and over 600 nodes) that does SDXL, Pony, Illustrious, Flux1D, Qwen,ZImage Turbo/Base, Anima, Flux.2 Klein and Krea 2 Turbo generations with an optional prompt extension using LLMs and Wildcards processing.

It will generate an upscaled 16MP image as the final result while staying as close as possible to the original generation and produce CivitAI compatible metadata for each stage of the image generation.
- Stage 1: Generate the regular image using [Detail Daemon](https://github.com/Jonseed/ComfyUI-Detail-Daemon) sampler, pass it to a selector (can be bypassed for batch generation)
- Stage 2: Upscale to 4MP using a model
- Stage 3: Use "Ultimate SD Upscale" to redefine the components of the 4MP image using the original model and LoRAs' specific characteristics. An optional Flux.1D resampler is available followed by a Cleanup stage, then Faces, Hands and Eyes Detailer are then used on the resulting image.
- Stage 4: That result is sent to SeedVR2 or HighresFix to generate the final 16MP image and a color matching step is performed to make it as close as possible as the initial upscaled image.

The workflow contains a "READ ME FIRST" section that details some about how it came to be, what it does and how to use it. Please refer to it for more information.

FYSA: list (and count) of used custom nodes:
```bash
❯ fgrep cnr_id gkr_combined_v8.3.json | tr '[:upper:]' '[:lower:]' | tr -s " " | sort | cut -d ":" -f 2 | uniq -c
   1  "cg-image-filter",
 235  "comfy-core",
   4  "comfy-image-saver",
   2  "comfy-mtb",
  18  "comfyliterals",
   1  "comfyui_controlnet_aux",
  12  "comfyui_essentials",
   9  "comfyui_llm_party",
   2  "comfyui_ultimatesdupscale",
  12  "comfyui-crystools",
  61  "comfyui-custom-scripts",
   9  "comfyui-detail-daemon",
  51  "comfyui-easy-use",
   5  "comfyui-fbcnn",
  73  "comfyui-image-saver",
  41  "comfyui-impact-pack",
   1  "comfyui-inspire-pack",
  35  "comfyui-kjnodes",
   7  "comfyui-lora-manager",
   6  "comfyui-ollama",
   4  "comfyui-qwenvl",
   1  "comfyui-resolution-master",
  13  "comfyui-rmbg",
  76  "rgthree-comfy",
   3  "seedvr2_videoupscaler",
 ```
 