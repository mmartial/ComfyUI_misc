#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML>=6.0.2"]
# ///

"""Lint GKR wildcard YAML files and optionally request semantic LLM review."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


REFERENCE_RE = re.compile(r"__([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)__")
CATEGORY_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
LEAF_RE = re.compile(r"^    -(?:\s|$)")
SEQUENCE_RE = re.compile(
    r"\b(panel|panels|page|pages|storyboard|diptych|triptych|contact sheet|"
    r"sequence|spread|frame-by-frame|six-frame|four-frame)\b",
    re.IGNORECASE,
)
CAMERA_CATEGORY_RE = re.compile(r"(?:^|_)(camera|composition|framing)$", re.I)
MODE_RE = re.compile(r"\bMODE\s*:\s*(narrative|tags)\b", re.I)
WEIGHT_RE = re.compile(r"\(([^()]+):(-?(?:\d+(?:\.\d+)?|\.\d+))\)")
TAG_SEQUENCE_RE = re.compile(
    r"\b(?:multi[- ]?panel|split comic panels?|sequential panels?|\w+-panel (?:page|sequence)|"
    r"comic page|manga page|(?:two|three|four|five|six|seven|eight|nine|\d+)[- ]page|"
    r"double[- ]page(?: comics?)? spread|storyboard|diptych|triptych|contact sheet|frame-by-frame|"
    r"before[- /]and[- /]after|walking sequence|panel sequence|in sequence|adjacent poses?|simultaneous poses?|"
    r"both pages|spanning (?:(?:both|two|three|four|\d+) pages|pages)|"
    r"across (?:(?:both|two|three|four|\d+) pages|pages)|central gutter|gutter clear|page pair)\b",
    re.I,
)
TAG_MULTI_VIEW_RE = re.compile(
    r"\b(?:character|creature|costume|prop|environment)?[- ]?(?:design |variant )?sheet\b|"
    r"\bturnaround\b|\bmultiple views?\b|\bfront(?:,| and) (?:three-quarter, )?side(?:,| and) back views?\b|"
    r"\bfront and back views?\b|\b(?:all |\w+ )?(?:five|four|three|\d+) outfits?\b|"
    r"\b(?:resting|alert|walking) poses?.*\b(?:resting|alert|walking) poses?\b",
    re.I,
)
TAG_DANGLING_RELATION_RE = re.compile(
    r"^(?:clutched|held|seated|placed|positioned|located|braced|tucked|hung|hidden|trapped|"
    r"surrounded|covered|filled|attached|fastened|pinned)\b\s+(?:in|inside|on|onto|at|by|"
    r"beneath|behind|beside|between|against|around|from|to|with|under|over|among)\b",
    re.I,
)
ALTERNATIVE_RE = re.compile(r"\b(?:or|either)\b", re.I)
STRUCTURAL_FIX_RULES = {
    "camera_format_conflict", "unrestricted_camera_composite", "missing_reference", "reference_cycle",
}


@dataclass(frozen=True)
class Leaf:
    uid: str
    file: str
    namespace: str
    category: str
    index: int
    line: int
    text: str
    references: tuple[tuple[str, str], ...]
    mode: str = "narrative"


@dataclass
class Finding:
    severity: str
    rule: str
    message: str
    file: str
    line: int = 0
    category: str = ""
    leaf_id: str = ""
    evidence: str = ""
    suggestion: str = ""
    source: str = "deterministic"


@dataclass
class PromptAudit:
    image: str
    mode: str
    status: str
    issues: list[str]
    post_prompt: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lint wildcard YAML structure, language, and composite routes."
    )
    parser.add_argument("paths", nargs="*", help="YAML file(s) or directories")
    parser.add_argument("--rules", type=Path, help="Rules YAML (defaults beside script)")
    parser.add_argument("--tags-rules", type=Path, help="Tags-mode rules YAML (defaults beside script)")
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument("--output", type=Path, help="Write report to this path")
    parser.add_argument("--fail-on", choices=("error", "warning", "never"), default="error")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="ANSI color mode for text reports")
    parser.add_argument("-v", "--verbose", action="store_true", help="Report inventory, rule, route, and LLM batch progress to stderr")
    parser.add_argument("--llm", action="store_true", help="Run OpenAI-compatible semantic review")
    parser.add_argument("--llm-scope", choices=("candidates", "content", "all"), default="candidates", help="LLM selection: flagged candidates, all literal-content leaves, or every leaf")
    parser.add_argument("--suggest-fixes", action="store_true", help="Run a second LLM pass proposing rewrites for found leaf issues; requires --llm")
    parser.add_argument("--fixed-output", type=Path, help="Write suggested rewrites to a new YAML file; requires --llm and --suggest-fixes")
    parser.add_argument("--fix-severity", choices=("error", "warning", "both"), default="error", help="Fix errors only (default), warnings only, or both")
    parser.add_argument("--fix-rules", help="Comma-separated rule allowlist for fixes; overrides --fix-severity")
    parser.add_argument("--fix-manifest", type=Path, help="Write accepted and rejected fix details as JSON")
    parser.add_argument("--skip-fix-verification", action="store_true", help="Skip the final LLM semantic-preservation check (not recommended)")
    parser.add_argument("--model", help="Model name; defaults to OPENAI_MODEL")
    parser.add_argument("--base-url", help="API base URL; defaults to OPENAI_BASE_URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing the API key")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--verification-batch-size", type=int, default=15, help="Items per semantic fix-verification request")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--prompt", type=Path, help="Review policy Markdown; defaults to ../prompt.md")
    parser.add_argument("--llm-log", type=Path, help="Write sanitized LLM requests and responses as JSON Lines")
    parser.add_argument("--validate-post-prompts", type=Path, metavar="DETAILS_MD", help="Audit final post-LLM prompts recorded by theme_organizer.py")
    parser.add_argument("--annotated-details", type=Path, help="Write a copy of details.md with audit results appended; requires --validate-post-prompts")
    return parser.parse_args()


def discover_paths(raw_paths: Iterable[str]) -> list[Path]:
    found: set[Path] = set()
    for raw in raw_paths:
        path = Path(raw).resolve()
        if path.is_dir():
            found.update(path.glob("*.yaml"))
            found.update(path.glob("*.yml"))
        elif path.suffix.lower() in {".yaml", ".yml"}:
            found.add(path)
        else:
            raise ValueError(f"not a YAML file or directory: {raw}")
    if not found:
        raise ValueError("no YAML files found")
    return sorted(found)


def source_lines(path: Path) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    category = ""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = CATEGORY_RE.match(line)
        if match:
            category = match.group(1)
            positions.setdefault(category, [])
        elif category and LEAF_RE.match(line):
            positions[category].append(number)
    return positions


def load_inventory(paths: list[Path]) -> tuple[list[Leaf], dict[tuple[str, str], list[Leaf]], list[Finding]]:
    leaves: list[Leaf] = []
    categories: dict[tuple[str, str], list[Leaf]] = {}
    findings: list[Finding] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        mode_match = MODE_RE.search(source)
        mode = mode_match.group(1).lower() if mode_match else "narrative"
        try:
            data = yaml.safe_load(source)
        except (OSError, yaml.YAMLError) as exc:
            findings.append(Finding("error", "yaml_syntax", str(exc), str(path)))
            continue
        if not isinstance(data, dict) or len(data) != 1:
            findings.append(Finding("error", "root_shape", "expected exactly one namespace mapping", str(path)))
            continue
        namespace, mapping = next(iter(data.items()))
        if not isinstance(namespace, str) or not isinstance(mapping, dict):
            findings.append(Finding("error", "root_shape", "namespace must contain a category mapping", str(path)))
            continue
        positions = source_lines(path)
        for category, values in mapping.items():
            if not isinstance(values, list):
                findings.append(Finding("error", "category_shape", "category value must be a list", str(path), category=str(category)))
                continue
            if not values:
                findings.append(Finding("warning", "empty_category", "category has no leaves", str(path), category=str(category)))
            lines = positions.get(str(category), [])
            for index, value in enumerate(values):
                line = lines[index] if index < len(lines) else 0
                if not isinstance(value, str):
                    findings.append(Finding("error", "leaf_type", "leaf must be a string", str(path), line, str(category)))
                    continue
                refs = tuple(REFERENCE_RE.findall(value))
                digest = hashlib.sha1(f"{path}:{category}:{index}:{value}".encode()).hexdigest()[:12]
                leaf = Leaf(digest, str(path), namespace, str(category), index, line, value, refs, mode)
                leaves.append(leaf)
                categories.setdefault((namespace, str(category)), []).append(leaf)
    return leaves, categories, findings


def load_rules(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("rules file must contain a mapping")
    return data


def literal_text(text: str) -> str:
    return REFERENCE_RE.sub(" ", text)


def has_literal_content(leaf: Leaf) -> bool:
    """Return true unless a leaf is only one or more wildcard references."""
    remainder = literal_text(leaf.text)
    remainder = re.sub(r"[\s,;|:+\-–—()\[\]{}]+", "", remainder)
    return bool(remainder)


def split_top_level_commas(text: str) -> list[str]:
    """Split a leaf on commas except those nested inside brackets or parentheses."""
    parts: list[str] = []
    start = 0
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    for index, character in enumerate(text):
        if character in pairs:
            depth += 1
        elif character in closers and depth:
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def pattern_findings(leaves: list[Leaf], rules: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    sequence_exceptions = set(rules.get("sequence_exempt_rules", []))
    for leaf in leaves:
        literal = literal_text(leaf.text)
        for name, rule in rules.get("patterns", {}).items():
            severity = rule.get("severity", "warning")
            if rule.get("authored_only") and not authored_category(leaf.category, rules):
                continue
            if leaf.mode == "narrative" and name in sequence_exceptions and SEQUENCE_RE.search(literal):
                continue
            for expression in rule.get("regex", []):
                match = re.search(expression, literal, re.IGNORECASE)
                if match:
                    message = rule.get("message", "pattern requires review")
                    guidance = rule.get("suggestion", "")
                    if guidance:
                        message = f"{message} Suggested approach: {guidance}"
                    findings.append(Finding(
                        severity, name, message, leaf.file,
                        leaf.line, leaf.category, leaf.uid, match.group(0)
                    ))
                    break
    return findings


def tags_mode_findings(leaves: list[Leaf], rules: dict[str, Any]) -> list[Finding]:
    """Apply deterministic checks that are meaningful only for tags-mode files."""
    findings: list[Finding] = []
    max_words = int(rules.get("max_phrase_words", 4))
    max_weights = int(rules.get("max_weighted_terms", 3))
    sentence_re = re.compile(rules.get("sentence_connector_regex", r"\b(?:is|are|was|were|while|whereas)\b"), re.I)
    relationship_re = re.compile(rules.get(
        "relationship_regex",
        r"\b(?:between|beneath|above|below|behind|before|toward|against|holding|gripping|protecting|fighting|facing|chasing|blocking|catching|carrying|surrounding|arcing|projecting)\b",
    ), re.I)
    relationship_max_words = int(rules.get("relationship_max_phrase_words", max_words + 6))
    for leaf in leaves:
        if leaf.mode != "tags" or not has_literal_content(leaf):
            continue
        literal = " ".join(literal_text(leaf.text).split())
        sequence_text = re.sub(
            r"\b(?:panel[- ]border(?:ed)?(?: framing)?|no sequential panels?)\b", "", literal, flags=re.I
        )
        exemptions = rules.get("format_exemptions", {}).get(leaf.namespace, {})
        sequence_exempt = leaf.category in set(exemptions.get("sequential_categories", []))
        multi_view_exempt = leaf.category in set(exemptions.get("multi_view_categories", []))
        match = TAG_SEQUENCE_RE.search(sequence_text)
        if match and not sequence_exempt:
            findings.append(Finding(
                "error", "tags_sequential_format",
                "Tags mode forbids sequential, multi-panel, and multi-page content; keep only a single-image rendering signature.",
                leaf.file, leaf.line, leaf.category, leaf.uid, match.group(0),
            ))
        multi_view = TAG_MULTI_VIEW_RE.search(sequence_text)
        if multi_view and not multi_view_exempt:
            findings.append(Finding(
                "error", "tags_multi_view_format",
                "Tags mode forbids design sheets, turnarounds, and multiple-view or multiple-variant layouts.",
                leaf.file, leaf.line, leaf.category, leaf.uid, multi_view.group(0),
            ))
        weights = WEIGHT_RE.findall(literal)
        if len(weights) > max_weights:
            findings.append(Finding(
                "warning", "tags_excessive_weights",
                f"Tags mode allows emphasis on at most {max_weights} important items per leaf.",
                leaf.file, leaf.line, leaf.category, leaf.uid, str(len(weights)),
            ))
        connector = sentence_re.search(WEIGHT_RE.sub(lambda m: m.group(1), literal))
        if connector:
            findings.append(Finding(
                "warning", "tags_sentence_connector",
                "Tags mode should use flat tag phrases; sentence connectors require a scene-defining relationship that cannot survive splitting.",
                leaf.file, leaf.line, leaf.category, leaf.uid, connector.group(0),
            ))
        for phrase in (part.strip(" .;:-") for part in split_top_level_commas(literal)):
            if not phrase:
                continue
            unweighted = WEIGHT_RE.sub(lambda m: m.group(1), phrase)
            dangling = TAG_DANGLING_RELATION_RE.search(unweighted)
            if dangling:
                findings.append(Finding(
                    "warning", "tags_dangling_relation",
                    "Tags mode should not detach a relational action into a prose fragment; attach it to its visible subject or object.",
                    leaf.file, leaf.line, leaf.category, leaf.uid, phrase,
                ))
                break
            words = re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", unweighted)
            limit = relationship_max_words if relationship_re.search(unweighted) else max_words
            if len(words) > limit:
                findings.append(Finding(
                    "warning", "tags_long_phrase",
                    f"Tag phrase has {len(words)} words; tags mode limits this phrase to {limit} words.",
                    leaf.file, leaf.line, leaf.category, leaf.uid, phrase,
                ))
                break
    return findings


def authored_category(category: str, rules: dict[str, Any]) -> bool:
    return any(re.search(pattern, category) for pattern in rules.get("authored_category_regex", []))


def graph_findings(leaves: list[Leaf], categories: dict[tuple[str, str], list[Leaf]]) -> list[Finding]:
    findings: list[Finding] = []
    graph: dict[tuple[str, str], set[tuple[str, str]]] = {key: set() for key in categories}
    for leaf in leaves:
        source = (leaf.namespace, leaf.category)
        for ref in leaf.references:
            graph.setdefault(source, set()).add(ref)
            if ref not in categories:
                findings.append(Finding("error", "missing_reference", f"reference {ref[0]}/{ref[1]} does not exist", leaf.file, leaf.line, leaf.category, leaf.uid, f"__{ref[0]}/{ref[1]}__"))

    visiting: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = set()
    reported: set[tuple[tuple[str, str], ...]] = set()

    def walk(node: tuple[str, str], stack: list[tuple[str, str]]) -> None:
        if node in visiting:
            start = stack.index(node)
            cycle = tuple(stack[start:] + [node])
            if cycle not in reported:
                reported.add(cycle)
                findings.append(Finding("error", "reference_cycle", " -> ".join(f"{a}/{b}" for a, b in cycle), categories[node][0].file))
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for target in graph.get(node, set()):
            if target in categories:
                walk(target, stack)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        walk(node, [])

    for leaf in leaves:
        camera_refs = [ref for ref in leaf.references if CAMERA_CATEGORY_RE.search(ref[1])]
        if not camera_refs:
            continue
        other_refs = [ref for ref in leaf.references if ref not in camera_refs]
        if SEQUENCE_RE.search(literal_text(leaf.text)) or any(category_has_sequence(ref, categories, set()) for ref in other_refs):
            findings.append(Finding("error", "camera_format_conflict", "unrestricted camera pool can conflict with sequential or multi-panel content", leaf.file, leaf.line, leaf.category, leaf.uid, ", ".join(f"{a}/{b}" for a, b in camera_refs)))
        elif len(other_refs) or len(literal_text(leaf.text).split()) > 10:
            findings.append(Finding("warning", "unrestricted_camera_composite", "completed content references an unrestricted camera pool; verify every expansion preserves subject count and visibility", leaf.file, leaf.line, leaf.category, leaf.uid, ", ".join(f"{a}/{b}" for a, b in camera_refs)))
    return findings


def route_motif_findings(
    categories: dict[tuple[str, str], list[Leaf]], rules: dict[str, Any]
) -> list[Finding]:
    """Estimate exact motif presence probability through uniformly selected wildcard routes."""
    findings: list[Finding] = []
    for name, policy in rules.get("route_motifs", {}).items():
        expressions = [re.compile(value, re.I) for value in policy.get("regex", [])]
        if not expressions:
            continue
        maximum = float(policy.get("max_probability", 0.15))
        roots = policy.get("roots", ["random"])
        memo: dict[tuple[str, str], float] = {}

        def probability(key: tuple[str, str], visiting: set[tuple[str, str]]) -> float:
            if key in memo:
                return memo[key]
            if key in visiting or key not in categories:
                return 0.0
            visiting = visiting | {key}
            chances: list[float] = []
            for leaf in categories[key]:
                if any(expression.search(literal_text(leaf.text)) for expression in expressions):
                    chances.append(1.0)
                    continue
                miss = 1.0
                for reference in leaf.references:
                    miss *= 1.0 - probability(reference, visiting)
                chances.append(1.0 - miss)
            result = sum(chances) / len(chances) if chances else 0.0
            memo[key] = result
            return result

        namespaces = sorted({namespace for namespace, _ in categories})
        for namespace in namespaces:
            for root in roots:
                key = (namespace, str(root))
                if key not in categories:
                    continue
                chance = probability(key, set())
                if chance > maximum:
                    leaf = categories[key][0]
                    findings.append(Finding(
                        policy.get("severity", "warning"), "route_motif_overrepresentation",
                        f"Motif '{name}' appears in approximately {chance:.1%} of expansions from {namespace}/{root}; configured maximum is {maximum:.1%}.",
                        leaf.file, category=str(root), evidence=f"{name}: {chance:.1%}",
                    ))
    return findings


def namespace_policy_findings(
    leaves: list[Leaf], categories: dict[tuple[str, str], list[Leaf]], rules: dict[str, Any]
) -> list[Finding]:
    """Apply namespace-specific route, content, and expanded-size contracts."""
    findings: list[Finding] = []
    leaves_by_namespace: dict[str, list[Leaf]] = {}
    for leaf in leaves:
        leaves_by_namespace.setdefault(leaf.namespace, []).append(leaf)

    for namespace, policy in rules.get("namespace_policies", {}).items():
        namespace_leaves = leaves_by_namespace.get(namespace, [])
        if not namespace_leaves:
            continue

        def reachable(root: tuple[str, str]) -> set[tuple[str, str]]:
            found: set[tuple[str, str]] = set()
            pending = [root]
            while pending:
                key = pending.pop()
                if key in found or key not in categories:
                    continue
                found.add(key)
                pending.extend(ref for leaf in categories[key] for ref in leaf.references)
            return found

        for root, excluded in policy.get("route_exclusions", {}).items():
            root_key = (namespace, str(root))
            reached = reachable(root_key)
            for category in excluded:
                if (namespace, str(category)) in reached:
                    anchor = categories[root_key][0]
                    findings.append(Finding(
                        "error", "route_contract_violation",
                        f"{namespace}/{root} must not reach the '{category}' output family.",
                        anchor.file, anchor.line, str(root), anchor.uid, str(category),
                    ))

        for name, content_policy in policy.get("forbidden_content", {}).items():
            expression = re.compile(str(content_policy["regex"]), re.I)
            for leaf in namespace_leaves:
                match = expression.search(literal_text(leaf.text))
                if match:
                    findings.append(Finding(
                        content_policy.get("severity", "error"), f"namespace_{name}",
                        content_policy.get("message", f"Content is forbidden in {namespace}."),
                        leaf.file, leaf.line, leaf.category, leaf.uid, match.group(0),
                    ))

        for name, content_policy in policy.get("restricted_content", {}).items():
            expression = re.compile(str(content_policy["regex"]), re.I)
            allowed = {str(value) for value in content_policy.get("allowed_categories", [])}
            for leaf in namespace_leaves:
                match = expression.search(literal_text(leaf.text))
                if match and leaf.category not in allowed:
                    findings.append(Finding(
                        content_policy.get("severity", "error"), f"namespace_{name}",
                        content_policy.get("message", f"Content is restricted in {namespace}."),
                        leaf.file, leaf.line, leaf.category, leaf.uid, match.group(0),
                    ))

        memo: dict[tuple[str, str], dict[tuple[int, int], float]] = {}

        def distribution(key: tuple[str, str], visiting: set[tuple[str, str]]) -> dict[tuple[int, int], float]:
            if key in memo:
                return memo[key]
            if key in visiting or key not in categories:
                return {(0, 0): 1.0}
            leaf_distributions: list[dict[tuple[int, int], float]] = []
            for leaf in categories[key]:
                literal = literal_text(leaf.text).strip(" ,;:-")
                items = len([part for part in split_top_level_commas(literal) if part.strip()]) if literal else 0
                words = len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", literal))
                combined = {(items, words): 1.0}
                for reference in leaf.references:
                    next_dist = distribution(reference, visiting | {key})
                    expanded: dict[tuple[int, int], float] = {}
                    for (left_i, left_w), left_p in combined.items():
                        for (right_i, right_w), right_p in next_dist.items():
                            result_key = (left_i + right_i, left_w + right_w)
                            expanded[result_key] = expanded.get(result_key, 0.0) + left_p * right_p
                    combined = expanded
                leaf_distributions.append(combined)
            result: dict[tuple[int, int], float] = {}
            divisor = len(leaf_distributions) or 1
            for leaf_dist in leaf_distributions:
                for size, chance in leaf_dist.items():
                    result[size] = result.get(size, 0.0) + chance / divisor
            memo[key] = result
            return result

        def percentile(dist: dict[tuple[int, int], float], dimension: int, threshold: float = 0.9) -> int:
            marginal: dict[int, float] = {}
            for size, chance in dist.items():
                marginal[size[dimension]] = marginal.get(size[dimension], 0.0) + chance
            cumulative = 0.0
            for value, chance in sorted(marginal.items()):
                cumulative += chance
                if cumulative >= threshold:
                    return value
            return max(marginal, default=0)

        for root, budget in policy.get("prompt_budgets", {}).items():
            key = (namespace, str(root))
            if key not in categories:
                continue
            dist = distribution(key, set())
            max_items = int(budget["max_items"])
            max_words = int(budget["max_words"])
            over = sum(chance for (items, words), chance in dist.items() if items > max_items or words > max_words)
            p90_items, p90_words = percentile(dist, 0), percentile(dist, 1)
            actual_max_items = max((size[0] for size in dist), default=0)
            actual_max_words = max((size[1] for size in dist), default=0)
            if over > 0:
                over_label = "<0.1%" if over < 0.001 else f"{over:.1%}"
                anchor = categories[key][0]
                findings.append(Finding(
                    budget.get("severity", "warning"), "route_prompt_budget",
                    f"Expanded {namespace}/{root} prompts exceed {max_items} items or {max_words} words in approximately {over_label} of routes.",
                    anchor.file, anchor.line, str(root), anchor.uid,
                    f"p90={p90_items} items/{p90_words} words; max={actual_max_items}/{actual_max_words}; target={budget.get('target_items', max_items)} items",
                ))
    return findings


def category_has_sequence(key: tuple[str, str], categories: dict[tuple[str, str], list[Leaf]], seen: set[tuple[str, str]]) -> bool:
    if key in seen or key not in categories:
        return False
    seen.add(key)
    for leaf in categories[key]:
        if SEQUENCE_RE.search(literal_text(leaf.text)):
            return True
        if any(category_has_sequence(ref, categories, seen) for ref in leaf.references):
            return True
    return False


def verbose(args: argparse.Namespace, message: str) -> None:
    if args.verbose:
        print(f"[wildcard-linter] {message}", file=sys.stderr)


def trace_event(path: Path | None, event: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def llm_review(leaves: list[Leaf], args: argparse.Namespace, policy: str, trace_path: Path | None = None) -> list[Finding]:
    model = args.model or os.getenv("OPENAI_MODEL")
    base_url = (args.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    api_key = os.getenv(args.api_key_env)
    if not model:
        raise ValueError("--model or OPENAI_MODEL is required with --llm")
    if not api_key:
        raise ValueError(f"{args.api_key_env} is required with --llm")
    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    trace_event(trace_path, {
        "event": "session",
        "model": model,
        "endpoint": endpoint,
        "scope": args.llm_scope,
        "leaf_count": len(leaves),
        "batch_size": args.batch_size,
        "policy_sha256": hashlib.sha256(policy.encode("utf-8")).hexdigest(),
    })
    results: list[Finding] = []
    for offset in range(0, len(leaves), args.batch_size):
        batch = leaves[offset:offset + args.batch_size]
        batch_number = offset // args.batch_size + 1
        batch_total = (len(leaves) + args.batch_size - 1) // args.batch_size
        verbose(args, f"LLM batch {batch_number}/{batch_total}: {len(batch)} leaves")
        payload_items = [{"id": leaf.uid, "file": Path(leaf.file).name, "namespace": leaf.namespace,
                          "mode": leaf.mode, "category": leaf.category, "line": leaf.line, "text": leaf.text} for leaf in batch]
        instruction = (
            "Audit every supplied wildcard leaf against the policy. Return JSON only as an array with one object per input ID. "
            "Each object must contain id, classification (pass, definite_failure, uncertain), failed_test, and reason. "
            "Apply the prompt-language section matching each item's mode. Do not omit IDs and do not add IDs. "
            "Modular leaves may be partial.\n\nPOLICY:\n" + policy
        )
        body = json.dumps({
            "model": model,
            "temperature": 0,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(payload_items, ensure_ascii=False)}],
        }).encode("utf-8")
        trace_event(trace_path, {"event": "request", "batch": batch_number, "items": payload_items})
        request = urllib.request.Request(endpoint, data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data["choices"][0]["message"]["content"].strip()
            trace_event(trace_path, {"event": "response", "batch": batch_number, "content": content})
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
            reviewed = json.loads(content)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
            raise RuntimeError(f"LLM request failed for batch starting at {offset}: {exc}") from exc
        if not isinstance(reviewed, list) or not all(isinstance(item, dict) for item in reviewed):
            raise RuntimeError(f"LLM response for batch starting at {offset} must be a JSON array of objects")
        by_id = {leaf.uid: leaf for leaf in batch}
        received: set[str] = set()
        for item in reviewed:
            uid = str(item.get("id", ""))
            if uid not in by_id:
                continue
            received.add(uid)
            classification = item.get("classification", "uncertain")
            if classification == "pass":
                continue
            leaf = by_id[uid]
            results.append(Finding(
                "error" if classification == "definite_failure" else "warning",
                str(item.get("failed_test") or "llm_semantic_review"), str(item.get("reason") or classification),
                leaf.file, leaf.line, leaf.category, leaf.uid, source="llm",
            ))
        for uid in by_id.keys() - received:
            leaf = by_id[uid]
            results.append(Finding("error", "llm_incomplete", "LLM response omitted this leaf", leaf.file, leaf.line, leaf.category, leaf.uid, source="llm"))
    return results


def llm_suggest_fixes(
    leaves: list[Leaf], findings: list[Finding], args: argparse.Namespace, trace_path: Path | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    model = args.model or os.getenv("OPENAI_MODEL")
    base_url = (args.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    api_key = os.getenv(args.api_key_env)
    if not model or not api_key:
        raise ValueError("model and API key are required for fix suggestions")
    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    leaf_by_id = {leaf.uid: leaf for leaf in leaves}
    selected_rules = {name.strip() for name in (args.fix_rules or "").split(",") if name.strip()}
    eligible_severities = {"error", "warning"} if args.fix_severity == "both" else {args.fix_severity}
    eligible = [
        finding for finding in findings
        if finding.rule not in STRUCTURAL_FIX_RULES
        and (finding.rule in selected_rules if selected_rules else finding.severity in eligible_severities)
    ]
    issues: dict[str, list[Finding]] = {}
    for finding in eligible:
        if finding.leaf_id in leaf_by_id:
            issues.setdefault(finding.leaf_id, []).append(finding)
    targets = [leaf_by_id[uid] for uid in issues]
    suggestions: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for offset in range(0, len(targets), args.batch_size):
        batch = targets[offset:offset + args.batch_size]
        batch_number = offset // args.batch_size + 1
        batch_total = (len(targets) + args.batch_size - 1) // args.batch_size
        verbose(args, f"fix-suggestion batch {batch_number}/{batch_total}: {len(batch)} leaves")
        items = [{
            "id": leaf.uid,
            "mode": leaf.mode,
            "category": leaf.category,
            "text": leaf.text,
            "issues": [{"rule": finding.rule, "message": finding.message, "evidence": finding.evidence} for finding in issues[leaf.uid]],
        } for leaf in batch]
        instruction = (
            "Propose one replacement wildcard leaf for every supplied item. Fix only the listed issues while preserving all valid visible facts, "
            "theme, subject count, distinct actions, format, and compact wildcard style. Do not add decorative detail, generic quality terms, "
            "negative prompts, new people, or a new camera. Preserve every __namespace/category__ wildcard reference and its weight exactly; "
            "never replace a reference with literal text. Preserve the item's declared mode. For tags mode, return a flat comma-separated "
            "tag list without dangling mini-prose fragments such as 'clutched in one hand'; keep each relationship attached to its subject or "
            "object. Never return sequential, multi-panel, or multi-page content. For narrative mode, a sequential rewrite is allowed only "
            "when the original requires multiple moments. "
            "Return JSON only as an array with exactly one object per input ID containing id, suggested_rewrite, and rationale. "
            "Do not modify files and do not omit IDs."
        )
        body = json.dumps({
            "model": model,
            "temperature": 0,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
        }).encode("utf-8")
        trace_event(trace_path, {"event": "fix_request", "batch": batch_number, "items": items})
        request = urllib.request.Request(endpoint, data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data["choices"][0]["message"]["content"].strip()
            trace_event(trace_path, {"event": "fix_response", "batch": batch_number, "content": content})
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
            reviewed = json.loads(content)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
            raise RuntimeError(f"fix-suggestion request failed for batch starting at {offset}: {exc}") from exc
        if not isinstance(reviewed, list) or not all(isinstance(item, dict) for item in reviewed):
            raise RuntimeError(f"fix-suggestion response for batch starting at {offset} must be a JSON array of objects")
        expected = {leaf.uid for leaf in batch}
        received: set[str] = set()
        for item in reviewed:
            uid = str(item.get("id", ""))
            rewrite = " ".join(str(item.get("suggested_rewrite", "")).split())
            if uid in expected and rewrite:
                if rewrite != leaf_by_id[uid].text:
                    suggestions[uid] = rewrite
                rationales[uid] = " ".join(str(item.get("rationale", "")).split())
                received.add(uid)
        if received != expected:
            missing = ", ".join(sorted(expected - received))
            raise RuntimeError(f"fix-suggestion response omitted or returned an empty rewrite for: {missing}")
    return suggestions, rationales


def reference_tokens(text: str) -> tuple[str, ...]:
    """Return exact wildcard references, including any surrounding weight syntax."""
    tokens: list[str] = []
    for match in REFERENCE_RE.finditer(text):
        start, end = match.span()
        weighted = re.search(r"\(__[A-Za-z0-9_-]+/[A-Za-z0-9_-]+__:-?(?:\d+(?:\.\d+)?|\.\d+)\)", text[max(0, start - 1):end + 16])
        tokens.append(weighted.group(0) if weighted else match.group(0))
    return tuple(tokens)


def validate_suggestions(
    leaves: list[Leaf], suggestions: dict[str, str], findings: list[Finding],
    rules: dict[str, Any], tags_rules: dict[str, Any],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Reject structural changes and rewrites that leave or introduce targeted errors."""
    leaf_by_id = {leaf.uid: leaf for leaf in leaves}
    targeted_errors: dict[str, set[str]] = {}
    deterministic_errors: dict[str, set[str]] = {}
    for finding in findings:
        if finding.severity == "error" and finding.leaf_id:
            targeted_errors.setdefault(finding.leaf_id, set()).add(finding.rule)
            if finding.source == "deterministic":
                deterministic_errors.setdefault(finding.leaf_id, set()).add(finding.rule)
    accepted: dict[str, str] = {}
    rejected: dict[str, list[str]] = {}
    for uid, rewrite in suggestions.items():
        leaf = leaf_by_id[uid]
        reasons: list[str] = []
        if sorted(reference_tokens(leaf.text)) != sorted(reference_tokens(rewrite)):
            reasons.append("wildcard references or their weights changed")
        if len(ALTERNATIVE_RE.findall(leaf.text)) != len(ALTERNATIVE_RE.findall(rewrite)):
            reasons.append("alternative-choice markers changed")
        candidate = Leaf(
            leaf.uid, leaf.file, leaf.namespace, leaf.category, leaf.index, leaf.line,
            rewrite, tuple(REFERENCE_RE.findall(rewrite)), leaf.mode,
        )
        post = pattern_findings([candidate], rules) + tags_mode_findings([candidate], tags_rules)
        baseline_local = pattern_findings([leaf], rules) + tags_mode_findings([leaf], tags_rules)
        locally_evaluable = {finding.rule for finding in baseline_local}
        unsupported = deterministic_errors.get(uid, set()) - locally_evaluable
        if unsupported:
            reasons.append("structural error is not safely auto-fixable: " + ", ".join(sorted(unsupported)))
        post_errors = {finding.rule for finding in post if finding.severity == "error"}
        unresolved = targeted_errors.get(uid, set()) & post_errors
        if unresolved:
            reasons.append("targeted error remains: " + ", ".join(sorted(unresolved)))
        baseline_rules = {
            finding.rule for finding in findings
            if finding.leaf_id == uid and finding.source == "deterministic"
        }
        introduced = {finding.rule for finding in post if finding.rule not in baseline_rules}
        if introduced:
            reasons.append("new deterministic finding: " + ", ".join(sorted(introduced)))
        if reasons:
            rejected[uid] = reasons
        else:
            accepted[uid] = rewrite
    return accepted, rejected


