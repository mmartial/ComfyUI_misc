<h1>ComfyUI Workflows</h1>

This folder contains some slightly modified workflows used to generate images with ComfyUI.
The worklows make use of wildcards and will create 4 different images for each prompt that can be reviewed and selected before finalizing generation.

# SDXL, Illustrious and Pony

The [SmoothWorkflowv3.1-mod.json](SmoothWorkflowv3.1-mod.json) workflow is based on the [Smooth Workflow (txt2img) v3.1](https://civitai.com/models/1598938/smooth-workflow-txt2img) workflow.

# Flux

The [AtomixFluxUnet_txt2img_Workflow_V4-SDXLupscale.json](AtomixFluxUnet_txt2img_Workflow_V4-SDXLupscale.json) workflow is based on the [Atomix FLUX Unet txt2img Workflow v4.1](https://civitai.com/models/878828/atomix-flux-unet-txt2img-workflow) workflow. 

## Flux with Ollama

Given that those wildcards generate SDXL-ready prompts, it is recommended to use the [AtomixFlux_Ollama_txt2img_WorkflowV4-SDXLupscale.json](AtomixFlux_Ollama_txt2img_WorkflowV4-SDXLupscale.json) workflow instead.

This workflow requires a running instance of Ollama, so depending on the resource available, this might be using VRAM that you might need for the Flux generation. If you have a way to run Ollama on a separate machine, this might be a good option as ywe recommend the use of an instruction tuned model (the default in the workflow is `gpt-oss:20b`). Make sure to adapt the server url (default is `http://ollama:11434`) and the model name (default is `gpt-oss:20b`) to match your setup; clicking `Reconnect` will allow you to test the connection and list the available models.

Using Ollama, this workflow will convert the prompt using the system prompt shown in the `System Prompt` note in the workflow, also available in the [ollama_sdxl2flux.txt](ollama_sdxl2flux.txt) file.
