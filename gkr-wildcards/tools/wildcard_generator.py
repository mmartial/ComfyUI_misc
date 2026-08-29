#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML>=6.0.2", "numpy>=2.0"]
# ///

"""Generate a complete tags-mode wildcard from a commented YAML skeleton."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import yaml

import wildcard_linter as linter
from danbooru_index import DanbooruIndex, EmbeddingClient, SearchResult, build_index


GENERATOR_RE = re.compile(r"^\s*#\s*GENERATOR\s*:\s*(.*?)\s*$", re.I)
CATEGORY_RE = re.compile(r"^  ([A-Za-z0-9_-]+)\s*:\s*(?:\[\s*\])?\s*(?:#.*)?$")
ROOT_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*$")
REFERENCE_RE = re.compile(r"__([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)__")
UNDERSCORE_TAG_RE = re.compile(
    r"(?<![A-Za-z0-9_'-])([a-z0-9][a-z0-9_'-]*_(?:[a-z0-9_'-]+(?:\([a-z0-9_'-]+\))?|\([a-z0-9_'-]+\)))(?![A-Za-z0-9_'-])"
)
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
KINDS = {"component", "combo", "scene", "spotlight", "router"}
DEFAULT_COUNTS = {"component": 20, "combo": 12, "scene": 12, "spotlight": 50, "router": 0}


@dataclass
class SkeletonCategory:
    name: str
    directives: list[str] = field(default_factory=list)


@dataclass
class Skeleton:
    path: Path
    namespace: str
    source: str
    header: str
    global_directives: list[str]
    categories: list[SkeletonCategory]


@dataclass
class CategoryPlan:
    name: str
    kind: str
    purpose: str
    count: int
    dependencies: list[str]
    required: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a tags-mode wildcard with staged OpenAI-compatible LLM calls."
    )
    parser.add_argument("skeleton", type=Path, help="Commented YAML skeleton containing one namespace")
    parser.add_argument("--output", type=Path, required=True, help="Destination for the initial generated YAML")
    parser.add_argument("--fixed-output", type=Path, help="Repaired YAML (defaults to <output-stem>.fixed.yaml)")
    parser.add_argument("--danbooru-tags", type=Path, required=True, help="Local Danbooru-compatible tag CSV")
    parser.add_argument("--danbooru-index", type=Path, help="Reusable SQLite tag index (defaults beside CSV)")
    parser.add_argument(
        "--content-profile", choices=("general", "sensitive", "unrestricted"), default="general",
        help="Candidate-vocabulary content profile (default: general)",
    )
    parser.add_argument(
        "--retrieval", choices=("auto", "lexical", "hybrid"), default="auto",
        help="Use embeddings when available (auto), disable them, or require them",
    )
    parser.add_argument("--embedding-model", help="Query embedding model; defaults to index metadata")
    parser.add_argument("--embedding-base-url", help="Query embedding base URL; defaults to index metadata")
    parser.add_argument("--embedding-api-key-env", default="OLLAMA_API_KEY")
    parser.add_argument("--embedding-query-prefix", help="Query prefix; defaults to index metadata")
    parser.add_argument("--retrieval-candidates", type=int, default=12)
    parser.add_argument("--prompt", type=Path, help="Policy Markdown (defaults to ../prompt.md)")
    parser.add_argument("--rules", type=Path, help="General linter rules (defaults beside script)")
    parser.add_argument("--tags-rules", type=Path, help="Tags-mode rules (defaults beside script)")
    parser.add_argument("--model", help="Model name; defaults to OPENAI_MODEL")
    parser.add_argument("--base-url", help="API base URL; defaults to OPENAI_BASE_URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--batch-categories", type=int, default=3)
    parser.add_argument(
        "--category-chunk-size", type=int, default=25,
        help="Maximum concepts/leaves requested per category in one LLM response (default: 25)",
    )
    parser.add_argument("--max-generation-calls", type=int, default=20)
    parser.add_argument(
        "--max-planner-retries", type=int, default=1,
        help="Corrective retries when the generated category plan fails graph validation (default: 1)",
    )
    parser.add_argument(
        "--max-category-retries", type=int, default=1,
        help="Corrective retries for a category whose generated leaves fail validation (default: 1)",
    )
    parser.add_argument(
        "--interactive", action="store_true",
        help="After retries fail, prompt to accept or replace invalid tags without palette checks",
    )
    parser.add_argument(
        "--interactive-overrides", type=Path,
        help="Persistent interactive decisions (defaults to <output-stem>.interactive-overrides.json)",
    )
    parser.add_argument("--max-repair-passes", type=int, default=2)
    parser.add_argument("--max-category-depth", type=int, default=6)
    parser.add_argument("--max-added-categories", type=int, default=30)
    parser.add_argument("--max-total-tokens", type=int, help="Stop new calls after reported total usage reaches this value")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=20, help="Leaves per review/repair request")
    parser.add_argument("--verification-batch-size", type=int, default=15)
    parser.add_argument("--canonical-tag-candidate-count", type=int, default=5)
    parser.add_argument("--canonical-tag-style", choices=("underscore", "spaces"), default="underscore")
    parser.add_argument("--manifest", type=Path, help="Generation manifest (defaults beside output)")
    parser.add_argument("--report", type=Path, help="Post-repair Markdown report (defaults to <output-stem>.fixed-report.md)")
    parser.add_argument("--llm-log", type=Path, help="Sanitized JSONL request/response trace")
    parser.add_argument("--llm-cache-dir", type=Path)
    parser.add_argument("--no-llm-cache", action="store_true")
    parser.add_argument("--llm-cache-max-age-minutes", type=float)
    parser.add_argument("--skip-semantic-review", action="store_true")
    parser.add_argument("--skip-fix-verification", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def log(args: argparse.Namespace, message: str) -> None:
    if args.verbose:
        print(f"[wildcard-generator] {message}", file=sys.stderr)


def parse_skeleton(path: Path) -> Skeleton:
    source = path.read_text(encoding="utf-8")
    if not re.search(r"MODE\s*:\s*tags\b", source, re.I):
        raise ValueError("skeleton must declare MODE: tags in its header")
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid skeleton YAML: {exc}") from exc
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError("skeleton must contain exactly one namespace mapping")
    namespace, mapping = next(iter(data.items()))
    if not isinstance(namespace, str) or not SAFE_NAME_RE.fullmatch(namespace) or not isinstance(mapping, dict):
        raise ValueError("skeleton namespace must contain a category mapping")
    nonempty = [str(name) for name, values in mapping.items() if values not in (None, [])]
    if nonempty:
        raise ValueError("skeleton categories must be empty lists: " + ", ".join(nonempty))

    lines = source.splitlines()
    root_index = next((index for index, line in enumerate(lines) if ROOT_RE.match(line)), None)
    if root_index is None:
        raise ValueError("could not locate namespace line")
    header = "\n".join(lines[:root_index]).rstrip()
    global_directives = [match.group(1) for line in lines[:root_index] if (match := GENERATOR_RE.match(line))]
    categories: list[SkeletonCategory] = []
    pending: list[str] = []
    for line in lines[root_index + 1:]:
        directive = GENERATOR_RE.match(line)
        if directive:
            pending.append(directive.group(1))
            continue
        category = CATEGORY_RE.match(line)
        if category:
            categories.append(SkeletonCategory(category.group(1), pending))
            pending = []
        elif line.strip() and not line.lstrip().startswith("#"):
            pending = []
    if not categories:
        raise ValueError("skeleton must declare at least one empty category")
    if len({category.name for category in categories}) != len(categories):
        raise ValueError("skeleton contains duplicate categories")
    return Skeleton(path.resolve(), namespace, source, header, global_directives, categories)


def infer_kind(name: str) -> str:
    lowered = name.lower()
    if (
        lowered == "random" or lowered.startswith("random_")
        or lowered.endswith("_random") or lowered.endswith("_router")
    ):
        return "router"
    if "spotlight" in lowered or "iconic" in lowered:
        return "spotlight"
    if "combo" in lowered:
        return "combo"
    if "scene" in lowered:
        return "scene"
    return "component"


def requested_count(directives: Iterable[str], kind: str) -> int:
    for directive in directives:
        match = re.search(
            r"\b(\d{1,4})\b[^.\n]{0,120}?\b"
            r"(?:leaves|items|options|entries|prompts|concepts)\b",
            directive, re.I,
        )
        if match:
            return int(match.group(1))
    return DEFAULT_COUNTS[kind]


def has_explicit_count(directives: Iterable[str]) -> bool:
    return any(
        re.search(
            r"\b\d{1,4}\b[^.\n]{0,120}?\b"
            r"(?:leaves|items|options|entries|prompts|concepts)\b",
            value, re.I,
        )
        for value in directives
    )


def planner_items(skeleton: Skeleton, args: argparse.Namespace) -> list[dict[str, Any]]:
    return [{
        "id": "plan",
        "namespace": skeleton.namespace,
        "global_generator_instructions": skeleton.global_directives,
        "required_categories": [
            {
                "name": category.name,
                "generator_instructions": category.directives,
                "inferred_kind": infer_kind(category.name),
                "default_count": requested_count(category.directives, infer_kind(category.name)),
            }
            for category in skeleton.categories
        ],
        "limits": {
            "max_added_categories": args.max_added_categories,
            "max_category_depth": args.max_category_depth,
            "defaults": DEFAULT_COUNTS,
        },
    }]


def parse_plan(result: dict[str, Any], skeleton: Skeleton, args: argparse.Namespace) -> list[CategoryPlan]:
    raw_categories = result.get("categories")
    if not isinstance(raw_categories, list):
        raise ValueError("planner response must contain a categories array")
    required_by_name = {category.name: category for category in skeleton.categories}
    plans: list[CategoryPlan] = []
    seen: set[str] = set()
    for raw in raw_categories:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", ""))
        if not SAFE_NAME_RE.fullmatch(name) or name in seen:
            continue
        kind = str(raw.get("kind", infer_kind(name))).lower()
        if kind not in KINDS:
            kind = infer_kind(name)
        if name in required_by_name and infer_kind(name) == "router":
            # A required public route is structural, not a planner preference.
            kind = "router"
        directive_source = required_by_name[name].directives if name in required_by_name else []
        fallback_count = requested_count(directive_source, kind)
        if has_explicit_count(directive_source):
            count = fallback_count
        else:
            try:
                count = int(raw.get("count", fallback_count))
            except (TypeError, ValueError):
                count = fallback_count
        if kind == "router":
            count = max(0, count)
        else:
            count = max(1, min(count, 500))
        dependencies = [
            str(value) for value in raw.get("dependencies", [])
            if isinstance(value, str) and SAFE_NAME_RE.fullmatch(value) and value != name
        ]
        plans.append(CategoryPlan(
            name=name, kind=kind, purpose=" ".join(str(raw.get("purpose", "")).split()),
            count=count, dependencies=list(dict.fromkeys(dependencies)), required=name in required_by_name,
        ))
        seen.add(name)

    for name, category in required_by_name.items():
        if name not in seen:
            kind = infer_kind(name)
            plans.append(CategoryPlan(
                name, kind, " ".join(category.directives), requested_count(category.directives, kind), [], True
            ))
            seen.add(name)
    added = [plan for plan in plans if not plan.required]
    if len(added) > args.max_added_categories:
        allowed = {plan.name for plan in plans if plan.required} | {plan.name for plan in added[:args.max_added_categories]}
        plans = [plan for plan in plans if plan.name in allowed]
    names = {plan.name for plan in plans}
    for plan in plans:
        plan.dependencies = [name for name in plan.dependencies if name in names]
    complete_routes = [plan.name for plan in plans if plan.kind in {"combo", "scene", "spotlight"}]
    for plan in plans:
        if plan.kind == "router" and not plan.dependencies:
            plan.dependencies = [name for name in complete_routes if name != plan.name]
    validate_plan_routes(plans)
    validate_plan_depth(plans, args.max_category_depth)
    return plans


def validate_plan_routes(plans: list[CategoryPlan]) -> None:
    """Require every planned category to contribute to a router-reachable route."""
    by_name = {plan.name: plan for plan in plans}
    roots = [plan.name for plan in plans if plan.kind == "router"]
    if not roots:
        return
    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(by_name[name].dependencies)
    unused = sorted(set(by_name) - reachable)
    if unused:
        raise ValueError(
            "planner produced categories unreachable from any router: " + ", ".join(unused)
        )


def validate_plan_depth(plans: list[CategoryPlan], maximum: int) -> None:
    by_name = {plan.name: plan for plan in plans}
    memo: dict[str, int] = {}

    def depth(name: str, visiting: set[str]) -> int:
        if name in memo:
            return memo[name]
        if name in visiting:
            raise ValueError(f"planner produced a category cycle involving {name}")
        dependencies = by_name[name].dependencies
        value = 1 + max((depth(dep, visiting | {name}) for dep in dependencies), default=0)
        memo[name] = value
        return value

    deepest = max((depth(name, set()) for name in by_name), default=0)
    if deepest > maximum:
        raise ValueError(f"planner produced category depth {deepest}; maximum is {maximum}")


def llm_args(args: argparse.Namespace) -> argparse.Namespace:
    """Supply the complete argument surface expected by imported linter helpers."""
    values = vars(args).copy()
    values.update({
        "llm_scope": "content", "fix_rules": "", "fix_severity": "both",
        "canonical_tag_suggestions": True, "suggest_fixes": True,
    })
    return SimpleNamespace(**values)


class Session:
    def __init__(self, args: argparse.Namespace, trace_path: Path | None):
        self.args = args
        self.linter_args = llm_args(args)
        self.trace_path = trace_path
        self.model = args.model or os.getenv("OPENAI_MODEL")
        self.base_url = (args.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv(args.api_key_env)
        if not self.model:
            raise ValueError("--model or OPENAI_MODEL is required")
        if not self.api_key:
            raise ValueError(f"{args.api_key_env} is required")
        self.endpoint = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        self.calls = 0
        self.reported_tokens = 0
        self.events: list[dict[str, Any]] = []
        self.provenance: dict[str, list[dict[str, Any]]] = {}
        self.generation_issues: list[dict[str, Any]] = []
        default_override_path = args.output.expanduser().resolve().with_suffix("").with_name(
            args.output.expanduser().resolve().with_suffix("").name + ".interactive-overrides.json"
        )
        self.interactive_override_path = (
            args.interactive_overrides.expanduser().resolve()
            if args.interactive_overrides else default_override_path
        )
        self.interactive_overrides = self.load_interactive_overrides()
        self.linter_args.llm_usage_callback = self.record_usage

    def load_interactive_overrides(self) -> dict[str, dict[str, str]]:
        if not self.interactive_override_path.is_file():
            return {}
        try:
            raw = json.loads(self.interactive_override_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid interactive override file {self.interactive_override_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"interactive override file must contain an object: {self.interactive_override_path}")
        overrides: dict[str, dict[str, str]] = {}
        for category, mapping in raw.items():
            if not isinstance(category, str) or not isinstance(mapping, dict):
                raise ValueError(f"invalid interactive override mapping in {self.interactive_override_path}")
            if not all(isinstance(old, str) and isinstance(new, str) for old, new in mapping.items()):
                raise ValueError(f"interactive override tags must be strings in {self.interactive_override_path}")
            overrides[category] = dict(mapping)
        return overrides

    def save_interactive_overrides(self) -> None:
        self.interactive_override_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.interactive_override_path.parent,
            prefix=self.interactive_override_path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(self.interactive_overrides, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary_path, self.interactive_override_path)

    def record_usage(self, usage: dict[str, Any]) -> None:
        try:
            self.reported_tokens += int(usage.get("total_tokens", 0))
        except (TypeError, ValueError):
            return

    def check_token_budget(self) -> None:
        if self.args.max_total_tokens is not None and self.reported_tokens >= self.args.max_total_tokens:
            raise RuntimeError(f"reported token budget reached ({self.args.max_total_tokens})")

    def request(self, call_name: str, instruction: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.calls >= self.args.max_generation_calls:
            raise RuntimeError(f"maximum generation calls reached ({self.args.max_generation_calls})")
        self.check_token_budget()
        self.calls += 1
        started = time.perf_counter()
        result = linter.llm_json_request(
            args=self.linter_args, endpoint=self.endpoint, api_key=self.api_key, model=self.model,
            instruction=instruction, items=items, call_name=call_name,
            batch_number=self.calls, batch_total=self.args.max_generation_calls, offset=0,
            trace_path=self.trace_path, response_event=f"{call_name}_response",
        )
        self.events.append({
            "call": self.calls, "name": call_name, "items": len(items),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        })
        return result


def planning_instruction(policy: str) -> str:
    return (
        "Design a category graph for a new ComfyUI Dynamic Prompts wildcard. Return JSON only: an array containing "
        "one object with id='plan' and a categories array. Every category object contains name, kind "
        "(component, combo, scene, spotlight, or router), purpose, count, and dependencies (category names). "
        "Preserve every required category exactly. Add only useful supporting categories and stay within the supplied limits. "
        "A dependency means that the category must reference that category at least once across its leaves. Routers select complete routes. Combos and scenes "
        "must resolve to coherent single images. Keep the graph acyclic and no deeper than the supplied limit. This generator "
        "supports tags mode only. Do not generate leaves yet. Follow all GENERATOR instructions.\n\nPOLICY:\n" + policy
    )


def generate_valid_plan(
    session: Session, skeleton: Skeleton, policy: str,
) -> list[CategoryPlan]:
    """Request a plan and retry with deterministic graph-validation feedback."""
    item = planner_items(skeleton, session.args)[0]
    result: dict[str, Any] = {}
    for attempt in range(session.args.max_planner_retries + 1):
        if attempt == 0:
            response = session.request("generation plan", planning_instruction(policy), [item])
        else:
            correction_item = dict(item)
            correction_item["rejected_response"] = result
            correction_item["validation_error"] = validation_error
            correction_item["correction_attempt"] = attempt
            instruction = planning_instruction(policy) + (
                "\n\nCORRECTION: The previous plan failed deterministic graph validation. Fix every issue in "
                "validation_error. Preserve all required categories, classify public random routes as routers, ensure every "
                "category is reachable from a router through dependencies, keep the graph acyclic, and return the complete "
                "corrected plan object."
            )
            response = session.request("generation plan correction", instruction, [correction_item])
        if len(response) != 1:
            validation_error = "planner did not return exactly one plan"
            result = response[0] if response else {}
        else:
            result = response[0]
            try:
                return parse_plan(result, skeleton, session.args)
            except ValueError as exc:
                validation_error = str(exc)
        if attempt >= session.args.max_planner_retries:
            raise ValueError(validation_error)
        log(
            session.args,
            f"plan rejected: {validation_error}; corrective retry {attempt + 1}/{session.args.max_planner_retries}",
        )
    raise AssertionError("planner retry loop exhausted unexpectedly")


def generation_instruction(policy: str, vocabulary: linter.DanbooruVocabulary) -> str:
    sample_note = (
        f"A local canonical vocabulary with {len(vocabulary)} tags is available to deterministic validation. "
        "Use established Danbooru underscore tags when confidently known and visually equivalent, but never invent an underscore "
        "tag. Use a compact literal phrase with spaces whenever no canonical tag exists or canonicalization would lose content."
    )
    return (
        "Realize supplied visual concepts as wildcard leaves. Return JSON only as an array with exactly one object per input ID, "
        "containing id, leaves (an array of strings), and provenance (one entry per leaf listing canonical_tags and literal_fallbacks). "
        "Return exactly requested_count leaves. A wildcard reference has exact form __namespace/category__. Reference only "
        "allowed_dependencies. Component leaves must contain literal visible content and no references unless the category purpose "
        "explicitly requires composition. Combo/scene leaves may combine allowed references with compact literal tag phrases. Router "
        "leaves are generated deterministically and are not supplied. Spotlight leaves are complete, high-specificity, single-image prompts. "
        "Use every allowed dependency at least once somewhere in the category's leaves so all planned categories contribute to output routes. "
        "Use the candidate palette for each concept as the primary vocabulary. Every underscore-form token must be an exact canonical "
        "tag from that palette. Prefer a canonical candidate whenever it preserves the concept. A literal fallback is allowed only when "
        "no candidate preserves necessary visible meaning; record its text and a brief reason in literal_fallbacks. All literal output is "
        "tags mode: flat comma-separated short visual phrases, 1-3 meaningful weights, no prose, quality filler, negative prompt, or "
        "sequential/multi-panel content. Respect content_profile: general forbids mature, suggestive, explicit, fetish, or graphic "
        "content; sensitive permits non-explicit mature material but not explicit sexual content; unrestricted adds no content "
        "restriction. Avoid duplicate or near-duplicate leaves. Follow the global and local GENERATOR instructions. "
        + sample_note + "\n\nPOLICY:\n" + policy
    )


def concept_instruction(policy: str) -> str:
    return (
        "Design concise visual concepts for each supplied wildcard category; do not write final prompts or Danbooru tags yet. "
        "Return JSON only as an array with exactly one object per input ID containing id and concepts. Each concepts entry contains "
        "summary and search_queries (1-4 short natural-language queries). Return exactly requested_count distinct concepts. Preserve "
        "the category purpose, theme, compatibility requirements, and single-image rule. Focus on the minimum visible ideas needed; "
        "avoid decorative prose and generic quality language. Respect content_profile: general forbids mature, suggestive, explicit, "
        "fetish, or graphic content; sensitive permits non-explicit mature material but not explicit sexual content; unrestricted "
        "adds no content restriction.\n\nPOLICY:\n" + policy
    )


def generate_concepts(
    session: Session, skeleton: Skeleton, batch: list[CategoryPlan], policy: str,
    *, chunk_indexes: dict[str, int] | None = None,
    chunk_offsets: dict[str, int] | None = None,
    total_counts: dict[str, int] | None = None,
    prior_summaries: dict[str, list[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    items = []
    for plan in batch:
        skeleton_category = next((item for item in skeleton.categories if item.name == plan.name), None)
        items.append({
            "id": plan.name, "kind": plan.kind, "purpose": plan.purpose,
            "requested_count": plan.count, "dependencies": plan.dependencies,
            "chunk_index": (chunk_indexes or {}).get(plan.name, 0),
            "chunk_offset": (chunk_offsets or {}).get(plan.name, 0),
            "total_requested_count": (total_counts or {}).get(plan.name, plan.count),
            "avoid_concept_summaries": list((prior_summaries or {}).get(plan.name, [])),
            "content_profile": session.args.content_profile,
            "generator_instructions": skeleton_category.directives if skeleton_category else [],
            "global_generator_instructions": skeleton.global_directives,
        })
    results = session.request("concept generation", concept_instruction(policy), items)
    by_id = {str(item.get("id", "")): item for item in results}
    concepts_by_category: dict[str, list[dict[str, Any]]] = {}
    for plan in batch:
        result = by_id.get(plan.name, {})
        raw = result.get("concepts", [])
        concepts: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                summary = " ".join(item.split())
                queries = [summary]
            elif isinstance(item, dict):
                summary = " ".join(str(item.get("summary", "")).split())
                queries = [
                    " ".join(value.split()) for value in item.get("search_queries", [])
                    if isinstance(value, str) and value.strip()
                ]
            else:
                continue
            if summary:
                concepts.append({"summary": summary, "search_queries": list(dict.fromkeys(queries or [summary]))[:4]})
        unique: list[dict[str, Any]] = []
        seen: set[str] = {
            value.casefold() for value in (prior_summaries or {}).get(plan.name, [])
        }
        for concept in concepts:
            key = concept["summary"].lower()
            if key not in seen:
                unique.append(concept)
                seen.add(key)
        max_retries = getattr(session.args, "max_category_retries", 1)
        for retry in range(max_retries + 1):
            if len(unique) >= plan.count:
                break
            if retry >= max_retries:
                raise RuntimeError(
                    f"category {plan.name} returned {len(unique)} new unique concepts; expected {plan.count} "
                    f"for chunk {(chunk_indexes or {}).get(plan.name, 0) + 1}"
                )
            log(
                session.args,
                f"category {plan.name} returned {len(unique)}/{plan.count} concepts for chunk "
                f"{(chunk_indexes or {}).get(plan.name, 0) + 1}; corrective retry "
                f"{retry + 1}/{max_retries}",
            )
            correction_item = dict(next(item for item in items if item["id"] == plan.name))
            correction_item["rejected_response"] = result
            correction_item["validation_error"] = (
                f"Return exactly {plan.count} concepts that are distinct from avoid_concept_summaries; "
                f"the previous response yielded only {len(unique)} usable concepts."
            )
            correction_item["correction_attempt"] = retry + 1
            corrected = session.request(
                "concept correction",
                concept_instruction(policy) + "\n\nCORRECTION: Return the full corrected object and obey validation_error.",
                [correction_item],
            )
            result = next(
                (entry for entry in corrected if str(entry.get("id", "")) == plan.name), {}
            )
            raw = result.get("concepts", [])
            concepts = []
            for entry in raw if isinstance(raw, list) else []:
                if isinstance(entry, str):
                    summary = " ".join(entry.split())
                    queries = [summary]
                elif isinstance(entry, dict):
                    summary = " ".join(str(entry.get("summary", "")).split())
                    queries = [
                        " ".join(value.split()) for value in entry.get("search_queries", [])
                        if isinstance(value, str) and value.strip()
                    ]
                else:
                    continue
                if summary:
                    concepts.append({"summary": summary, "search_queries": list(dict.fromkeys(queries or [summary]))[:4]})
            unique = []
            seen = {value.casefold() for value in (prior_summaries or {}).get(plan.name, [])}
            for concept in concepts:
                key = concept["summary"].casefold()
                if key not in seen:
                    unique.append(concept)
                    seen.add(key)
        if len(unique) > plan.count:
            removed = unique[plan.count:]
            unique = unique[:plan.count]
            session.generation_issues.append({
                "category": plan.name,
                "rule": "trimmed_excess_generated_concepts",
                "expected_count": plan.count,
                "removed_concepts": removed,
            })
            log(
                session.args,
                f"category {plan.name} returned excess concepts; deterministically kept the first "
                f"{plan.count} and recorded {len(removed)} removed concept(s) for review",
            )
        concepts_by_category[plan.name] = unique
    return concepts_by_category


def retrieve_concepts(
    concepts_by_category: dict[str, list[dict[str, Any]]], index: DanbooruIndex,
    embedding_client: EmbeddingClient | None, query_prefix: str, limit: int,
) -> None:
    queries: list[str] = []
    for concepts in concepts_by_category.values():
        for concept in concepts:
            queries.extend(concept["search_queries"])
    unique_queries = list(dict.fromkeys(queries))
    vectors: dict[str, list[float]] = {}
    if embedding_client and unique_queries:
        embedded = embedding_client.embed([query_prefix + query for query in unique_queries])
        vectors = dict(zip(unique_queries, embedded))
    for concepts in concepts_by_category.values():
        for concept in concepts:
            merged: dict[str, SearchResult] = {}
            for query in concept["search_queries"]:
                for candidate in index.hybrid_search(query, vectors.get(query), limit):
                    current = merged.get(candidate.tag)
                    if current is None or candidate.score > current.score:
                        merged[candidate.tag] = candidate
            ranked = sorted(merged.values(), key=lambda item: (-item.score, -item.post_count, item.tag))[:limit]
            concept["candidates"] = [
                {"tag": item.tag, "match": item.match, "score": round(item.score, 4), "post_count": item.post_count}
                for item in ranked
            ]


def category_batches(plans: list[CategoryPlan], size: int) -> list[list[CategoryPlan]]:
    if size < 1:
        raise ValueError("--batch-categories must be at least 1")
    # Dependencies first makes generated context useful to later composite calls.
    remaining = {plan.name: plan for plan in plans}
    ordered: list[CategoryPlan] = []
    while remaining:
        ready = [plan for plan in remaining.values() if all(dep not in remaining for dep in plan.dependencies)]
        if not ready:
            raise ValueError("category plan is cyclic")
        ready.sort(key=lambda plan: (plan.kind in {"combo", "scene", "spotlight", "router"}, plan.name))
        for plan in ready:
            ordered.append(plan)
            del remaining[plan.name]
    return [ordered[index:index + size] for index in range(0, len(ordered), size)]


class CategoryValidationError(RuntimeError):
    """A generated category response that can be corrected by another LLM call."""

    def __init__(
        self, message: str, invalid_tags: set[str] | None = None,
        missing_dependencies: set[str] | None = None,
    ):
        super().__init__(message)
        self.invalid_tags = invalid_tags or set()
        self.missing_dependencies = missing_dependencies or set()


def validate_category_response(
    category: str, result: dict[str, Any], expected_count: int,
    namespace: str, dependencies: list[str], allowed_canonical: set[str],
    require_dependencies: bool = True,
    forbidden_signatures: set[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    raw = result.get("leaves", [])
    provenance = result.get("provenance", [])
    leaves = [" ".join(value.split()) for value in raw if isinstance(value, str) and value.strip()]
    leaves = list(dict.fromkeys(leaves))
    errors: list[str] = []
    if len(leaves) != expected_count:
        errors.append(f"returned {len(leaves)} unique leaves; expected {expected_count}")
    signatures: dict[str, int] = {}
    normalized_duplicates: list[str] = []
    for index, leaf in enumerate(leaves, 1):
        signature = linter.duplicate_leaf_signature(leaf, "tags")
        if signature in signatures:
            normalized_duplicates.append(f"{signatures[signature]} and {index}")
        else:
            signatures[signature] = index
    if normalized_duplicates:
        errors.append(
            "contains normalized duplicate leaves at positions "
            + ", ".join(normalized_duplicates)
        )
    repeated_from_prior = sorted(
        index for signature, index in signatures.items()
        if signature in (forbidden_signatures or set())
    )
    if repeated_from_prior:
        errors.append(
            "duplicates leaves from earlier chunks at positions "
            + ", ".join(str(index) for index in repeated_from_prior)
        )
    if not isinstance(provenance, list) or not all(isinstance(item, dict) for item in provenance):
        errors.append("provenance must be an array containing one object per leaf")
        provenance = []
    elif len(provenance) != len(leaves):
        errors.append(f"returned {len(provenance)} provenance objects for {len(leaves)} leaves")

    invalid_provenance: set[str] = set()
    for entry in provenance:
        canonical_tags = entry.get("canonical_tags", [])
        literal_fallbacks = entry.get("literal_fallbacks", [])
        if not isinstance(canonical_tags, list) or not isinstance(literal_fallbacks, list):
            errors.append("provenance fields canonical_tags and literal_fallbacks must be arrays")
            continue
        invalid_provenance.update(
            str(tag) for tag in canonical_tags if str(tag) not in allowed_canonical
        )
    if invalid_provenance:
        errors.append(
            "provenance cites tags outside the retrieved palette: "
            + ", ".join(sorted(invalid_provenance))
        )

    allowed_refs = {(namespace, dependency) for dependency in dependencies}
    invalid_refs: set[str] = set()
    used_dependencies: set[str] = set()
    invalid_output_tags: set[str] = set()
    for leaf in leaves:
        refs = set(REFERENCE_RE.findall(leaf))
        invalid_refs.update(
            f"__{ref_namespace}/{ref_category}__"
            for ref_namespace, ref_category in refs - allowed_refs
        )
        used_dependencies.update(
            ref_category for ref_namespace, ref_category in refs if ref_namespace == namespace
        )
        literal = linter.literal_text(leaf)
        invalid_output_tags.update(
            token
            for token in UNDERSCORE_TAG_RE.findall(literal)
            if token not in allowed_canonical
        )
    if invalid_refs:
        errors.append("uses undeclared references: " + ", ".join(sorted(invalid_refs)))
    missing_dependencies = sorted(set(dependencies) - used_dependencies)
    if require_dependencies and missing_dependencies:
        errors.append(
            "does not use declared dependencies: " + ", ".join(missing_dependencies)
        )
    if invalid_output_tags:
        errors.append(
            "uses underscore tags outside the retrieved palette: "
            + ", ".join(sorted(invalid_output_tags))
        )
    if errors:
        raise CategoryValidationError(
            f"category {category}: " + "; ".join(errors),
            invalid_provenance | invalid_output_tags,
            set(missing_dependencies),
        )
    return leaves, provenance


def trim_excess_category_response(
    result: dict[str, Any], expected_count: int,
) -> tuple[dict[str, Any], list[str]]:
    """Deterministically align and trim an otherwise structured overlong response."""
    leaves = result.get("leaves", [])
    provenance = result.get("provenance", [])
    if (
        not isinstance(leaves, list) or len(leaves) <= expected_count
        or not isinstance(provenance, list) or len(provenance) < expected_count
    ):
        return result, []
    removed = [str(leaf) for leaf in leaves[expected_count:]]
    trimmed = dict(result)
    trimmed["leaves"] = leaves[:expected_count]
    trimmed["provenance"] = provenance[:expected_count]
    return trimmed, removed


def apply_interactive_tag_overrides(
    category: str, result: dict[str, Any], invalid_tags: set[str],
    input_fn: Any = input, existing: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Prompt for explicit tag overrides and apply them to leaves and provenance."""
    replacements = {
        tag: replacement for tag, replacement in (existing or {}).items() if tag in invalid_tags
    }
    missing_tags = sorted(invalid_tags - set(replacements))
    if replacements:
        log_message = ", ".join(f"{tag} -> {replacement}" for tag, replacement in sorted(replacements.items()))
        print(
            f"[wildcard-generator] reused interactive overrides for category {category}: {log_message}",
            file=sys.stderr,
        )
    if missing_tags:
        print(f"[wildcard-generator] interactive overrides for category {category}", file=sys.stderr)
    leaves = result.get("leaves", [])
    provenance = result.get("provenance", [])

    def tag_expression(tag: str) -> re.Pattern[str]:
        # A bare invalid tag such as ``animal`` must not match the qualifier in
        # a different canonical tag such as ``mole_(animal)``.
        return re.compile(
            rf"(?<!_\()(?<![A-Za-z0-9_-]){re.escape(tag)}(?![A-Za-z0-9_-])"
        )

    for invalid_tag in missing_tags:
        affected_indexes: set[int] = set()
        if isinstance(leaves, list):
            expression = tag_expression(invalid_tag)
            affected_indexes.update(
                index for index, leaf in enumerate(leaves)
                if isinstance(leaf, str) and expression.search(leaf)
            )
        if isinstance(provenance, list):
            affected_indexes.update(
                index for index, entry in enumerate(provenance)
                if isinstance(entry, dict)
                and isinstance(entry.get("canonical_tags"), list)
                and invalid_tag in {str(tag) for tag in entry["canonical_tags"]}
            )
        context_lines = [
            f"  Leaf {index + 1}: {leaves[index]}"
            for index in sorted(affected_indexes)
            if isinstance(leaves, list) and index < len(leaves) and isinstance(leaves[index], str)
        ]
        context = "\n".join(context_lines) or "  Leaf context unavailable"
        replacement = input_fn(
            f"\nInvalid tag '{invalid_tag}' in category '{category}':\n{context}\n"
            "Enter a replacement, or press Enter to accept it unchanged: "
        ).strip()
        replacements[invalid_tag] = replacement or invalid_tag

    corrected = dict(result)
    def replace_leaf_tags(leaf: str) -> str:
        for old, new in replacements.items():
            leaf = tag_expression(old).sub(new, leaf)
        return leaf

    corrected["leaves"] = [
        replace_leaf_tags(leaf) if isinstance(leaf, str) else leaf for leaf in leaves
    ] if isinstance(leaves, list) else leaves
    corrected_provenance: list[Any] = []
    if isinstance(provenance, list):
        for entry in provenance:
            if not isinstance(entry, dict):
                corrected_provenance.append(entry)
                continue
            corrected_entry = dict(entry)
            canonical_tags = entry.get("canonical_tags", [])
            if isinstance(canonical_tags, list):
                corrected_entry["canonical_tags"] = [
                    replacements.get(str(tag), str(tag)) for tag in canonical_tags
                ]
            corrected_provenance.append(corrected_entry)
        corrected["provenance"] = corrected_provenance
    return corrected, replacements


