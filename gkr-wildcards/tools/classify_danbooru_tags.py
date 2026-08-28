#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML>=6.0.2"]
# ///

"""Classify tag vocabulary into reusable content profiles with an OpenAI-compatible LLM."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


CLASSES = {"general", "sensitive", "explicit", "ambiguous"}
INSTRUCTION = """Classify every supplied Danbooru-style general tag for an image-prompt vocabulary.
Return JSON only as an array with exactly one object per input id. Each object must contain:
id, content_class (general, sensitive, explicit, or ambiguous), confidence (0 through 1), and reason_code.

Definitions:
- general: appropriate in a general-audience image prompt vocabulary.
- sensitive: non-explicit mature, suggestive, nonsexual anatomical, medical, blood, or disturbing material.
- explicit: explicit sexual acts, explicit sexual anatomy/presentation, fetish content, or extreme graphic material.
- ambiguous: the short tag name is insufficient to classify reliably or has materially different possible meanings.

Classify the tag itself, not an imagined image. Do not infer that a harmless word is mature merely because it could occur in mature art.
Use ambiguous rather than guessing when specialized vocabulary is unclear. Do not omit IDs or add commentary."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", help="Model name; defaults to OPENAI_MODEL")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL; defaults to OPENAI_BASE_URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--overrides", type=Path, help="YAML mapping of tag names to reviewed content classes")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--max-batches", type=int, help="Stop after this many new batches")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "name" not in reader.fieldnames:
            raise ValueError("CSV must contain a name column")
        return list(reader.fieldnames), [dict(row) for row in reader]


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    output_fields = list(fieldnames)
    for name in ("content_class", "classification_confidence", "classification_reason", "classification_source"):
        if name not in output_fields:
            output_fields.append(name)
    temporary = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def load_overrides(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("overrides", data) if isinstance(data, dict) else {}
    result: dict[str, str] = {}
    for tag, value in raw.items():
        classification = value.get("class") if isinstance(value, dict) else value
        if classification not in CLASSES:
            raise ValueError(f"invalid override class for {tag}: {classification}")
        result[str(tag)] = str(classification)
    return result


def request_batch(
    endpoint: str, api_key: str, model: str, items: list[dict[str, str]], timeout: int,
) -> list[dict[str, Any]]:
    body = json.dumps({
        "model": model, "temperature": 0,
        "messages": [
            {"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
        ],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"].strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
        result = json.loads(cleaned)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"classification request failed: HTTP {exc.code}: {detail[:2000]}") from exc
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"classification request failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise RuntimeError("classification response must be a JSON array of objects")
    return result


def main() -> int:
    args = parse_args()
    try:
        if args.batch_size < 1 or args.timeout < 1:
            raise ValueError("batch size and timeout must be positive")
        source_fields, rows = read_rows(args.csv.expanduser().resolve())
        output = args.output.expanduser().resolve()
        if output.exists() and not args.no_resume:
            _, prior_rows = read_rows(output)
            prior = {row.get("name", ""): row for row in prior_rows if row.get("content_class") in CLASSES}
            for row in rows:
                if row.get("name") in prior:
                    for key in ("content_class", "classification_confidence", "classification_reason", "classification_source"):
                        row[key] = prior[row["name"]].get(key, "")
        overrides = load_overrides(args.overrides.expanduser().resolve() if args.overrides else None)
        for row in rows:
            if row.get("name") in overrides:
                row.update({
                    "content_class": overrides[row["name"]], "classification_confidence": "1",
                    "classification_reason": "manual_override", "classification_source": "override",
                })
        pending = [row for row in rows if row.get("content_class") not in CLASSES]
        model = args.model or os.getenv("OPENAI_MODEL")
        if pending and not model:
            raise ValueError("--model or OPENAI_MODEL is required")
        base_url = (args.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        api_key = os.getenv(args.api_key_env, "")
        completed_batches = 0
        for offset in range(0, len(pending), args.batch_size):
            if args.max_batches is not None and completed_batches >= args.max_batches:
                break
            batch = pending[offset:offset + args.batch_size]
            items = [{"id": str(offset + index), "tag": row["name"]} for index, row in enumerate(batch)]
            reviewed = request_batch(endpoint, api_key, str(model), items, args.timeout)
            by_id = {str(item.get("id", "")): item for item in reviewed}
            for index, row in enumerate(batch):
                item = by_id.get(str(offset + index))
                if item is None or item.get("content_class") not in CLASSES:
                    raise RuntimeError(f"classification response omitted or invalidated tag: {row['name']}")
                confidence = float(item.get("confidence", 0))
                if not 0 <= confidence <= 1:
                    raise RuntimeError(f"classification confidence is invalid for tag: {row['name']}")
                row.update({
                    "content_class": str(item["content_class"]),
                    "classification_confidence": f"{confidence:.4f}",
                    "classification_reason": " ".join(str(item.get("reason_code", "unspecified")).split()),
                    "classification_source": f"llm:{model}",
                })
            completed_batches += 1
            write_rows(output, source_fields, rows)
            if args.verbose:
                complete = sum(row.get("content_class") in CLASSES for row in rows)
                print(f"[tag-classifier] classified {complete}/{len(rows)} tags", file=sys.stderr)
        write_rows(output, source_fields, rows)
        counts = {name: sum(row.get("content_class") == name for row in rows) for name in sorted(CLASSES)}
        counts["unclassified"] = sum(row.get("content_class") not in CLASSES for row in rows)
        print(json.dumps({"output": str(output), "counts": counts}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"classify_danbooru_tags: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
