# Geekier Workflow: README First

(copied from the Workflow for easy review in `simpler` version)

## General Information

Thank you for using this "Combined Workflow" ❤️

This workflow is published with an [MIT License](https://github.com/mmartial/ComfyUI_misc/blob/main/LICENSE) and the latest version can be found on [Github](https://github.com/mmartial/ComfyUI_misc/tree/main/ComfyUI-Workflows) (which will include a link to CivitAI)

### About

This workflow is built to produce high resolution results. It is not build for speed: it takes over 7 minutes on a 4090 to generate a batch of 2 images (with 98% of the VRAM being used at times thanks to SageAttention)

My goal was to generate a 4K image and upscale it to 16MP as the final result while staying as close as possible to the original and saving CivitAI-compatible metadata (for each stage of the image generation).

ℹ️ Some of its logic is present within SubGraphs which can be reviewed.
Even on a 4090 --ie with 24GB of VRAM--, the last Upscaling stage (S4) requires proper model offload (to RAM if possible) so using the latest version of ComfyUI is recommended to get the update memory management, despite some aggressive attempt to unload models, clear VRAM and the recommended use of attention mechanisms.<br>
⚙️ Because I have placed components in SubGraphs, it is possible to copy the SubGraph node into a new workflow (I recommend you expand it if you do, so you can see its logic) and use a `Load Image` node to run the various stages/steps on a specific image. I do this often, they are useful base "functions" to have around and use.

ℹ️ Using `Any Switches (rgthree)` is an attempt to create the closest thing possible to "functions parameters" in ComfyUI.
 
Stages/steps are used to separate the various items to be performed. 
Groups exist as an organizational structure for the entire process and follow the Stage numbers.
Nodes in 🟦 are where parameters customization should be made (for the 🟢 and 🔵 groups only, not the 🔴 group).

There are many nodes involved in this workflow, and you might need to retrieve/install a few models ([SAM3](https://github.com/facebookresearch/sam3), [SeedVR2](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler)) or [Wildcards (using Lora Manager)](https://github.com/willmiao/ComfyUI-Lora-Manager#wildcards-for-textlm--promptlm) ([🔗 For some of my wildcards](https://github.com/mmartial/ComfyUI_misc)).

Groups and muters are here to keep the workflow organized and hopefully easier to navigate.<br>
The "How to use it" section tells you a few things about it.<br>
The "What it does" section explains what the different stages do.<br>

It is also a Work-In-Progress workflow: updates will be released when I am happy with the result.
You will see multiple "notes" block at different locations in the workflow. Those will contain additional information to steps with the current block. Reviewing those is recommended to get a good understanding of the various steps involved. 

You will also see "Muters/Bypassers" to enable or disable some features of the workflow. The main ones are below, but strategically placed duplicates/tailored versions are present at various locations for quick selection. Some of those should only have one entry selected; please follow the posted instructions to avoid potential issues.

**It "works for me," but it might not be the best way to do it. Feedback is welcome.**

### Quick use

Prompt, LoRAs and image parameter are set in 🟢 groups. LLM usage (Ollama, QwenVL or other LLMs) settings (including enabling LLM) is also in this group.<br>
The initial image generation and model selection is done in 🔴. Only one model generation can be performed at a given time. Used LoRAs should be compatible with that base model class (ie use Flux LoRAs for a Flux base model).<br>
The 🔵 groups are where the image processing is performed. Model selection and upscaling parameters for sub-stage functions are available there.<br>

ℹ️ A table with recommendations (based on my various tests) is available in the below. Please see and test with your parameters to optimize your generations.

ℹ️ Please review the various additional notes available through this workflow to find clarifications on additional settings.

⬇️ This is the primary location to find most of the fast toggles use in the workflow.<br>
⚠️ Enabling disabled Stages WILL enable ALL sub-steps for this stage, in some cases, breaking the "Only 1" rule.

## How to use it

### 🟢 groups ("01") are where prompt generation is done. 
- Ollama/QwenVL and "Other LLM" (you must "Bring Your Own API key") are an option for transforming Wildcards prompts into narratives (🟢01a) or help generate better narratives for prompts (🟢01a and/or 🟢01b). In 🟢01a you can have an LLM extend the wildcards provided or convert a SDXL entry into a narrative or a cleaner "bag of words" (tags) prompt. 🟢01b is here to refine the content of 🟢01a or to generate narratives based on the input provided. Modify the group's prompt to match your expectation.
  - 01z is a similar LLM group but --if enabled-- is run AFTER the initial prompt is generated. It supports three types of prompt generation.
  - Ollama usage: set the `url` to match your local instance (`Reconnect` will allow you to test the connection and list the available models). A `keep_alive` value of `0` will drop the model (and release VRAM) right after generation. When using Ollama, use an instruction tuned model (the default in the workflow is `gpt-oss:20b`).
  - "Other LLM" is a means to allow users to use external LLMs. Some setup is required to avoid exposing your API keys (described in the "Prompt Composition"🟩 note). Your cost will depend on your selected model and token usage. From testing, `gpt-4.1-mini` was sufficient to produce good results and [cost about $2 per 1M tokens on OpenRouter.ai](https://openrouter.ai/openai/gpt-4.1-mini) (always confirm cost on your preferred LLM API provider). Once a prompt is generated, it is possible to disable the use of LLMs , copy and manual edit the prompt into the prompt logic (in 🟢01x).
  - "QwenVL" is run on the GPU, and does not require any additional installation (beyond its custom node). The node will download the required model when run the first time. The model to use depends on your VRAM. QwenVL is the model used for the `img2txt` group (01c) to describe an input image so it can be used as a base for `txt2img` generation.
  - (01a+b only) Multiple LLM "system prompts" are possible: "original" works well with Illustrious to provide a narrative prompt, while "advanced" provides longer narrative which work best with more recent models. The "Automatic" one will do its best to adapt per model. Select only one using the muter toggle based on the length and amount of details you need in the generated content. Being specific in your user prompt will generate better results. The "Automatic" system prompt is set to generate bag of words for SDXL/Pony/Illustrious/Anima.
  - The `System Prompt` used for 01a and 01b is available in each group and can be tweaked.
- Narrative prompts only work for certain models and are not recommended for Anima, SDXL or Pony (use classic tags (bag of words), or wildcards (in 🟢01x)).
- 01c (QwenVL Image 2 Prompt) uses QwenVL to describe an input image into a text prompt. 01c can only be used if both 01a and 01b are muted.
- 01m (`mode` selection) switch the generation from `txt2img` to `img2img`.
- In 01x you will be able to set parameters for image generation: LoRAs, positive (including wildcard use) and negative prompts (`trigger_words` for the LoRAs can be selected in the LoRA manager node)
  - the final prompt is a combination of the various prompts (logic is shown in 01x).
  - a "LoRA Randomizer" option is available in "01l" (which can be disabled from the toggle). Check the group for details. Remember to select LoRAs compatible with the model you are using. It is recommended to prefer setting "Character" LoRAs in the 01x LoRA selection group (see the "One character logic" note).
- "Final Prompt Enhancement" (01z) --if enabled-- is run after 01x to use LLMs to optimize the final prompt's text to be used by the positive conditioning based on a set of "system prompts".

### 🔴 groups ("02") specify the `Sampler` models
You can only generate one type of image at at time (LoRAs are model-type specific so things may break otherwise)
- Each type of model can have its specific variables set in the selected SubGraphs: Checkpoint, VAE, CLIP (or model embedded ones for SDXL/Pony/Illustrious), `steps`, `cfg`, `sampler`, `scheduler`, `clip_skip`, `denoise`. `batch_size`, `seed`, `positive_prompt` and `negative prompt` are set in 🟢01x.
- Random model selection: if you toggle the `select_..._at_random` option, you must also select the `Base Model` that matches what you are trying to generate.
- 🔗[Detail Daemon](https://github.com/Jonseed/ComfyUI-Detail-Daemon) is used with each initial sampler to add details to the original image generation. It is recommended to check its usage as the value to use differ for each base model.
- ⚠️ Z Image Turbo and Z Image Base don't use the same `steps` or `cfg`. Check the note next to models group for recommendations; refer to usage recommendations from your model source the model's preferred settings.
- The positive prompt is a concatenation of the various prompts combinations generated in the prompting group (including LLM if available/enabled). The "prompt concatenation logic" is detailed in 🟢01x. If enabled, 01z will replace 01x's prompt with a reorganized/optimized prompt.
- Each 🔴 Sampler outputs all the components needed for the 🔵 groups to work independently. This allows the workflow to have independent KSampler Subgraphs and a common set of "Stages"

*Detail Daemon parameters*:<br>
🔗[https://github.com/Jonseed/ComfyUI-Detail-Daemon?tab=readme-ov-file#detail-daemon-sampler](https://github.com/Jonseed/ComfyUI-Detail-Daemon?tab=readme-ov-file#detail-daemon-sampler)
> - `detail_amount`: the main value that adjusts the detail in the middle of the generation process. Positive values lower the sigmas, reducing noise removed at each step, which increases detail. For Flux or Z-Image models, you'll probably want between 0.1–1.0 range, or higher. For SDXL models, probably less than 0.25. You can also use negative values if you want to decrease detail or simplify the image.
> - `start`: when do you want the adjustment to start, in a percent range from 0–1.0, 0 being the first step, 1.0 being the last step. Recommended: 0.1–0.5
> - `end`: when do you want the adjustment to end, in a percent range from 0–1.0, 0 being the first step, 1.0 being the last step. Recommended: 0.5–0.9
> - `bias` : shifts the detail_amount in the middle steps forward or back in the generation process.
> - `exponent`: changes the curvature of the adjustment. 0 is no curvature, 1 is smoothly curved.

## What it does

### Stage 1 (🔵"03" group): Candidate Selection
The workflow will pause and allow us to select which images to go to later stages
- This can be bypassed (use the `Bypass Candidate Selection` toggle)
- I usually set the `batch_size` (in 01x) to 2 or 4 to allow me to review candidates before sending those to later stage (modify if you prefer to generate one image at a time).
- In this stage, a first `Image Saver` (Subgraph, 03s) is used. It will store images in folder based on Today's date, and use a file naming convention of: `%time_S1_%basemodelname_%seed` (S1 for "Stage 1", later stages base names are `S2`, `S3`, `S4` and might include information about the step within the stage). 
- CivitAI metadata (from the original Sampler) is stored in the image: `model`, `sampler`, `scheduler`, `steps`, `cfg`, `clip_skip`, `seed`, full `positive_prompt` and `negative_prompt` (including LoRAs, Embeddings...). The workflow is also embedded in the resulting image file.
- it is possible to bypass specific "Image Savers" steps using the toggles; for example, to only keep the final (S4) image
### Stage 2 (🔵"04"): Upscale 
... to 4 MegaPixels (MP) using an upscaler model (MP value can be changed; consider keeping 4 as a maximum as the next step are image regeneration and detailers).
### Stage 3 (🔵"05"): Resamplers (05a), Cleanup (05b) and Detailers (05c)
(multiple SubGraphs) Upscaled Image Regeneration (2x Pass: 1=ReSampler (05a01), 2=Details Enhancement (05a02)) + 1x optional Flux pass (05a03) + 1x cleanup step (with 8MP upscale, 05b01) + Faces (05c01), Eyes (05c02) & Hands (05c03) Detailers
- For the 2x Pass Resamplers (05a01, 05a02), it is possible to use "Alternate Models" (SDXL, Pony, Illustrious) for this step to speed up the process and reduce the VRAM usage. When doing so, it is recommended to use a model that match the style of your source image. Both can be enabled in the "Reduce Stage 3 ..." Muter selection. This is not needed with SDXL/Pony/Illustrious base models generation (02a). LoRAs will not be passed to those alternate models. When not using alternate models, the `Ultimate SD Upscaler` step will regenerate the upscaled image with the **original model and LoRAs** (potentially increasing VRAM usage and time for larger models).
- An optional Flux detailer (05a03, likely requiring at minimum 24GB VRAM) is possible. This step will use specific LoRAs to enhance details.
- When `detail_amount` (and subsequent) detail deamon parameters are exposed, those can be modified as preferred.
- The Resampler steps (all 05a steps) provide some control over the original settings (in the form of multipliers).
- A "cleanup" step (05b01) is then performed that will use HiresFix with a model to cleanup the source image (the upscale model selected will influence the final result), then a rescale will make the image 8MP (configurable) and attempt to match its color palette before going to the detailer steps.
- Faces, Eyes and Hands Detailers (05c01 to 05c03) are then used on the resulting image (each detailer can be bypassed)
- This step will use [Segment Anything Model 3 (SAM3)](https://ai.meta.com/research/sam3/) to find the elements listed in the parameter box (`face`, `eye`, `hand`). Other common detection parameters can be configured. The detailers use a different `denoise` for "small" (1/value surface uage of the full image) face/eyes/hands to partially re-render small elements. Please see the comment in Stage 3 for additional details.
### Stage 4 (🔵"06"): Upscale to 12-16 MegaPixels (recommended to use ComfyUI's Attention to unload models, can do 20+ MP if your VRAM allows it)
The final image size will depend on your available VRAM, so test to find your capabilities using SeedVR2 (16MP can be achieved on 24GB VRAM) or another HiresFix, then Color Matching against the first upscaled image (SubGraph)
- SeedVR2 models can be selected; if possible, for art, use a `sharp` model. We are using tiled upscaling, which increase the time to upscale while reducing VRAM usage.
- As before for HiresFix, the upscale model matters for the final image result.
- Select only one of the last upscaler using the toggle selection.
- a "Color Match" step is then performed to try to match the final image against the original image.

Multiple "comparer" nodes will show the difference between the various stages and their steps. Those serve as quick methods to understand the interaction of a given step and its parameters on the selected base-model and can be bypassed to speed up batch processing if preferred.