def generate_categories(
    session: Session, skeleton: Skeleton, plans: list[CategoryPlan], policy: str,
    vocabulary: linter.DanbooruVocabulary, index: DanbooruIndex,
    embedding_client: EmbeddingClient | None, embedding_query_prefix: str,
) -> dict[str, list[str]]:
    generated: dict[str, list[str]] = {}
    plan_by_name = {plan.name: plan for plan in plans}
    chunk_size = getattr(session.args, "category_chunk_size", 25)
    if chunk_size < 1:
        raise ValueError("--category-chunk-size must be at least 1")
    for batch in category_batches(plans, session.args.batch_categories):
        routers = [plan for plan in batch if plan.kind == "router"]
        content_batch = [plan for plan in batch if plan.kind != "router"]
        for plan in routers:
            generated[plan.name] = [f"__{skeleton.namespace}/{name}__" for name in plan.dependencies]
            log(session.args, f"generated {plan.name}: {len(generated[plan.name])} router leaves")
        if not content_batch:
            continue
        accumulated_leaves = {plan.name: [] for plan in content_batch}
        accumulated_provenance = {plan.name: [] for plan in content_batch}
        accumulated_summaries = {plan.name: [] for plan in content_batch}
        chunk_index = 0
        while any(len(accumulated_leaves[plan.name]) < plan.count for plan in content_batch):
            active_originals = [
                plan for plan in content_batch if len(accumulated_leaves[plan.name]) < plan.count
            ]
            chunk_plans = [
                CategoryPlan(
                    plan.name, plan.kind, plan.purpose,
                    min(chunk_size, plan.count - len(accumulated_leaves[plan.name])),
                    plan.dependencies,
                )
                for plan in active_originals
            ]
            offsets = {plan.name: len(accumulated_leaves[plan.name]) for plan in active_originals}
            total_counts = {plan.name: plan.count for plan in active_originals}
            indexes = {plan.name: chunk_index for plan in active_originals}
            concepts_by_category = generate_concepts(
                session, skeleton, chunk_plans, policy,
                chunk_indexes=indexes, chunk_offsets=offsets, total_counts=total_counts,
                prior_summaries=accumulated_summaries,
            )
            retrieve_concepts(
                concepts_by_category, index, embedding_client, embedding_query_prefix,
                session.args.retrieval_candidates,
            )
            items: list[dict[str, Any]] = []
            for plan in chunk_plans:
                original = plan_by_name[plan.name]
                skeleton_category = next((item for item in skeleton.categories if item.name == plan.name), None)
                dependency_examples = {name: generated.get(name, [])[:3] for name in plan.dependencies}
                items.append({
                    "id": plan.name, "namespace": skeleton.namespace, "kind": plan.kind,
                    "purpose": plan.purpose, "requested_count": plan.count,
                    "total_requested_count": original.count,
                    "chunk_index": chunk_index, "chunk_offset": offsets[plan.name],
                    "allowed_dependencies": plan.dependencies,
                    "dependency_examples": dependency_examples,
                    "avoid_duplicate_leaves": list(accumulated_leaves[plan.name]),
                    "concepts": concepts_by_category[plan.name],
                    "generator_instructions": skeleton_category.directives if skeleton_category else [],
                    "global_generator_instructions": skeleton.global_directives,
                    "content_profile": session.args.content_profile,
                })
            results = session.request("category generation", generation_instruction(policy, vocabulary), items)
            by_id = {str(item.get("id", "")): item for item in results}
            for plan in chunk_plans:
                original = plan_by_name[plan.name]
                accumulated_summaries[plan.name].extend(
                    concept["summary"] for concept in concepts_by_category[plan.name]
                )
                prior_signatures = {
                    linter.duplicate_leaf_signature(leaf, "tags")
                    for leaf in accumulated_leaves[plan.name]
                }
                allowed_canonical = {
                    str(candidate["tag"])
                    for concept in concepts_by_category[plan.name]
                    for candidate in concept.get("candidates", [])
                }
                result = by_id.get(plan.name, {})
                item = next(item for item in items if item["id"] == plan.name)
                excess_issue: dict[str, Any] | None = None
                for retry in range(session.args.max_category_retries + 1):
                    result, removed_leaves = trim_excess_category_response(result, plan.count)
                    if removed_leaves:
                        excess_issue = {
                            "category": plan.name,
                            "rule": "trimmed_excess_generated_leaves",
                            "chunk_index": chunk_index,
                            "expected_count": plan.count,
                            "removed_leaves": removed_leaves,
                        }
                        session.generation_issues.append(excess_issue)
                    try:
                        leaves, provenance = validate_category_response(
                            plan.name, result, plan.count, skeleton.namespace,
                            plan.dependencies, allowed_canonical,
                            require_dependencies=False,
                            forbidden_signatures=prior_signatures,
                        )
                        break
                    except CategoryValidationError as exc:
                        if retry >= session.args.max_category_retries:
                            if session.args.interactive and exc.invalid_tags:
                                result, replacements = apply_interactive_tag_overrides(
                                    plan.name, result, exc.invalid_tags,
                                    existing=session.interactive_overrides.get(plan.name),
                                )
                                leaves, provenance = validate_category_response(
                                    plan.name, result, plan.count, skeleton.namespace,
                                    plan.dependencies, allowed_canonical | set(replacements.values()),
                                    require_dependencies=False,
                                    forbidden_signatures=prior_signatures,
                                )
                                vocabulary.tags.update(replacements.values())
                                session.interactive_overrides.setdefault(plan.name, {}).update(replacements)
                                session.save_interactive_overrides()
                                break
                            raise
                        log(session.args, f"{exc}; corrective retry {retry + 1}/{session.args.max_category_retries}")
                        correction_item = dict(item)
                        correction_item["rejected_response"] = result
                        correction_item["validation_error"] = str(exc)
                        correction_item["correction_attempt"] = retry + 1
                        corrected = session.request(
                            "category correction",
                            generation_instruction(policy, vocabulary) +
                            "\n\nCORRECTION: Fix every issue in validation_error and return the full corrected object. "
                            "Keep exactly requested_count unique leaves and do not repeat avoid_duplicate_leaves.",
                            [correction_item],
                        )
                        result = next(
                            (entry for entry in corrected if str(entry.get("id", "")) == plan.name), {}
                        )
                accumulated_leaves[plan.name].extend(leaves)
                accumulated_provenance[plan.name].extend(provenance)
                log(
                    session.args,
                    f"generated {plan.name} chunk {chunk_index + 1}: {len(leaves)} leaves "
                    f"({len(accumulated_leaves[plan.name])}/{original.count})",
                )
            chunk_index += 1

        for plan in content_batch:
            leaves = accumulated_leaves[plan.name]
            provenance = accumulated_provenance[plan.name]
            if len(leaves) != plan.count:
                raise RuntimeError(f"category {plan.name} aggregated {len(leaves)} leaves; expected {plan.count}")
            used_dependencies = {
                category for leaf in leaves for namespace, category in REFERENCE_RE.findall(leaf)
                if namespace == skeleton.namespace
            }
            missing_dependencies = sorted(set(plan.dependencies) - used_dependencies)
            if missing_dependencies:
                session.generation_issues.append({
                    "category": plan.name,
                    "rule": "unused_declared_dependencies",
                    "missing_dependencies": missing_dependencies,
                })
                log(
                    session.args,
                    f"category {plan.name} omits declared dependencies after aggregation; "
                    "continuing with an unresolved review finding",
                )
            generated[plan.name] = leaves
            session.provenance[plan.name] = provenance
            log(session.args, f"generated {plan.name}: {len(leaves)} leaves")
    # Retain planner order in rendering, while keeping this sanity check explicit.
    missing = set(plan_by_name) - set(generated)
    if missing:
        raise RuntimeError("generation omitted categories: " + ", ".join(sorted(missing)))
    return generated


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def default_artifact_paths(output: Path) -> tuple[Path, Path, Path]:
    base = output.with_suffix("")
    return (
        base.with_name(base.name + ".fixed.yaml"),
        base.with_name(base.name + ".fixed-report.md"),
        base.with_name(base.name + ".generation.json"),
    )


