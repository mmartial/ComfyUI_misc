<h1>ComfyUI "Combined Workflow"</h1>

# Workflows

## "combined"

This is the primary image generator that has supports for multiple image models for sampling (with about 500 custom nodes)
The workflow include an optional model resampler, VOSR2 upscalers, and Face and Eyes detailers.
See the embedded `READ ME FIRST` for usage details.

FYSA: list (and count) of used custom nodes:

```bash
❯ fgrep cnr_id gkr_9.4_combined.json | tr '[:upper:]' '[:lower:]' | tr -s " " | sort | cut -d ":" -f 2 | uniq -c
   1  "cg-image-filter",
 201  "comfy-core",
   7  "comfy-image-saver",
   1  "comfy-mtb",
  36  "comfyliterals",
   9  "comfyui_essentials",
   4  "comfyui_lc_maskmaker_nodes",
  15  "comfyui_lc123_nodes",
   7  "comfyui_llm_party",
  15  "comfyui-crystools",
  25  "comfyui-custom-scripts",
   8  "comfyui-detail-daemon",
  18  "comfyui-easy-use",
   4  "comfyui-fbcnn",
  25  "comfyui-image-saver",
   1  "comfyui-impact-pack",
  30  "comfyui-kjnodes",
  62  "comfyui-lora-manager",
   2  "comfyui-qwenvl",
   1  "comfyui-resolution-master",
   2  "comfyui-rmbg",
   1  "neons-style-explorer",
  25  "rgthree-comfy",
```

## "ModelTools"

Designed to be used with the original generation model, this workflow provide acces to 2x Model resamplers, an Inpainting detailers, SAM detailers with small/large distinction and a final VOSR2 upscaler (about 250 custom nodes).
See the embedded `READ ME FIRST` for usage details.

FYSA: list (and count) of used custom nodes:

```bash
❯ fgrep cnr_id gkr_9.4_ModelTools.json | tr '[:upper:]' '[:lower:]' | tr -s " " | sort | cut -d ":" -f 2 | uniq -c
   1  "cg-image-filter",
  71  "comfy-core",
   4  "comfy-image-saver",
   3  "comfy-mtb",
   5  "comfyliterals",
   4  "comfyui_essentials",
   1  "comfyui_lc_maskmaker_nodes",
  22  "comfyui_lc123_nodes",
   2  "comfyui_ultimatesdupscale",
   4  "comfyui-crystools",
  14  "comfyui-custom-scripts",
   1  "comfyui-detail-daemon",
  43  "comfyui-easy-use",
   3  "comfyui-fbcnn",
  17  "comfyui-image-saver",
  15  "comfyui-impact-pack",
  15  "comfyui-kjnodes",
   9  "comfyui-lora-manager",
   1  "comfyui-rmbg",
  21  "rgthree-comfy",
```

## "AltModelResampler"

Designed to be used with any image, this workflow will allow resampling of the intput image usign Flux1.D or Krea2 with some LoRAs and can create very different results from the source image (about 200 custom nodes).
See the embedded `READ ME FIRST` for usage details.

```bash
❯ fgrep cnr_id gkr_9.4_AltModelResampler.json | tr '[:upper:]' '[:lower:]' | tr -s " " | sort | cut -d ":" -f 2 | uniq -c
  69  "comfy-core",
   1  "comfy-mtb",
  12  "comfyliterals",
   1  "comfyui_controlnet_aux",
   7  "comfyui_essentials",
   1  "comfyui_lc_maskmaker_nodes",
  15  "comfyui_lc123_nodes",
   6  "comfyui-crystools",
  18  "comfyui-custom-scripts",
   1  "comfyui-detail-daemon",
   6  "comfyui-easy-use",
   2  "comfyui-fbcnn",
  25  "comfyui-image-saver",
   4  "comfyui-impact-pack",
  23  "comfyui-kjnodes",
  13  "comfyui-lora-manager",
   2  "contextanchoredtilerefine",
   5  "rgthree-comfy",
```