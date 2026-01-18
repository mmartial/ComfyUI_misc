<h1>Safetensor Cleaner</h1>

A python script designed to help keep our Stable Diffusion (ComfyUI, [Stability Matrix](https://github.com/mmartial/ComfyUI-Nvidia-Docker/wiki/Stability-Matrix-integration), [LoRA Manager](https://github.com/mmartial/ComfyUI-Nvidia-Docker/wiki/LoRA-Manager-Integration) or other tools) model collection organized. 
It manages "sidecar" files—like preview images (`.preview.png`), info files (`.civitai.info`), and metadata—ensuring they stay with their models and don't clutter your folders (especially after a model deletion).

- [1. Usage](#1-usage)
- [2. Configuration (`safetensor_cleaner.json`)](#2-configuration-safetensor_cleanerjson)


- **Clean Orphans**: identifies and deletes sidecar files that no longer have a corresponding model file (e.g., you deleted the `.safetensors` file but the `.preview.png` was left behind).
- **Deduplicate**: Detects when you have multiple sidecar files for the same model (e.g., multiple preview images) and helps you keep just one.
- **Move to Model**: Moves sidecar files into the exact same folder as their model, useful if your downloads got scattered.
- **Version Detection**: Smartly detects multiple versions of the same model family (e.g., `Model_v1`, `Model_v2`) and identifies orphans that likely belong to them.

Requirements:
- `python`; I tried to keep it simple and only use python standard libraries.
- a configuration file named `safetensor_cleaner.json` in the same folder as the script (an example is provided).

## 1. Usage

Open your terminal or command prompt, navigate to the folder, and run the script to get the list of options (please refer to it to find existing or new features).

```bash
python3 ./safetensor_cleaner.py --help
```
Only the `move` and `delete` related options will perform changes.
For extra safety, you can use the `--confirm-each` flag to be asked for each deletion or move.

By default, the script **will not** make any changes. 
It only prints what it *found*. 
It is recommended to always run this first.

```bash
python3 safetensor_cleaner.py --root /path_to_base_comfy_models
[...]
Group: novaMatureXL_v35
  - novaMatureXL_v35.metadata.json (/path/to/StableDiffusion/Illustrious)
  - novaMatureXL_v35.jpeg (/path/to/StableDiffusion/Illustrious)
  [ORPHAN] No model found.
[...]
```
Shows some orphan sidecars, using `--delete_orphan` will delete those.


Finding multiple versions of a similar model:
```bash
python3 ./safetensor_cleaner.py --show-versions
[...]
Model Group: Art_Nouveau
  - Art_Nouveau_IL_MIX_V01 (7 files) [MODEL]
      Art_Nouveau_IL_MIX_V01.metadata.json [/path/to/StableDiffusion/Illustrious]
      Art_Nouveau_IL_MIX_V01.safetensors [/path/to/StableDiffusion/Illustrious]
      Art_Nouveau_IL_MIX_V01.civitai.info [/path/to/StableDiffusion/Illustrious]
      Art_Nouveau_IL_MIX_V01.cm-info.json [/path/to/StableDiffusion/Illustrious]
      Art_Nouveau_IL_MIX_V01.preview.jpeg [/path/to/StableDiffusion/Illustrious]
      Art_Nouveau_IL_MIX_V01.sha256 [/path/to/StableDiffusion/Illustrious]
      Art_Nouveau_IL_MIX_V01.jpeg [/path/to/StableDiffusion/Illustrious]
  - Art_Nouveau_Style (5 files) [MODEL]
      Art_Nouveau_Style.safetensors [/path/to/StableDiffusion/Flux.1 D/style]
      Art_Nouveau_Style.jpeg [/path/to/StableDiffusion/Flux.1 D/style]
      Art_Nouveau_Style.preview.jpeg [/path/to/StableDiffusion/Flux.1 D/style]
      Art_Nouveau_Style.cm-info.json [/path/to/StableDiffusion/Flux.1 D/style]
      Art_Nouveau_Style.metadata.json [/path/to/StableDiffusion/Flux.1 D/style]
  - Art_Nouveau_Z_v1 (5 files) [MODEL]
      Art_Nouveau_Z_v1.metadata.json [/path/to/StableDiffusion/ZImageTurbo/style]
      Art_Nouveau_Z_v1.safetensors [/path/to/StableDiffusion/ZImageTurbo/style]
      Art_Nouveau_Z_v1.jpeg [/path/to/StableDiffusion/ZImageTurbo/style]
      Art_Nouveau_Z_v1.cm-info.json [/path/to/StableDiffusion/ZImageTurbo/style]
      Art_Nouveau_Z_v1.preview.jpeg [/path/to/StableDiffusion/ZImageTurbo/style]
[...]
```
The tool does not understand the difference in model types, it relies on the stem to identify groups.

Groups are common stems found in multiple files. For example:

```bash
python3 ./safetensor_cleaner.py --show-versions
[...]
Model Group: pieModels
  - pieModels_applePieV2 (7 files) [MODEL]
      pieModels_applePieV2.preview.jpeg [/path/to/StableDiffusion/Illustrious]
      pieModels_applePieV2.civitai.info [/path/to/StableDiffusion/Illustrious]
      pieModels_applePieV2.sha256 [/path/to/StableDiffusion/Illustrious]
      pieModels_applePieV2.metadata.json [/path/to/StableDiffusion/Illustrious]
      pieModels_applePieV2.cm-info.json [/path/to/StableDiffusion/Illustrious]
      pieModels_applePieV2.safetensors [/path/to/StableDiffusion/Illustrious]
      pieModels_applePieV2.jpeg [/path/to/StableDiffusion/Illustrious]
  - pieModels_blueberryPie (4 files) [MODEL]
      pieModels_blueberryPie.safetensors [/path/to/StableDiffusion/Illustrious]
      pieModels_blueberryPie.cm-info.json [/path/to/StableDiffusion/Illustrious]
      pieModels_blueberryPie.metadata.json [/path/to/StableDiffusion/Illustrious]
      pieModels_blueberryPie.preview.jpeg [/path/to/StableDiffusion/Illustrious]
[...]
```

Refers to a group of files that share the same base name (the `stem`) (e.g., `pieModels`). 
It is possible to ignore groups in the configuration file, just add the stem name to the `ignore_groups` list.

The algorithm does it best to find best match for groups but it is not always find the most optimal match. 

```bash
python3 ./safetensor_cleaner.py --show-versions
[...]
Model Group: BSY_General
  - BSY_General_Negatives_V1_PRO (3 files) [MODEL]
      BSY_General_Negatives_V1_PRO.metadata.json [/path/to/Data/Models/Embeddings]
      BSY_General_Negatives_V1_PRO.sha256 [/path/to/Data/Models/Embeddings]
      BSY_General_Negatives_V1_PRO.safetensors [/path/to/Data/Models/Embeddings]
  - BSY_General_Positives_V1_PRO (3 files) [MODEL]
      BSY_General_Positives_V1_PRO.sha256 [/path/to/Data/Models/Embeddings]
      BSY_General_Positives_V1_PRO.safetensors [/path/to/Data/Models/Embeddings]
      BSY_General_Positives_V1_PRO.metadata.json [/path/to/Data/Models/Embeddings]
[...]
```

If a group's steam is too short to represent a set of models, it is recommended to add it to the `ignore_groups` list.

## 2. Configuration (`safetensor_cleaner.json`)

The sidecar JSON file tells the script what to **ignore**.
 This is useful if you have non-model folders (like `venv` or `.git`) or specific file types you want to keep.

**Example Structure:**
```json
{
  "ignore_extensions": [".py", ".txt"],
  "ignore_folders": [
    "venv",
    ".git",
    "MyBackupFolder"
  ],
  "ignore_groups": ["Specific_Model_Stem"]
}
```

- **ignore_extensions**: Files with these endings will be completely ignored.
- **ignore_folders**: Use this to prevent the script from scanning system folders or backups.
- **ignore_groups**: If you have a specific model "basename" you want the script to skip.