def render_wildcard(skeleton: Skeleton, plans: list[CategoryPlan], content: dict[str, list[str]]) -> str:
    required = {category.name: category for category in skeleton.categories}
    lines = [skeleton.header, f"{skeleton.namespace}:", ""] if skeleton.header else [f"{skeleton.namespace}:", ""]
    for plan in plans:
        if plan.name in required:
            for directive in required[plan.name].directives:
                lines.append(f"  # GENERATOR: {directive}")
        else:
            lines.append("  # Added by wildcard_generator.py to support the requested theme and routes.")
        lines.append(f"  {plan.name}:")
        for leaf in content.get(plan.name, []):
            lines.append(f"    - {yaml_quote(leaf)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def deterministic_findings(
    path: Path, rules: dict[str, Any], tags_rules: dict[str, Any],
    vocabulary: linter.DanbooruVocabulary, candidate_count: int, style: str,
) -> tuple[list[linter.Leaf], list[linter.Finding]]:
    leaves, categories, findings = linter.load_inventory([path])
    findings.extend(linter.pattern_findings(leaves, rules))
    findings.extend(linter.tags_mode_findings(leaves, tags_rules))
    findings.extend(linter.duplicate_leaf_findings(leaves))
    findings.extend(linter.canonical_tag_findings(
        leaves, vocabulary, candidate_count, style, tags_rules.get("canonical_composition")
    ))
    findings.extend(linter.graph_findings(leaves, categories))
    findings.extend(linter.route_motif_findings(categories, rules))
    findings.extend(linter.namespace_policy_findings(leaves, categories, rules))
    return leaves, findings


