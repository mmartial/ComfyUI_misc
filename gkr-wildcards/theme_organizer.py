#!/usr/bin/env python3

# run with: uv run --with pillow theme_organizer.py -h
#
# Designed to work with Lora Manager prompt, must know the node ID to check first (default: 672). It will extract the theme from the image metadata and organize images into theme folders.
#
# Practical example: uv run --with pillow ../theme_organizer.py *.png -m --details details.md

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from PIL import Image

THEME_PATTERN = re.compile(r"__gkr_([^/]+)/.*?__")
WILDCARD_PATTERN = re.compile(r"__([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)__")


def load_metadata(image_path: Path) -> dict:
    with Image.open(image_path) as img:
        return dict(img.info)


def parse_json_metadata(info: dict, key: str) -> dict:
    value = info.get(key)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def strings_in(value):
    """Yield strings from nested widget values without treating metadata dictionaries as prompts."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings_in(item)


def workflow_node(workflow: dict, node_id: str, title_hint: str) -> dict:
    nodes = workflow.get("nodes", [])
    for node in nodes:
        if str(node.get("id")) == str(node_id):
            return node
    hint = title_hint.lower()
    for node in nodes:
        if hint in str(node.get("title", "")).lower():
            return node
    return {}


def node_text(node: dict) -> str | None:
    # widgets_values is the image's captured value. Named values may reflect a
    # later UI state, so use them only as a fallback.
    for value in strings_in(node.get("widgets_values", [])):
        if value.strip():
            return value
    named = node.get("widgets_values_named", {})
    if isinstance(named, dict):
        for key in ("text_0", "text"):
            value = named.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def extract_prompts(info: dict, pre_node: str, post_node: str) -> tuple[str | None, str | None]:
    workflow = parse_json_metadata(info, "workflow")
    pre = node_text(workflow_node(workflow, pre_node, "Input Prompt"))
    post = node_text(workflow_node(workflow, post_node, "01z result"))
    return pre, post


def extract_prompt_mode(info: dict, switch_node: str = "2375") -> str | None:
    """Identify the unmuted system-prompt literal feeding the configured switch."""
    workflow = parse_json_metadata(info, "workflow")
    nodes = {str(node.get("id")): node for node in workflow.get("nodes", [])}
    switch = nodes.get(str(switch_node), {})
    input_links = {
        item.get("link") for item in switch.get("inputs", [])
        if isinstance(item, dict) and item.get("link") is not None
    }
    sources = []
    for link in workflow.get("links", []):
        if not isinstance(link, list) or len(link) < 4 or link[0] not in input_links:
            continue
        source = nodes.get(str(link[1]))
        if source and source.get("mode", 0) == 0:
            sources.append(source)
    if len(sources) != 1:
        return None
    title = str(sources[0].get("title", "")).strip()
    normalized = title.lower().replace("+", " and ")
    if "narrative" in normalized and "tags" in normalized:
        return "Narrative and Tags"
    if "narrative" in normalized:
        return "Narrative"
    if "tags" in normalized:
        return "Tags"
    return title or None


def extract_wildcard(info: dict, target_node: str) -> str | None:
    prompt = parse_json_metadata(info, "prompt")
    inputs = prompt.get(str(target_node), {}).get("inputs", {})
    if isinstance(inputs, dict):
        for key, value in inputs.items():
            if key != "lastAccepted" and isinstance(value, str):
                match = WILDCARD_PATTERN.search(value)
                if match:
                    return match.group(0)
    workflow = parse_json_metadata(info, "workflow")
    node = workflow_node(workflow, target_node, "LoRA Manager Prompt")
    text = node_text(node)
    if text:
        match = WILDCARD_PATTERN.search(text)
        if match:
            return match.group(0)
    return None


def extract_theme(
    image_path: Path, 
    target_node: str = "672", 
    ignored_keys: tuple = ("lastAccepted",)
) -> str | None:
    """Extracts the theme string from the image metadata, avoiding ignored keys like lastAccepted."""
    try:
        info = load_metadata(image_path)

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



def process_image(
    image_path: Path, move: bool, target_node: str, pre_node: str, post_node: str,
    mode_switch_node: str,
) -> dict | None:
    if not image_path.is_file():
        print(f"[SKIP] Not a file: {image_path}")
        return None

    try:
        info = load_metadata(image_path)
    except Exception as exc:
        print(f"Error reading {image_path.name}: {exc}", file=sys.stderr)
        return None

    theme = extract_theme(image_path, target_node)
    wildcard = extract_wildcard(info, target_node)
    pre_prompt, post_prompt = extract_prompts(info, pre_node, post_node)
    prompt_mode = extract_prompt_mode(info, mode_switch_node)

    if not theme:
        print(f"[NOT FOUND] {image_path.name}: No matching '__gkr_<theme>/...' pattern found.")
        return None

    destination = image_path.resolve()
    if move:
        dest_dir = image_path.parent / theme
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / image_path.name

        # Prevent overwriting if filename exists in target
        if dest_path.exists() and dest_path != image_path:
            print(f"[EXISTS] Cannot move {image_path.name}: File already exists in {theme}/")
            return None

        shutil.move(str(image_path), str(dest_path))
        destination = dest_path.resolve()
        print(f"[MOVED] {image_path.name} -> {theme}/")
    else:
        print(f"[THEME] {image_path.name} : '{theme}'")
    return {
        "file": image_path.name,
        "wildcard": wildcard,
        "destination": destination,
        "pre_prompt": pre_prompt,
        "post_prompt": post_prompt,
        "prompt_mode": prompt_mode,
    }


def markdown_code(value: str | None) -> str:
    return value if value is not None else "_Not found_"


def write_details(path: Path, details: list[dict]) -> None:
    lines = ["# Image prompt details", ""]
    for item in details:
        lines.extend([
            f"## `{item['file']}`", "",
            f"- Wildcard: `{item['wildcard'] or 'Not found'}`", "",
            f"- Prompt mode: `{item['prompt_mode'] or 'Not found'}`", "",
            f"- Destination path: `{item['destination']}`", "",
            "### Pre-LLM prompt", "", "```text",
            markdown_code(item["pre_prompt"]), "```", "",
            "### Post-LLM prompt", "", "```text",
            markdown_code(item["post_prompt"]), "```", "",
        ])
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[DETAILS] Wrote {path}")


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
        "--pre-node", default="159",
        help="Node containing the expanded pre-LLM prompt (default: '159').",
    )
    parser.add_argument(
        "--post-node", default="1442",
        help="Node containing the post-LLM prompt (default: '1442').",
    )
    parser.add_argument(
        "--details", type=Path,
        default=Path(__file__).resolve().parent / "details.md",
        help="Markdown details output (default: details.md beside the script).",
    )
    parser.add_argument(
        "--mode-switch-node", default="2375",
        help="Any Switch node selecting Narrative/Tags system prompts (default: '2375').",
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

    details = []
    for path in args.images:
        item = process_image(
            path, move=args.move, target_node=args.node,
            pre_node=args.pre_node, post_node=args.post_node,
            mode_switch_node=args.mode_switch_node,
        )
        if item:
            details.append(item)
    write_details(args.details, details)


if __name__ == "__main__":
    main()
