#!/usr/bin/env python3
"""Download and normalize a vocabulary of Danbooru General tags."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "https://safebooru.donmai.us/tags.json"
SAFEBOORU_ORG_API_URL = "https://safebooru.org/index.php"
HUGGINGFACE_REPOSITORY = "newtextdoc1111/danbooru-tag-csv"
HUGGINGFACE_REVISION = "fdf2772213f13d46bff60fc5ebd876e1a811a053"
HUGGINGFACE_FILENAME = "danbooru_tags.csv"
HUGGINGFACE_SHA256 = "a48e1e63b81e8e4fc3091c660fd763e9d05e19beea3b98d1ee78d00ed10ac9d3"
HUGGINGFACE_URL = (
    f"https://huggingface.co/datasets/{HUGGINGFACE_REPOSITORY}/resolve/"
    f"{HUGGINGFACE_REVISION}/{HUGGINGFACE_FILENAME}?download=true"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and normalize a Danbooru General-tag vocabulary."
    )
    parser.add_argument(
        "--output", type=Path, default=Path("safebooru_general_tags.csv"),
        help="Destination CSV (default: safebooru_general_tags.csv)",
    )
    parser.add_argument(
        "--metadata-output", type=Path,
        help="Metadata JSON destination (default: <output>.metadata.json)",
    )
    parser.add_argument(
        "--min-post-count", type=int, default=100,
        help="Exclude tags used by fewer posts (default: 100; use 1 for all active tags)",
    )
    parser.add_argument(
        "--page-size", type=int, default=1000,
        help="Tags requested per API page (default and API maximum: 1000)",
    )
    parser.add_argument(
        "--max-pages", type=int,
        help="Optional maximum pages; produces an intentionally partial list",
    )
    parser.add_argument(
        "--source", choices=("auto", "huggingface", "donmai", "safebooru-org"), default="auto",
        help="Source (default: auto; pinned Hugging Face snapshot, then API fallbacks)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.25,
        help="Seconds between requests (default: 0.25)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help=argparse.SUPPRESS)
    parser.add_argument(
        "--user-agent", default="gkr-wildcard-tag-fetcher/1.0",
        help="HTTP User-Agent identifying the downloader",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.min_post_count < 1:
        parser.error("--min-post-count must be at least 1")
    if not 1 <= args.page_size <= 1000:
        parser.error("--page-size must be between 1 and 1000")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def collect_huggingface_tags(args: argparse.Namespace) -> list[dict[str, int | str]]:
    if args.verbose:
        print(
            f"[danbooru-tags] downloading pinned Hugging Face revision {HUGGINGFACE_REVISION[:8]}",
            file=sys.stderr,
        )
    request = urllib.request.Request(
        HUGGINGFACE_URL,
        headers={"User-Agent": args.user_agent, "Accept": "text/csv,application/octet-stream"},
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        detail = f": {body[:500]}" if body else ""
        raise RuntimeError(f"Hugging Face download failed with HTTP {exc.code}{detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Hugging Face download failed: {exc}") from exc
    digest = hashlib.sha256(payload).hexdigest()
    if digest != HUGGINGFACE_SHA256:
        raise RuntimeError(
            f"Hugging Face CSV checksum mismatch: expected {HUGGINGFACE_SHA256}, received {digest}"
        )
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
        if not reader.fieldnames or not {"tag", "category", "count"}.issubset(reader.fieldnames):
            raise RuntimeError("Hugging Face CSV is missing required tag, category, or count columns")
        selected: dict[str, tuple[int, str]] = {}
        for row in reader:
            try:
                name = row["tag"].strip()
                category = int(row["category"])
                count = int(row["count"].replace(",", ""))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if name and category == 0 and count >= args.min_post_count:
                alias = row.get("alias", "") or ""
                previous = selected.get(name)
                if previous is None or count > previous[0]:
                    selected[name] = (count, alias)
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"Hugging Face CSV is not valid UTF-8: {exc}") from exc
    return [
        {"name": name, "post_count": value[0], "alias": value[1]}
        for name, value in sorted(selected.items(), key=lambda item: (-item[1][0], item[0]))
    ]


def fetch_page(args: argparse.Namespace, page: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({
        "limit": args.page_size,
        "page": page,
        "search[category]": 0,
        "search[hide_empty]": "true",
        "search[is_deprecated]": "false",
        "search[order]": "count",
        "only": "name,post_count,category,is_deprecated",
    })
    request = urllib.request.Request(
        f"{args.api_url}?{query}", headers={"User-Agent": args.user_agent, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        detail = f": {body[:500]}" if body else ""
        raise RuntimeError(f"API request for page {page} failed with HTTP {exc.code}{detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"API request for page {page} failed: {exc}") from exc
    if not isinstance(payload, list) or not all(isinstance(tag, dict) for tag in payload):
        raise RuntimeError(f"API response for page {page} was not a JSON array of tag objects")
    return payload


def fetch_safebooru_org_page(args: argparse.Namespace, page: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({
        "page": "dapi", "s": "tag", "q": "index", "limit": args.page_size, "pid": page,
    })
    request = urllib.request.Request(
        f"{SAFEBOORU_ORG_API_URL}?{query}",
        headers={"User-Agent": args.user_agent, "Accept": "application/xml,text/xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            root = ET.fromstring(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        detail = f": {body[:500]}" if body else ""
        raise RuntimeError(f"Safebooru.org request for page {page} failed with HTTP {exc.code}{detail}") from exc
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        raise RuntimeError(f"Safebooru.org request for page {page} failed: {exc}") from exc
    if root.tag != "tags":
        raise RuntimeError(f"Safebooru.org response for page {page} was not a tag list")
    records: list[dict[str, Any]] = []
    for element in root.findall("tag"):
        try:
            records.append({
                "name": element.attrib["name"],
                "post_count": int(element.attrib["count"]),
                "category": int(element.attrib["type"]),
                "is_deprecated": element.attrib.get("type") == "6",
            })
        except (KeyError, ValueError):
            continue
    return records


def collect_donmai_tags(
    args: argparse.Namespace, first_page: list[dict[str, Any]] | None = None,
) -> list[dict[str, int | str]]:
    selected: dict[str, int] = {}
    page = 1
    while args.max_pages is None or page <= args.max_pages:
        if args.verbose:
            print(f"[danbooru-tags] fetching page {page}", file=sys.stderr)
        records = first_page if page == 1 and first_page is not None else fetch_page(args, page)
        if not records:
            break
        reached_threshold = False
        for record in records:
            name = record.get("name")
            count = record.get("post_count")
            category = record.get("category", 0)
            deprecated = record.get("is_deprecated", False)
            if not isinstance(name, str) or not isinstance(count, int):
                continue
            if category != 0 or deprecated or count < args.min_post_count:
                if isinstance(count, int) and count < args.min_post_count:
                    reached_threshold = True
                continue
            selected[name] = max(count, selected.get(name, 0))
        if args.verbose:
            print(
                f"[danbooru-tags] page {page}: {len(records)} received, {len(selected)} retained total",
                file=sys.stderr,
            )
        if reached_threshold or len(records) < args.page_size:
            break
        page += 1
        if args.delay:
            time.sleep(args.delay)
    return [
        {"name": name, "post_count": count}
        for name, count in sorted(selected.items(), key=lambda item: (-item[1], item[0]))
    ]


def collect_safebooru_org_tags(args: argparse.Namespace) -> list[dict[str, int | str]]:
    selected: dict[str, int] = {}
    page = 0
    while args.max_pages is None or page < args.max_pages:
        if args.verbose:
            print(f"[danbooru-tags] fetching Safebooru.org page {page}", file=sys.stderr)
        records = fetch_safebooru_org_page(args, page)
        if not records:
            break
        for record in records:
            name, count = record.get("name"), record.get("post_count")
            if (
                isinstance(name, str) and isinstance(count, int)
                and record.get("category") == 0 and not record.get("is_deprecated")
                and count >= args.min_post_count
            ):
                selected[name] = max(count, selected.get(name, 0))
        if args.verbose:
            print(
                f"[danbooru-tags] page {page}: {len(records)} received, {len(selected)} retained total",
                file=sys.stderr,
            )
        if len(records) < args.page_size:
            break
        page += 1
        if args.delay:
            time.sleep(args.delay)
    return [
        {"name": name, "post_count": count}
        for name, count in sorted(selected.items(), key=lambda item: (-item[1], item[0]))
    ]


def collect_tags(args: argparse.Namespace) -> tuple[list[dict[str, int | str]], str]:
    if args.source == "huggingface":
        return collect_huggingface_tags(args), "huggingface"
    if args.source == "donmai":
        return collect_donmai_tags(args), "donmai"
    if args.source == "safebooru-org":
        return collect_safebooru_org_tags(args), "safebooru-org"
    try:
        return collect_huggingface_tags(args), "huggingface"
    except RuntimeError as exc:
        print(f"[danbooru-tags] Hugging Face unavailable ({exc}); trying Donmai", file=sys.stderr)
    try:
        first_page = fetch_page(args, 1)
        return collect_donmai_tags(args, first_page), "donmai"
    except RuntimeError as exc:
        print(f"[danbooru-tags] Donmai unavailable ({exc}); falling back to Safebooru.org", file=sys.stderr)
        print("[danbooru-tags] Safebooru.org is not count-ordered; all pages must be scanned", file=sys.stderr)
        return collect_safebooru_org_tags(args), "safebooru-org"


def atomic_write_csv(path: Path, tags: list[dict[str, int | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            fieldnames = ["name", "post_count"] + (["alias"] if any("alias" in tag for tag in tags) else [])
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tags)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    metadata_path = args.metadata_output or args.output.with_name(args.output.name + ".metadata.json")
    try:
        tags, source = collect_tags(args)
        if not tags:
            raise RuntimeError("the API returned no tags matching the requested threshold")
        atomic_write_csv(args.output, tags)
        atomic_write_json(metadata_path, {
            "source_url": {
                "huggingface": HUGGINGFACE_URL,
                "donmai": args.api_url,
                "safebooru-org": SAFEBOORU_ORG_API_URL,
            }[source],
            "source": source,
            "category": "general",
            "category_id": 0,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "minimum_post_count": args.min_post_count,
            "tag_count": len(tags),
            "partial": source != "huggingface" and args.max_pages is not None,
            **({
                "repository": HUGGINGFACE_REPOSITORY,
                "revision": HUGGINGFACE_REVISION,
                "source_sha256": HUGGINGFACE_SHA256,
            } if source == "huggingface" else {}),
        })
    except (OSError, RuntimeError) as exc:
        print(f"download_danbooru_tags: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(tags)} tags to {args.output}")
    print(f"Wrote metadata to {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