def apply_leaf_rewrites(content: dict[str, list[str]], leaves: list[linter.Leaf], rewrites: dict[str, str]) -> int:
    applied = 0
    for leaf in leaves:
        if leaf.uid in rewrites and leaf.category in content and leaf.index < len(content[leaf.category]):
            content[leaf.category][leaf.index] = rewrites[leaf.uid]
            applied += 1
    return applied


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    prompt_path = (args.prompt or script_dir.parent / "prompt.md").resolve()
    rules_path = (args.rules or script_dir / "rules.yaml").resolve()
    tags_rules_path = (args.tags_rules or script_dir / "tags-rules.yaml").resolve()
    output = args.output.expanduser().resolve()
    default_fixed, default_report, default_manifest = default_artifact_paths(output)
    fixed_output = (args.fixed_output or default_fixed).expanduser().resolve()
    manifest_path = (args.manifest or default_manifest).expanduser().resolve()
    report_path = (args.report or default_report).expanduser().resolve()
    trace_path = args.llm_log.expanduser().resolve() if args.llm_log else None
    manifest: dict[str, Any] = {
        "version": 1, "status": "starting", "output": str(output),
        "fixed_output": str(fixed_output), "started": time.time(),
        "generation_calls": [], "unresolved": [],
    }
    content: dict[str, list[str]] = {}
    plans: list[CategoryPlan] = []
    skeleton: Skeleton | None = None
    session: Session | None = None
    tag_index: DanbooruIndex | None = None
    final_findings: list[linter.Finding] = []
    leaves: list[linter.Leaf] = []
    try:
        if (
            args.max_category_depth < 1 or args.max_added_categories < 0
            or args.max_generation_calls < 1 or args.max_category_retries < 0
            or args.max_planner_retries < 0
            or args.category_chunk_size < 1
        ):
            raise ValueError(
                "generation limits must be positive (added-category, planner-retry, and category-retry limits may be zero)"
            )
        skeleton = parse_skeleton(args.skeleton.expanduser().resolve())
        policy = prompt_path.read_text(encoding="utf-8")
        rules = linter.load_rules(rules_path)
        tags_rules = linter.load_rules(tags_rules_path)
        csv_path = args.danbooru_tags.expanduser().resolve()
        full_vocabulary = linter.load_danbooru_tags(csv_path)
        index_path = (args.danbooru_index or csv_path.with_suffix(".index.sqlite")).expanduser().resolve()
        if not index_path.exists():
            info = build_index(csv_path, index_path, args.content_profile)
            log(args, f"built lexical tag index with {info['tag_count']} tags")
        tag_index = DanbooruIndex(index_path)
        if (
            not tag_index.compatible_with_csv(csv_path)
            or tag_index.metadata.get("content_profile") != args.content_profile
        ):
            tag_index.close()
            info = build_index(csv_path, index_path, args.content_profile)
            log(args, f"rebuilt stale lexical tag index with {info['tag_count']} tags")
            tag_index = DanbooruIndex(index_path)
        allowed_tags = tag_index.canonical_names()
        vocabulary = linter.DanbooruVocabulary(
            allowed_tags,
            {name: count for name, count in full_vocabulary.post_counts.items() if name in allowed_tags},
            {
                alias: targets & allowed_tags for alias, targets in full_vocabulary.aliases.items()
                if targets & allowed_tags
            },
        )
        embedding_client: EmbeddingClient | None = None
        embedding_query_prefix = str(
            args.embedding_query_prefix
            if args.embedding_query_prefix is not None
            else tag_index.metadata.get("embedding_query_prefix", "")
        )
        retrieval_mode = "lexical"
        if args.retrieval == "hybrid" and not tag_index.has_embeddings:
            raise ValueError("--retrieval hybrid requires a complete embedding index")
        if args.retrieval != "lexical" and tag_index.has_embeddings:
            embedding_model = args.embedding_model or tag_index.metadata.get("embedding_model")
            embedding_base_url = args.embedding_base_url or tag_index.metadata.get("embedding_base_url")
            if not embedding_model or not embedding_base_url:
                if args.retrieval == "hybrid":
                    raise ValueError("embedding index lacks model or base-URL metadata")
                log(args, "embedding metadata is incomplete; using lexical retrieval")
            else:
                candidate_client = EmbeddingClient(
                    str(embedding_base_url), str(embedding_model),
                    os.getenv(args.embedding_api_key_env, ""), args.timeout,
                    int(tag_index.metadata.get("embedding_dimensions", 0)) or None,
                )
                try:
                    probe = candidate_client.embed([embedding_query_prefix + "superhero landing"])[0]
                    expected = int(tag_index.metadata.get("embedding_dimensions", 0))
                    if len(probe) != expected:
                        raise RuntimeError(f"query embedding has {len(probe)} dimensions; index requires {expected}")
                    embedding_client = candidate_client
                    retrieval_mode = "hybrid"
                except RuntimeError as exc:
                    if args.retrieval == "hybrid":
                        raise
                    log(args, f"embedding query probe failed; using lexical retrieval: {exc}")
        output.parent.mkdir(parents=True, exist_ok=True)
        fixed_output.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text("", encoding="utf-8")
        session = Session(args, trace_path)
        log(args, f"loaded skeleton {skeleton.namespace} with {len(skeleton.categories)} required categories")
        plans = generate_valid_plan(session, skeleton, policy)
        log(args, f"accepted plan with {len(plans)} categories ({sum(not plan.required for plan in plans)} added)")
        content = generate_categories(
            session, skeleton, plans, policy, vocabulary, tag_index,
            embedding_client, embedding_query_prefix,
        )
        output.write_text(render_wildcard(skeleton, plans, content), encoding="utf-8")
        fixed_output.write_text(render_wildcard(skeleton, plans, content), encoding="utf-8")

        for repair_pass in range(args.max_repair_passes + 1):
            leaves, findings = deterministic_findings(
                fixed_output, rules, tags_rules, vocabulary,
                args.canonical_tag_candidate_count, args.canonical_tag_style,
            )
            for issue in session.generation_issues:
                if issue["rule"] == "trimmed_excess_generated_concepts":
                    removed = " | ".join(
                        str(concept.get("summary", concept)) for concept in issue["removed_concepts"]
                    )
                    findings.append(linter.Finding(
                        "warning", str(issue["rule"]),
                        f"Concept generation for category '{issue['category']}' returned more than its requested "
                        f"{issue['expected_count']} concepts. The generator retained the first "
                        f"{issue['expected_count']} and omitted: {removed}. Manual review: no action is required unless "
                        "the omitted concept should replace one of the generated leaves.",
                        str(fixed_output), category=str(issue["category"]), evidence=removed,
                    ))
                elif issue["rule"] == "trimmed_excess_generated_leaves":
                    removed = " | ".join(issue["removed_leaves"])
                    findings.append(linter.Finding(
                        "warning", str(issue["rule"]),
                        f"Generated category '{issue['category']}' returned more than its requested "
                        f"{issue['expected_count']} leaves. The generator retained the first "
                        f"{issue['expected_count']} aligned leaf/provenance pairs and removed the following excess "
                        f"output: {removed}. Manual review: restore or replace an omitted concept only if it is more "
                        "valuable than one of the retained leaves.",
                        str(fixed_output), category=str(issue["category"]), evidence=removed,
                    ))
                else:
                    missing = ", ".join(issue["missing_dependencies"])
                    findings.append(linter.Finding(
                        "warning", str(issue["rule"]),
                        f"Generated category '{issue['category']}' did not reference these declared dependencies after "
                        f"corrective retries: {missing}. This may leave category pools unreachable from public routes. "
                        "Manual review: connect required pools through an appropriate reachable composite/router. Only "
                        "a planner-added pool confirmed to be unnecessary should be manually removed.",
                        str(fixed_output), category=str(issue["category"]), evidence=missing,
                    ))
            if not args.skip_semantic_review:
                session.check_token_budget()
                review_leaves = [leaf for leaf in leaves if linter.has_literal_content(leaf)]
                findings.extend(linter.llm_review(review_leaves, session.linter_args, policy, trace_path, vocabulary))
            final_findings = findings
            actionable = [finding for finding in findings if finding.leaf_id]
            log(args, f"validation pass {repair_pass + 1}: {len(findings)} findings, {len(actionable)} leaf findings")
            if not actionable or repair_pass >= args.max_repair_passes:
                break
            session.check_token_budget()
            proposed, _ = linter.llm_suggest_fixes(
                leaves, findings, session.linter_args, trace_path, vocabulary, tags_rules
            )
            accepted, _ = linter.validate_suggestions(
                leaves, proposed, findings, rules, tags_rules, vocabulary,
                args.canonical_tag_candidate_count, args.canonical_tag_style,
            )
            if accepted and not args.skip_fix_verification:
                accepted, _ = linter.llm_verify_fixes(
                    leaves, accepted, findings, session.linter_args, trace_path
                )
            if not apply_leaf_rewrites(content, leaves, accepted):
                break
            fixed_output.write_text(render_wildcard(skeleton, plans, content), encoding="utf-8")

        final_findings.sort(key=lambda finding: (finding.file, finding.line, finding.severity, finding.rule))
        report_path.write_text(
            linter.render(
                final_findings, leaves, "markdown", fix_attempted=True, fixed_leaf_ids=set()
            ),
            encoding="utf-8",
        )
        manifest.update({
            "status": "complete_with_findings" if final_findings else "complete",
            "namespace": skeleton.namespace,
            "plan": [asdict(plan) for plan in plans],
            "generation_calls": session.events,
            "generation_call_count": session.calls,
            "reported_tokens": session.reported_tokens,
            "tag_provenance": session.provenance,
            "interactive_tag_overrides": session.interactive_overrides,
            "interactive_override_file": str(session.interactive_override_path),
            "generation_issues": session.generation_issues,
            "retrieval": {
                "mode": retrieval_mode,
                "index": str(index_path),
                "content_profile": args.content_profile,
                "indexed_tag_count": tag_index.metadata.get("tag_count"),
                "excluded_tag_count": tag_index.metadata.get("excluded_tag_count"),
                "source_csv_sha256": tag_index.metadata.get("source_csv_sha256"),
                "embedding_model": tag_index.metadata.get("embedding_model") if retrieval_mode == "hybrid" else None,
            },
            "category_count": len(plans),
            "leaf_count": sum(len(values) for values in content.values()),
            "unresolved": [asdict(finding) for finding in final_findings],
            "report": str(report_path),
            "finished": time.time(),
        })
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"generated: {output}")
        print(f"fixed: {fixed_output} ({manifest['leaf_count']} leaves, {len(final_findings)} unresolved findings)")
        print(f"report: {report_path}")
        print(f"manifest: {manifest_path}")
        if retrieval_mode == "lexical":
            print("retrieval: lexical only (build embeddings for better semantic candidate discovery)")
        return 1 if any(finding.severity == "error" for finding in final_findings) else 0
    except (OSError, ValueError, RuntimeError) as exc:
        # Once assembly has begun, retain the best draft as requested.
        if skeleton and plans and content:
            output.parent.mkdir(parents=True, exist_ok=True)
            rendered = render_wildcard(skeleton, plans, content)
            if not output.exists():
                output.write_text(rendered, encoding="utf-8")
            fixed_output.parent.mkdir(parents=True, exist_ok=True)
            fixed_output.write_text(rendered, encoding="utf-8")
        manifest.update({
            "status": "incomplete", "error": f"{type(exc).__name__}: {exc}",
            "plan": [asdict(plan) for plan in plans],
            "generation_calls": session.events if session else [],
            "interactive_tag_overrides": session.interactive_overrides if session else {},
            "interactive_override_file": str(session.interactive_override_path) if session else None,
            "generation_issues": session.generation_issues if session else [],
            "finished": time.time(),
        })
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            pass
        print(f"wildcard_generator: {exc}", file=sys.stderr)
        return 2
    finally:
        if tag_index is not None:
            tag_index.close()


if __name__ == "__main__":
    raise SystemExit(main())