def llm_verify_fixes(
    leaves: list[Leaf], suggestions: dict[str, str], findings: list[Finding],
    args: argparse.Namespace, trace_path: Path | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Use a separate LLM pass to reject rewrites that change valid semantics."""
    if not suggestions:
        return {}, {}
    model = args.model or os.getenv("OPENAI_MODEL")
    base_url = (args.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    api_key = os.getenv(args.api_key_env)
    if not model or not api_key:
        raise ValueError("model and API key are required for fix verification")
    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    leaf_by_id = {leaf.uid: leaf for leaf in leaves}
    issues: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.leaf_id in suggestions:
            issues.setdefault(finding.leaf_id, []).append(finding)
    targets = [leaf_by_id[uid] for uid in suggestions]
    accepted: dict[str, str] = {}
    rejected: dict[str, list[str]] = {}
    verification_batch_size = args.verification_batch_size
    for offset in range(0, len(targets), verification_batch_size):
        batch = targets[offset:offset + verification_batch_size]
        batch_number = offset // verification_batch_size + 1
        batch_total = (len(targets) + verification_batch_size - 1) // verification_batch_size
        verbose(args, f"fix-verification batch {batch_number}/{batch_total}: {len(batch)} leaves")
        items = [{
            "id": leaf.uid, "mode": leaf.mode, "category": leaf.category,
            "original": leaf.text, "rewrite": suggestions[leaf.uid],
            "issues": [{"rule": finding.rule, "message": finding.message} for finding in issues.get(leaf.uid, [])],
        } for leaf in batch]
        instruction = (
            "Act as a strict preservation verifier for wildcard rewrites. Compare every original and rewrite. A rewrite passes only when it "
            "fixes the listed issue while preserving every valid visible subject, object, attribute, relationship, action, alternative choice "
            "(especially 'or'), and compositional fact. Reject removal of supporting details, invention of replacement details, conversion of "
            "alternatives into simultaneous requirements, weakened or changed relationships, and semantic evasion through paraphrase. Changes "
            "strictly necessary to replace an explicitly listed invisible/abstract failure with minimal visible evidence are allowed. Formatting "
            "and compact wording changes alone are allowed. Return JSON only as an array with exactly one object per input ID containing id, "
            "classification (pass or reject), reason, and violations (an array chosen from fact_removed, fact_added, alternative_changed, "
            "relationship_changed, subject_or_action_changed, format_evasion, other). Do not omit IDs."
        )
        body = json.dumps({
            "model": model, "temperature": 0,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
        }).encode("utf-8")
        trace_event(trace_path, {"event": "verify_request", "batch": batch_number, "items": items})
        request = urllib.request.Request(endpoint, data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data["choices"][0]["message"]["content"].strip()
            trace_event(trace_path, {"event": "verify_response", "batch": batch_number, "content": content})
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
            reviewed = json.loads(content)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
            raise RuntimeError(f"fix verification failed for batch starting at {offset}: {exc}") from exc
        if not isinstance(reviewed, list) or not all(isinstance(item, dict) for item in reviewed):
            raise RuntimeError(f"fix verification response for batch starting at {offset} must be a JSON array of objects")
        expected = {leaf.uid for leaf in batch}
        received: set[str] = set()
        for item in reviewed:
            uid = str(item.get("id", ""))
            if uid not in expected:
                continue
            received.add(uid)
            if item.get("classification") == "pass":
                accepted[uid] = suggestions[uid]
            else:
                violations = item.get("violations", [])
                labels = ", ".join(str(value) for value in violations) if isinstance(violations, list) else str(violations)
                reason = " ".join(str(item.get("reason", "semantic preservation check failed")).split())
                rejected[uid] = [f"semantic verification: {labels}: {reason}" if labels else f"semantic verification: {reason}"]
        for uid in expected - received:
            rejected[uid] = ["semantic verification response omitted this rewrite"]
    return accepted, rejected


def write_fix_manifest(
    path: Path, leaves: list[Leaf], proposed: dict[str, str], accepted: dict[str, str],
    rejected: dict[str, list[str]], rationales: dict[str, str], findings: list[Finding],
) -> None:
    leaf_by_id = {leaf.uid: leaf for leaf in leaves}
    issues: dict[str, list[dict[str, str]]] = {}
    for finding in findings:
        if finding.leaf_id in proposed:
            issues.setdefault(finding.leaf_id, []).append({
                "severity": finding.severity, "rule": finding.rule,
                "message": finding.message, "source": finding.source,
            })
    records = []
    for uid, rewrite in proposed.items():
        leaf = leaf_by_id[uid]
        records.append({
            "id": uid, "file": leaf.file, "line": leaf.line, "category": leaf.category,
            "status": "accepted" if uid in accepted else "rejected",
            "original": leaf.text, "suggested_rewrite": rewrite,
            "rationale": rationales.get(uid, ""), "issues": issues.get(uid, []),
            "rejection_reasons": rejected.get(uid, []),
        })
    payload = {
        "summary": {"proposed": len(proposed), "accepted": len(accepted), "rejected": len(rejected)},
        "fixes": records,
    }
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def unified_leaf_diff(original: str, suggestion: str) -> str:
    return "\n".join(difflib.unified_diff(
        [original], [suggestion], fromfile="original", tofile="potential-fix", lineterm=""
    ))


def ansi(text: str, code: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def write_fixed_file(source: Path, destination: Path, leaves: list[Leaf], suggestions: dict[str, str]) -> int:
    source = source.resolve()
    destination = destination.expanduser().resolve()
    if source == destination:
        raise ValueError("--fixed-output must not overwrite the original YAML file")
    source_leaves = {leaf.uid: leaf for leaf in leaves if Path(leaf.file).resolve() == source}
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    applied = 0
    for uid, rewrite in suggestions.items():
        leaf = source_leaves.get(uid)
        if leaf is None or leaf.line < 1 or leaf.line > len(lines):
            continue
        original_line = lines[leaf.line - 1]
        match = re.match(r"^(\s*-\s*).*(\r?\n)?$", original_line)
        if not match:
            raise RuntimeError(f"cannot safely replace leaf at {source}:{leaf.line}")
        newline = match.group(2) or ""
        lines[leaf.line - 1] = f"{match.group(1)}{json.dumps(rewrite, ensure_ascii=False)}{newline}"
        applied += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.write_text("".join(lines), encoding="utf-8")
    try:
        yaml.safe_load(temporary_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"generated fixed file was invalid YAML: {exc}") from exc
    os.replace(temporary_path, destination)
    return applied


def render(findings: list[Finding], leaves: list[Leaf], fmt: str, color: bool = False) -> str:
    counts = {severity: sum(f.severity == severity for f in findings) for severity in ("error", "warning")}
    files = len({leaf.file for leaf in leaves})
    categories = len({(leaf.namespace, leaf.category) for leaf in leaves})
    leaf_by_id = {leaf.uid: leaf for leaf in leaves}
    if fmt == "json":
        rendered_findings = []
        for finding in findings:
            item = asdict(finding)
            leaf = leaf_by_id.get(finding.leaf_id)
            item["original_text"] = leaf.text if leaf else ""
            item["diff"] = unified_leaf_diff(leaf.text, finding.suggestion) if leaf and finding.suggestion else ""
            rendered_findings.append(item)
        return json.dumps({"summary": {"files": files, "categories": categories, "leaves": len(leaves), **counts}, "findings": rendered_findings}, indent=2, ensure_ascii=False)
    if fmt == "markdown":
        lines = ["# Wildcard lint report", "", f"Files: {files} · Categories: {categories} · Leaves: {len(leaves)} · Errors: {counts['error']} · Warnings: {counts['warning']}", ""]
        for finding in findings:
            location = f"{finding.file}:{finding.line}" if finding.line else finding.file
            llm_badge = " · **LLM**" if finding.source == "llm" else ""
            marker = "🔴" if finding.severity == "error" else "🟠"
            lines.extend(["---", "", f"### {marker} {finding.severity.upper()} · `{finding.rule}`{llm_badge}", "", f"Location: `{location}`", "", finding.message])
            if finding.evidence:
                lines.extend(["", f"Evidence: `{finding.evidence}`"])
            if finding.suggestion:
                leaf = leaf_by_id.get(finding.leaf_id)
                lines.extend(["", "**Potential fix — LLM generated:**", "", finding.suggestion])
                if leaf:
                    lines.extend(["", "```diff", f"- {leaf.text}", f"+ {finding.suggestion}", "```"])
        return "\n".join(lines) + "\n"
    lines = [f"Scanned {files} file(s), {categories} categories, {len(leaves)} leaves: {counts['error']} error(s), {counts['warning']} warning(s)"]
    for finding in findings:
        location = f"{finding.file}:{finding.line}" if finding.line else finding.file
        evidence = f" [{finding.evidence}]" if finding.evidence else ""
        severity_color = "31;1" if finding.severity == "error" else "33;1"
        heading = ansi(finding.severity.upper(), severity_color, color)
        llm_badge = ansi(" [LLM]", "35;1", color) if finding.source == "llm" else ""
        lines.extend(["", ansi("─" * 88, "90", color), f"{heading}{llm_badge}  {location}", f"category: {finding.category}  rule: {finding.rule}", f"{finding.message}{evidence}"])
        if finding.suggestion:
            leaf = leaf_by_id.get(finding.leaf_id)
            lines.extend(["", ansi("Potential fix [LLM-generated]:", "32;1", color), finding.suggestion])
            if leaf:
                lines.extend(["", ansi(f"- {leaf.text}", "31", color), ansi(f"+ {finding.suggestion}", "32", color)])
    return "\n".join(lines) + "\n"


DETAIL_SECTION_RE = re.compile(r"^## `([^`]+)`\s*$", re.M)


def parse_details_prompts(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    matches = list(DETAIL_SECTION_RE.finditer(text))
    records: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        section = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        mode_match = re.search(r"^- Prompt mode: `([^`]+)`", section, re.M)
        post_match = re.search(r"^### Post-LLM prompt\s*\n+```text\n(.*?)\n```", section, re.M | re.S)
        records.append({
            "image": match.group(1),
            "mode": mode_match.group(1).strip() if mode_match else "Not found",
            "post_prompt": post_match.group(1).strip() if post_match else "",
        })
    return records


def validate_tag_prompt(prompt: str, max_items: int = 24) -> list[str]:
    issues: list[str] = []
    if "\n" in prompt:
        issues.append("tags output must be exactly one line")
    items = [item.strip() for item in split_top_level_commas(prompt) if item.strip()]
    if len(items) > max_items:
        issues.append(f"tags output has {len(items)} items; soft maximum is {max_items}")
    if not items or not re.match(r"^(?:no_humans|\d+(?:others?|girls?|boys?|women|men)|solo)\b", items[0], re.I):
        issues.append("first item does not declare an exact subject count or no_humans")
    if ALTERNATIVE_RE.search(prompt):
        issues.append("unresolved or/either alternative remains")
    camera_terms = {
        name for name, expression in {
            "close": r"\b(?:close[- ]?up|macro)\b",
            "medium": r"\bmedium(?:[- ]wide)?(?: shot| view| group)?\b",
            "wide": r"\b(?:wide|panoramic|full[- ]body|long shot)\b",
            "overhead": r"\b(?:overhead|top[- ]down|bird['’]?s[- ]eye)\b",
            "low-angle": r"\blow[- ]angle\b",
        }.items() if re.search(expression, prompt, re.I)
    }
    distance_terms = camera_terms & {"close", "medium", "wide"}
    if len(distance_terms) > 1:
        issues.append(f"competing camera distances remain: {', '.join(sorted(distance_terms))}")
    if re.search(r"[.!?]\s+[A-Z]", prompt):
        issues.append("sentence-like prose remains in tags output")
    without_valid_weights = WEIGHT_RE.sub("", prompt)
    malformed = re.search(r"(?:^|,)\s*[^,()]+:\d+(?:\.\d+)?(?:\s*,|$)", without_valid_weights)
    if malformed:
        issues.append("weighted tag is missing outer parentheses")
    return issues


def audit_post_prompts(path: Path) -> list[PromptAudit]:
    audits: list[PromptAudit] = []
    for record in parse_details_prompts(path):
        mode = record["mode"]
        prompt = record["post_prompt"]
        issues: list[str] = []
        if not prompt or prompt == "_Not found_" or mode == "Not found":
            status = "unable"
            issues.append("prompt mode or post-LLM prompt is missing")
        elif mode == "Tags":
            issues = validate_tag_prompt(prompt)
            status = "noncompliant" if issues else "compliant"
        elif mode == "Narrative":
            if len([part for part in prompt.split("\n\n") if part.strip()]) != 1:
                issues.append("narrative output must contain exactly one prose block")
            status = "noncompliant" if issues else "compliant"
        elif mode == "Narrative and Tags":
            blocks = [part.strip() for part in prompt.split("\n\n") if part.strip()]
            if len(blocks) != 2:
                issues.append("combined output must contain exactly two blocks")
            else:
                issues.extend(validate_tag_prompt(blocks[1]))
            status = "noncompliant" if issues else "compliant"
        else:
            status = "unable"
            issues.append(f"unrecognized prompt mode: {mode}")
        audits.append(PromptAudit(record["image"], mode, status, issues, prompt))
    return audits


def render_prompt_audit(audits: list[PromptAudit], fmt: str) -> str:
    counts = {status: sum(a.status == status for a in audits) for status in ("compliant", "noncompliant", "unable")}
    if fmt == "json":
        return json.dumps({"summary": counts, "images": [asdict(audit) for audit in audits]}, indent=2, ensure_ascii=False) + "\n"
    if fmt == "markdown":
        lines = ["# Post-prompt validation", "", f"Audited {len(audits)} generated image prompt(s): {counts['compliant']} compliant, {counts['noncompliant']} noncompliant, {counts['unable']} unable to validate.", "", "> This is a post-generation audit. It does not reject an image or alter workflow output."]
        for audit in audits:
            lines.extend(["", f"## `{audit.image}`", "", f"- Mode: `{audit.mode}`", f"- Audit status: **{audit.status}**"])
            if audit.issues:
                lines.extend(["", *[f"- {issue}" for issue in audit.issues]])
        return "\n".join(lines) + "\n"
    lines = [f"Audited {len(audits)} generated image prompt(s): {counts['compliant']} compliant, {counts['noncompliant']} noncompliant, {counts['unable']} unable"]
    for audit in audits:
        lines.append(f"{audit.status.upper()} {audit.image} [{audit.mode}]")
        lines.extend(f"  - {issue}" for issue in audit.issues)
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    rules_path = args.rules or script_dir / "rules.yaml"
    prompt_path = args.prompt or script_dir.parent / "prompt.md"
    try:
        if args.annotated_details and not args.validate_post_prompts:
            raise ValueError("--annotated-details requires --validate-post-prompts")
        if args.validate_post_prompts:
            if args.paths:
                raise ValueError("YAML paths cannot be combined with --validate-post-prompts")
            audits = audit_post_prompts(args.validate_post_prompts.expanduser().resolve())
            report = render_prompt_audit(audits, args.format)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(report, encoding="utf-8")
            else:
                sys.stdout.write(report)
            if args.annotated_details:
                original = args.validate_post_prompts.read_text(encoding="utf-8").rstrip()
                annotation = render_prompt_audit(audits, "markdown")
                args.annotated_details.parent.mkdir(parents=True, exist_ok=True)
                args.annotated_details.write_text(original + "\n\n---\n\n" + annotation, encoding="utf-8")
            if args.fail_on == "never":
                return 0
            if any(audit.status == "unable" for audit in audits):
                return 1
            if args.fail_on == "warning" and any(audit.status == "noncompliant" for audit in audits):
                return 1
            return 0
        if not args.paths:
            raise ValueError("provide at least one YAML path or --validate-post-prompts")
        if args.batch_size < 1:
            raise ValueError("--batch-size must be at least 1")
        if args.verification_batch_size < 1:
            raise ValueError("--verification-batch-size must be at least 1")
        if args.timeout < 1:
            raise ValueError("--timeout must be at least 1 second")
        if args.suggest_fixes and not args.llm:
            raise ValueError("--suggest-fixes requires --llm")
        if args.fixed_output and (not args.llm or not args.suggest_fixes):
            raise ValueError("--fixed-output requires --llm and --suggest-fixes")
        if args.fix_manifest and (not args.llm or not args.suggest_fixes):
            raise ValueError("--fix-manifest requires --llm and --suggest-fixes")
        if args.llm_scope == "content" and (not args.llm or not args.suggest_fixes or not args.fixed_output):
            raise ValueError("--llm-scope content requires --llm, --suggest-fixes, and --fixed-output")
        paths = discover_paths(args.paths)
        if args.fixed_output and len(paths) != 1:
            raise ValueError("--fixed-output requires exactly one input YAML file")
        verbose(args, f"discovered {len(paths)} YAML file(s)")
        rules = load_rules(rules_path)
        verbose(args, f"loaded general rules from {rules_path}")
        leaves, categories, findings = load_inventory(paths)
        verbose(args, f"inventoried {len(categories)} categories and {len(leaves)} leaves")
        pattern_results = pattern_findings(leaves, rules)
        findings.extend(pattern_results)
        verbose(args, f"pattern checks produced {len(pattern_results)} finding(s)")
        tags_rules_path = args.tags_rules or script_dir / "tags-rules.yaml"
        tags_rules = load_rules(tags_rules_path)
        verbose(args, f"loaded tags-mode rules from {tags_rules_path}")
        tags_results = tags_mode_findings(leaves, tags_rules)
        findings.extend(tags_results)
        verbose(args, f"tags-mode checks produced {len(tags_results)} finding(s)")
        graph_results = graph_findings(leaves, categories)
        findings.extend(graph_results)
        verbose(args, f"reference and route checks produced {len(graph_results)} finding(s)")
        motif_results = route_motif_findings(categories, rules)
        findings.extend(motif_results)
        verbose(args, f"route motif checks produced {len(motif_results)} finding(s)")
        policy_results = namespace_policy_findings(leaves, categories, rules)
        findings.extend(policy_results)
        verbose(args, f"namespace policy checks produced {len(policy_results)} finding(s)")
        suggestions: dict[str, str] = {}
        proposed_suggestions: dict[str, str] = {}
        rejected_suggestions: dict[str, list[str]] = {}
        fix_rationales: dict[str, str] = {}
        if args.llm:
            candidate_ids = {finding.leaf_id for finding in findings if finding.leaf_id}
            if args.llm_scope == "all":
                review_leaves = leaves
            else:
                review_ids = set(candidate_ids)
                if args.llm_scope == "content":
                    content_ids = {leaf.uid for leaf in leaves if leaf.uid not in candidate_ids and has_literal_content(leaf)}
                    review_ids.update(content_ids)
                    verbose(args, f"added {len(content_ids)} clean content-bearing leaves; router-only leaves excluded")
                review_leaves = [leaf for leaf in leaves if leaf.uid in review_ids]
            trace_path = args.llm_log
            if trace_path is None and args.verbose:
                descriptor, temporary_name = tempfile.mkstemp(prefix="wildcard-linter-llm-", suffix=".jsonl")
                os.close(descriptor)
                trace_path = Path(temporary_name)
            if trace_path is not None:
                trace_path = trace_path.expanduser().resolve()
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                trace_path.write_text("", encoding="utf-8")
                verbose(args, f"sanitized LLM trace: {trace_path}")
            verbose(args, f"submitting {len(review_leaves)} leaves for {args.llm_scope} LLM review")
            llm_results = llm_review(review_leaves, args, prompt_path.read_text(encoding="utf-8"), trace_path)
            findings.extend(llm_results)
            verbose(args, f"LLM review produced {len(llm_results)} finding(s)")
            if args.suggest_fixes:
                verbose(args, "requesting potential fixes for found leaf issues")
                proposed_suggestions, fix_rationales = llm_suggest_fixes(leaves, findings, args, trace_path)
                suggestions, rejected_suggestions = validate_suggestions(
                    leaves, proposed_suggestions, findings, rules, tags_rules
                )
                verbose(args, f"deterministic validation accepted {len(suggestions)} of {len(proposed_suggestions)} potential fixes")
                if not args.skip_fix_verification:
                    suggestions, semantic_rejections = llm_verify_fixes(
                        leaves, suggestions, findings, args, trace_path
                    )
                    rejected_suggestions.update(semantic_rejections)
                for finding in findings:
                    if finding.leaf_id in suggestions:
                        finding.suggestion = suggestions[finding.leaf_id]
                verbose(args, f"accepted {len(suggestions)} of {len(proposed_suggestions)} potential fixes; rejected {len(rejected_suggestions)}")
                if args.fix_manifest:
                    write_fix_manifest(
                        args.fix_manifest, leaves, proposed_suggestions, suggestions,
                        rejected_suggestions, fix_rationales, findings,
                    )
                    verbose(args, f"wrote fix manifest to {args.fix_manifest.expanduser().resolve()}")
        if args.fixed_output:
            applied = write_fixed_file(paths[0], args.fixed_output, leaves, suggestions)
            verbose(args, f"wrote fixed copy to {args.fixed_output.expanduser().resolve()} with {applied} replacement(s)")
        findings.sort(key=lambda f: (f.file, f.line, f.severity, f.rule))
        use_color = args.format == "text" and not args.output and (args.color == "always" or (args.color == "auto" and sys.stdout.isatty()))
        report = render(findings, leaves, args.format, use_color)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
            verbose(args, f"wrote report to {args.output.resolve()}")
        else:
            sys.stdout.write(report)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"wildcard_linter: {exc}", file=sys.stderr)
        return 2
    if args.fail_on == "never":
        return 0
    if any(f.severity == "error" for f in findings):
        return 1
    if args.fail_on == "warning" and any(f.severity == "warning" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
