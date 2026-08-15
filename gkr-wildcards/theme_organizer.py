#!/usr/bin/env python3

# run with: uv run --with pillow theme_organizer.py -h
# Designed to work with Lora Manager prompt, must know the node ID to check first (default: 672). It will extract the theme from the image metadata and organize images into theme folders.

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from PIL import Image

THEME_PATTERN = re.compile(r"__gkr_([^/]+)/.*?__")


def extract_theme(
    image_path: Path, 
    target_node: str = "672", 
    ignored_keys: tuple = ("lastAccepted",)
) -> str | None:
    """Extracts the theme string from the image metadata, avoiding ignored keys like lastAccepted."""
    try:
        with Image.open(image_path) as img:
            info = img.info

        # 1. Inspect the executed prompt inputs for the specific node
        if "prompt" in info:
            try:
                prompt_json = json.loads(info["prompt"])
                node_data = prompt_json.get(target_node, {})
                inputs = node_data.get("inputs", {})

                # Check all input keys except ignored ones (like 'lastAccepted')
                for key, val in inputs.items():
                    if key not in ignored_keys and isinstance(val, str):
                        match = THEME_PATTERN.search(val)
                        if match:
                            return match.group(1)
            except json.JSONDecodeError:
                pass

        # 2. Inspect workflow node widgets (skipping 'lastAccepted')
        if "workflow" in info:
            try:
                workflow_json = json.loads(info["workflow"])
                for node in workflow_json.get("nodes", []):
                    if str(node.get("id")) == target_node:
                        # Check widgets_values (often a list of values)
                        widgets = node.get("widgets_values", [])
                        if isinstance(widgets, list):
                            for item in widgets:
                                if isinstance(item, str):
                                    match = THEME_PATTERN.search(item)
                                    if match:
                                        return match.group(1)
            except json.JSONDecodeError:
                pass

        # 3. Fallback: Search remaining metadata strings
        for key, value in info.items():
            if key not in ("prompt", "workflow") and isinstance(value, str):
                match = THEME_PATTERN.search(value)
                if match:
                    return match.group(1)

    except Exception as e:
        print(f"Error reading {image_path.name}: {e}", file=sys.stderr)

    return None



def process_image(image_path: Path, move: bool, target_node: str):
    if not image_path.is_file():
        print(f"[SKIP] Not a file: {image_path}")
        return

    theme = extract_theme(image_path, target_node)

    if not theme:
        print(f"[NOT FOUND] {image_path.name}: No matching '__gkr_<theme>/...' pattern found.")
        return

    if move:
        dest_dir = image_path.parent / theme
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / image_path.name

        # Prevent overwriting if filename exists in target
        if dest_path.exists() and dest_path != image_path:
            print(f"[EXISTS] Cannot move {image_path.name}: File already exists in {theme}/")
            return

        shutil.move(str(image_path), str(dest_path))
        print(f"[MOVED] {image_path.name} -> {theme}/")
    else:
        print(f"[THEME] {image_path.name} : '{theme}'")


def main():
    parser = argparse.ArgumentParser(
        description="Extract ComfyUI prompt themes and organize images into theme folders."
    )
    parser.add_argument(
        "images",
        nargs="+",
        type=Path,
        help="One or more image file paths or glob patterns (e.g., *.png).",
    )
    parser.add_argument(
        "-m",
        "--move",
        action="store_true",
        default=False,
        help="Create theme folders and move images into them (default: display only).",
    )
    parser.add_argument(
        "--node",
        type=str,
        default="672",
        help="Node ID to check first (default: '672').",
    )

    args = parser.parse_args()

    for path in args.images:
        process_image(path, move=args.move, target_node=args.node)


if __name__ == "__main__":
    main()
