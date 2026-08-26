#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML>=6.0.2"]
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


GENERATOR_RE = re.compile(r"^\s*#\s*GENERATOR\s*:\s*(.*?)\s*$", re.I)
CATEGORY_RE = re.compile(r"^  ([A-Za-z0-9_-]+)\s*:\s*(?:\[\s*\])?\s*(?:#.*)?$")
ROOT_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*$")
REFERENCE_RE = re.compile(r"__([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)__")
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
    parser.add_argument("--prompt", type=Path, help="Policy Markdown (defaults to ../prompt.md)")
    parser.add_argument("--rules", type=Path, help="General linter rules (defaults beside script)")
    parser.add_argument("--tags-rules", type=Path, help="Tags-mode rules (defaults beside script)")
    parser.add_argument("--model", help="Model name; defaults to OPENAI_MODEL")
    parser.add_argument("--base-url", help="API base URL; defaults to OPENAI_BASE_URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--batch-categories", type=int, default=3)
    parser.add_argument("--max-generation-calls", type=int, default=20)
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
    if lowered == "random" or lowered.startswith("random_") or lowered.endswith("_router"):
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
        match = re.search(r"\b(\d{1,4})\s+(?:leaves|items|options|entries|prompts)\b", directive, re.I)
        if match:
            return int(match.group(1))
    return DEFAULT_COUNTS[kind]


def has_explicit_count(directives: Iterable[str]) -> bool:
    return any(re.search(r"\b\d{1,4}\s+(?:leaves|items|options|entries|prompts)\b", value, re.I) for value in directives)


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
    validate_plan_depth(plans, args.max_category_depth)
    return plans


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
        self.linter_args.llm_usage_callback = self.record_usage

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
        "A dependency means that the category may reference that category. Routers select complete routes. Combos and scenes "
        "must resolve to coherent single images. Keep the graph acyclic and no deeper than the supplied limit. This generator "
        "supports tags mode only. Do not generate leaves yet. Follow all GENERATOR instructions.\n\nPOLICY:\n" + policy
    )


def generation_instruction(policy: str, vocabulary: linter.DanbooruVocabulary) -> str:
    sample_note = (
        f"A local canonical vocabulary with {len(vocabulary)} tags is available to deterministic validation. "
        "Use established Danbooru underscore tags when confidently known and visually equivalent, but never invent an underscore "
        "tag. Use a compact literal phrase with spaces whenever no canonical tag exists or canonicalization would lose content."
    )
    return (
        "Generate wildcard leaves for every supplied category. Return JSON only as an array with exactly one object per input ID, "
        "containing id and leaves (an array of strings). Return exactly requested_count leaves, except a router should return the "
        "minimum useful set of reference-only leaves. A wildcard reference has exact form __namespace/category__. Reference only "
        "allowed_dependencies. Component leaves must contain literal visible content and no references unless the category purpose "
        "explicitly requires composition. Combo/scene leaves may combine allowed references with compact literal tag phrases. Router "
        "leaves contain references only. Spotlight leaves are complete, high-specificity, single-image prompts. All literal output is "
        "tags mode: flat comma-separated short visual phrases, 1-3 meaningful weights, no prose, quality filler, negative prompt, or "
        "sequential/multi-panel content. Avoid duplicate or near-duplicate leaves. Follow the global and local GENERATOR instructions. "
        + sample_note + "\n\nPOLICY:\n" + policy
    )


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


def generate_categories(
    session: Session, skeleton: Skeleton, plans: list[CategoryPlan], policy: str,
    vocabulary: linter.DanbooruVocabulary,
) -> dict[str, list[str]]:
    generated: dict[str, list[str]] = {}
    plan_by_name = {plan.name: plan for plan in plans}
    for batch in category_batches(plans, session.args.batch_categories):
        items: list[dict[str, Any]] = []
        for plan in batch:
            skeleton_category = next((item for item in skeleton.categories if item.name == plan.name), None)
            dependency_examples = {name: generated.get(name, [])[:3] for name in plan.dependencies}
            items.append({
                "id": plan.name, "namespace": skeleton.namespace, "kind": plan.kind,
                "purpose": plan.purpose, "requested_count": plan.count,
                "allowed_dependencies": plan.dependencies,
                "dependency_examples": dependency_examples,
                "generator_instructions": skeleton_category.directives if skeleton_category else [],
                "global_generator_instructions": skeleton.global_directives,
            })
        results = session.request("category generation", generation_instruction(policy, vocabulary), items)
        by_id = {str(item.get("id", "")): item for item in results}
        for plan in batch:
            raw = by_id.get(plan.name, {}).get("leaves", [])
            leaves = [" ".join(value.split()) for value in raw if isinstance(value, str) and value.strip()]
            leaves = list(dict.fromkeys(leaves))
            if plan.kind != "router" and len(leaves) != plan.count:
                raise RuntimeError(f"category {plan.name} returned {len(leaves)} unique leaves; expected {plan.count}")
            if plan.kind == "router" and not leaves:
                leaves = [f"__{skeleton.namespace}/{name}__" for name in plan.dependencies]
            allowed = {(skeleton.namespace, dep) for dep in plan.dependencies}
            for leaf in leaves:
                refs = set(REFERENCE_RE.findall(leaf))
                if not refs <= allowed:
                    raise RuntimeError(f"category {plan.name} returned an undeclared reference in: {leaf}")
                if plan.kind == "router" and linter.has_literal_content(SimpleNamespace(text=leaf)):
                    raise RuntimeError(f"router {plan.name} returned literal content: {leaf}")
            generated[plan.name] = leaves
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
    final_findings: list[linter.Finding] = []
    leaves: list[linter.Leaf] = []
    try:
        if args.max_category_depth < 1 or args.max_added_categories < 0 or args.max_generation_calls < 1:
            raise ValueError("generation limits must be positive (added-category limit may be zero)")
        skeleton = parse_skeleton(args.skeleton.expanduser().resolve())
        policy = prompt_path.read_text(encoding="utf-8")
        rules = linter.load_rules(rules_path)
        tags_rules = linter.load_rules(tags_rules_path)
        vocabulary = linter.load_danbooru_tags(args.danbooru_tags.expanduser().resolve())
        output.parent.mkdir(parents=True, exist_ok=True)
        fixed_output.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text("", encoding="utf-8")
        session = Session(args, trace_path)
        log(args, f"loaded skeleton {skeleton.namespace} with {len(skeleton.categories)} required categories")
        planned = session.request("generation plan", planning_instruction(policy), planner_items(skeleton, args))
        if len(planned) != 1:
            raise RuntimeError("planner did not return exactly one plan")
        plans = parse_plan(planned[0], skeleton, args)
        log(args, f"accepted plan with {len(plans)} categories ({sum(not plan.required for plan in plans)} added)")
        content = generate_categories(session, skeleton, plans, policy, vocabulary)
        output.write_text(render_wildcard(skeleton, plans, content), encoding="utf-8")
        fixed_output.write_text(render_wildcard(skeleton, plans, content), encoding="utf-8")

        for repair_pass in range(args.max_repair_passes + 1):
            leaves, findings = deterministic_findings(
                fixed_output, rules, tags_rules, vocabulary,
                args.canonical_tag_candidate_count, args.canonical_tag_style,
            )
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
            "finished": time.time(),
        })
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            pass
        print(f"wildcard_generator: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
