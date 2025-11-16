<h1>How to Create Your Own AI Art Wildcard Files with an LLM</h1>

This guide explains how to use the `llm-wildcard-generator.yaml` template to instruct a Large Language Model (LLM) like Gemini or ChatGPT to create a powerful, custom wildcard file for your AI art projects.

- [1. Prerequisites](#1-prerequisites)
- [2. What is a Wildcard File?](#2-what-is-a-wildcard-file)
- [3. How to Use This Template (Step-by-Step)](#3-how-to-use-this-template-step-by-step)
  - [3.1. Step 1: Get the Template](#31-step-1-get-the-template)
  - [3.2. Step 2: Define Your Theme](#32-step-2-define-your-theme)
  - [3.3. Step 3: Prompt Your LLM](#33-step-3-prompt-your-llm)
  - [3.4. Step 4: Review and Refine](#34-step-4-review-and-refine)
  - [3.5. Step 5: Save and Use](#35-step-5-save-and-use)
- [4. Understanding the Wildcard File Structure](#4-understanding-the-wildcard-file-structure)
- [5. Advanced Tips](#5-advanced-tips)
- [6. Cleanup prompts](#6-cleanup-prompts)
  - [6.1. Global fix](#61-global-fix)
  - [6.2. Spotlight fix](#62-spotlight-fix)

# 1. Prerequisites

- A Large Language Model (LLM) like Gemini or ChatGPT
- A text editor (like Notepad, Sublime Text, or VSCode)
- A YAML file (like `llm-wildcard-generator.yaml`)

# 2. What is a Wildcard File?

Think of a wildcard file as **"Mad Libs" for AI art prompting**.

It's a text file (in this case, YAML) that holds lists of ideas (like characters, locations, and art styles). When you use a wildcard prompt, the software (like ComfyUI with its Dynamic Prompts node) randomly picks one item from each list you reference.

This lets you generate hundreds of unique, high-quality images from a single, simple prompt like:
`"__my_theme/combo_character__"`

Instead of writing a new, long prompt every time, you just run that one line, and it might generate:
- "A vampire lord in a tattered suit, in an abandoned mansion, moonlight, gothic horror style"
- "An occult sorcerer in dark robes, in a crumbling cathedral, dim candlelight, painterly style"
- ...and so on!

# 3. How to Use This Template (Step-by-Step)

## 3.1. Step 1: Get the Template

Start with the `llm-wildcard-generator.yaml` file. This file isn't your final wildcard file; it's a *prompt for your LLM*.

## 3.2. Step 2: Define Your Theme

Open `llm-wildcard-generator.yaml` in any text editor. Find this line at the top:

```yaml
user_theme: "[YOUR_THEME_HERE]"
```

Change `[YOUR_THEME_HERE]` to whatever theme you want. Be descriptive!

Good Examples:
- `user_theme: "Cyberpunk City at Night"`
- `user_theme: "High Fantasy Magical Forest"`
- `user_theme: "Cozy Victorian Bakery"`
- `user_theme: "Lovecraftian Cosmic Horror"`

## 3.3. Step 3: Prompt Your LLM

1. Copy the entire contents of your edited `llm-wildcard-generator.yaml` file.
2. Paste that entire block of text directly into your favorite LLM (Instruction-tuned local model, Gemini, ChatGPT, Claude, etc.).
3. Run the prompt (for example "Follow the instructions in the YAML file")

The LLM will read the `llm_instructions` and the `generation_structure` and create a brand-new, complete YAML file based on the `user_theme` you provided.

## 3.4. Step 4: Review and Refine

The LLM will output a large block of YAML code.

- Check it: Does it look good? Did it follow the instructions?
- Refine it (Optional): Copy the LLM's output into your own text editor. You can now manually add, remove, or change any of the options the LLM generated. This is your file now!

## 3.5. Step 5: Save and Use

1. Save the new code (the LLM's output) as a `.yaml` file. The name should match the top-level key the LLM created.
  - For example, if the LLM started the file with `my_cyberpunk:`, you should save the file as `my_cyberpunk.yaml`.

2. Move this new file into the correct wildcard folder for your AI art software (e.g., in ComfyUI, this might be `basedir/custom_nodes/comfyui-impact-pack/wildcards/` or a folder specified by your Dynamic Prompts or YAML-reader node).

# 4. Understanding the Wildcard File Structure

The template instructs the LLM to create a file with these key sections:

- Component Categories: (e.g., primary_subjects, settings, lighting_styles)
  - These are the basic building blocks. The LLM will rename them to fit your theme (e.g., primary_subjects might become cyberpunk_archetypes).

- Combo Categories: (e.g., combo_character, combo_scene)
  - These are pre-built prompts that combine the component categories. This is where the __theme_name/category__ syntax is used.

- Spotlight Prompts: (e.g., spotlight_prompts)
  - These are not wildcards. They are complete, highly-detailed, pre-written prompts. They are perfect for testing the "vibe" of your theme and for when you want a guaranteed high-quality result without randomization.

- Negative Prompt:
  - A ready-to-use negative prompt that includes general "bad art" terms as well as specific terms to exclude from your theme (e.g., a "Fantasy" theme's negative prompt will include "sci-fi, futuristic, spaceship").

# 5. Advanced Tips

- Emphasis: The (word:1.2) syntax increases the "weight" or importance of a word, just like in a normal Stable Diffusion prompt. The LLM is instructed to add this where it makes sense.

- Nesting: You can create your own "combo" categories that reference other "combo" categories!

- Iteration: Don't like the first file the LLM made? Tweak your user_theme in the template (e.g., from "Cyberpunk" to "Gritty, Noir Cyberpunk") and try again!

# 6. Cleanup prompts

## 6.1. Global fix

```text
From this input YAML file, reproduce each line of text using usable comfyui prompts. 

Web search for examples of proper prompts online and describe the logic for future prompting.

Fix the strings on each line so they are usable by sdxl or illustrious: descriptions should be short with a few words where word clusters should be comma-separated.  Do not separate each word with a comma, only independent concepts should be separated. 

For each line, decide if the concept should be extended to provide better image results, if so, extend with compatible additions, limited to two extra concepts per line.

Do not use the terms: 1boy, 1girl.

Add Prompt Weighting on key features to increase their impact.

Perform a cleanup of redundant items.

Add a single "negative_prompt" at the end to use in the negative prompt field.

Each line from the source file should be present in the new file. 

Provide a valid YAML file.
```

## 6.2. Spotlight fix

```text
From this input YAML file, replace each entry in the spotlight sections. 
Given the context of the document create a complete prompt for each entry replaced.
This prompt should be epic and represent the best possible image that represent the global theme of the document.
Limit to a max of 1000 characters per line.

Web search for examples of proper prompts online and describe the logic for future prompting.

Fix the strings on each line so they are usable by sdxl or illustrious: descriptions should be short with a few words where word clusters should be comma-separated.  Do not separate each word with a comma, only independent concepts should be separated. 

Do not use the terms: 1boy, 1girl.

Add Prompt Weighting on key features to increase their impact.

Perform a cleanup of redundant items.

Provide valid YAML sections to replace the old one.
```
