#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML>=6.0.2", "numpy>=2.0"]
# ///

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "wildcard_linter.py"
INDEX_PATH = MODULE_PATH.with_name("danbooru_index.py")
INDEX_SPEC = importlib.util.spec_from_file_location("danbooru_index", INDEX_PATH)
assert INDEX_SPEC and INDEX_SPEC.loader
INDEX_MODULE = importlib.util.module_from_spec(INDEX_SPEC)
sys.modules[INDEX_SPEC.name] = INDEX_MODULE
INDEX_SPEC.loader.exec_module(INDEX_MODULE)
SPEC = importlib.util.spec_from_file_location("wildcard_linter", MODULE_PATH)
assert SPEC and SPEC.loader
LINTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LINTER
SPEC.loader.exec_module(LINTER)


class WildcardLinterTests(unittest.TestCase):
    def test_canonical_candidates_prefer_configured_index_retriever(self):
        vocabulary = LINTER.DanbooruVocabulary({"grey_suit", "business_suit"})

        class FakeRetriever:
            def candidates(self, value, limit):
                self.request = (value, limit)
                return ["business_suit"]

        retriever = FakeRetriever()
        candidates = LINTER.canonical_candidates("formal_outfit", vocabulary, 3, retriever)
        self.assertEqual(candidates, ["business_suit"])
        self.assertEqual(retriever.request, ("formal_outfit", 3))

    def test_plain_literal_compound_receives_canonical_candidate_review(self):
        vocabulary = LINTER.DanbooruVocabulary({"glass", "dome", "greenhouse", "obidome"})
        leaf = LINTER.Leaf(
            "bio", "test.yaml", "gkr", "scene", 0, 10,
            "1person, jumpsuit, glass biodome, science_fiction", (), "tags",
        )

        class FakeRetriever:
            def candidates(self, value, limit):
                self.request = (value, limit)
                return ["obidome", "dome", "greenhouse"]

        retriever = FakeRetriever()
        findings = LINTER.canonical_literal_concept_findings(
            [leaf], vocabulary, retriever, candidate_count=5,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "canonical_literal_concept")
        evidence = json.loads(findings[0].evidence)
        self.assertEqual(evidence["input"], "glass biodome")
        self.assertEqual(evidence["status"], "literal_phrase_candidate_review")
        self.assertEqual(evidence["known_canonical_components"], ["glass"])
        self.assertEqual(evidence["unknown_words"], ["biodome"])
        self.assertIn("dome", evidence["candidates"])
        self.assertFalse(LINTER.canonical_compound_component("golden", "gold"))
        self.assertFalse(LINTER.canonical_compound_component("stained", "stain"))
        self.assertTrue(LINTER.canonical_compound_component("biodome", "dome"))

    def test_literal_review_suppresses_candidates_without_compatible_noun_head(self):
        vocabulary = LINTER.DanbooruVocabulary({"sitting", "railroad_tracks", "seat"})
        leaf = LINTER.Leaf(
            "vehicle", "test.yaml", "gkr", "vehicle_spotlight", 0, 10,
            "hover_vehicle, strapped in, magnetic rails", (), "tags",
        )

        class FakeRetriever:
            def __init__(self):
                self.requests = []

            def candidates(self, value, limit):
                self.requests.append(value)
                return {
                    "strapped in": ["sitting", "seat"],
                    "magnetic rails": ["railroad_tracks"],
                }.get(value, [])

        retriever = FakeRetriever()
        findings = LINTER.canonical_literal_concept_findings(
            [leaf], vocabulary, retriever, candidate_count=5,
        )
        self.assertEqual(
            retriever.requests,
            ["strapped in", "strapped", "magnetic rails", "magnetic", "rails"],
        )
        self.assertEqual(findings, [])

    def test_literal_review_suppresses_composition_of_known_tags(self):
        vocabulary = LINTER.DanbooruVocabulary({"spandex", "suit", "business_suit"})
        leaf = LINTER.Leaf(
            "known", "test.yaml", "gkr", "archetype", 0, 10,
            "1other, spandex suit", (), "tags",
        )

        class FakeRetriever:
            def candidates(self, value, limit):
                raise AssertionError("known compositions should not require retrieval")

        self.assertEqual(
            LINTER.canonical_literal_concept_findings([leaf], vocabulary, FakeRetriever()), []
        )

    def test_literal_review_builds_component_tag_set_palette(self):
        vocabulary = LINTER.DanbooruVocabulary({"ruins", "rubble", "stone_wall", "arch", "column"})
        leaf = LINTER.Leaf(
            "place", "test.yaml", "gkr", "vehicle_spotlight", 0, 10,
            "crumbling stone cloister", (), "tags",
        )

        class FakeRetriever:
            def candidates(self, value, limit):
                return {
                    "crumbling stone cloister": [],
                    "crumbling": ["ruins", "rubble"],
                    "stone": ["stone_wall"],
                    "cloister": ["arch", "column"],
                }.get(value, [])

        findings = LINTER.canonical_literal_concept_findings([leaf], vocabulary, FakeRetriever())
        self.assertEqual(len(findings), 1)
        evidence = json.loads(findings[0].evidence)
        self.assertEqual(
            evidence["candidate_tag_set_palette"],
            ["ruins", "rubble", "stone_wall", "arch", "column"],
        )
        self.assertEqual([item["text"] for item in evidence["component_guidance"]], [
            "crumbling", "stone", "cloister",
        ])

    def test_compact_canonical_relationship_compositions_are_recognized(self):
        vocabulary = LINTER.DanbooruVocabulary({
            "standing", "against_mirror", "holding", "black_rose", "steering_wheel",
            "playing_card", "machinery", "holographic_interface", "black", "wax_seal",
        })
        expected = {
            "standing against_mirror": ["standing", "against_mirror"],
            "holding black_rose": ["holding", "black_rose"],
            "hands on (steering_wheel:1.2)": ["steering_wheel"],
            "looking at playing_card": ["playing_card"],
            "leaning against machinery": ["machinery"],
            "scanning holographic_interface": ["holographic_interface"],
            "black wax_seal": ["black", "wax_seal"],
        }
        for phrase, components in expected.items():
            self.assertEqual(
                LINTER.canonical_relationship_components(phrase, vocabulary), components,
            )
        self.assertEqual(
            LINTER.canonical_relationship_components("holding invented_object", vocabulary), [],
        )
        leaf = LINTER.Leaf(
            "relations", "test.yaml", "gkr", "scene", 0, 10,
            ", ".join(expected), (), "tags",
        )
        self.assertEqual(
            LINTER.exact_canonical_relationship_items(leaf, vocabulary),
            [
                {"text": phrase, "canonical_components": components}
                for phrase, components in expected.items()
            ],
        )
        self.assertEqual(LINTER.canonical_tag_guidance(leaf, vocabulary, 5), [])

    def test_validation_rejects_splitting_verified_compact_relationship(self):
        vocabulary = LINTER.DanbooruVocabulary({"playing_card"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "cowboy, looking at playing_card, saloon", (), "tags",
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "cowboy, looking at, playing_card, saloon"}, [],
            self.rules, self.tags_rules, vocabulary,
        )
        self.assertEqual(accepted, {})
        self.assertTrue(any("verified compact relationship" in reason for reason in rejected[leaf.uid]))

    def test_colored_verbose_log_highlights_cache_and_request_details(self):
        stream = io.StringIO()
        message = "completed, HTTP 200; 0 cached, 1 requested; no LLM call"
        with patch.object(LINTER.sys, "stderr", stream):
            LINTER.verbose(SimpleNamespace(verbose=True, color="always"), message)
        output = stream.getvalue()
        self.assertIn("\033[32;1mHTTP 200\033[0m", output)
        self.assertIn("\033[32;1m0 cached\033[0m", output)
        self.assertIn("\033[33;1m1 requested\033[0m", output)
        self.assertIn("\033[32;1mno LLM call\033[0m", output)

    def setUp(self) -> None:
        self.rules = LINTER.load_rules(Path(__file__).resolve().parents[1] / "rules.yaml")
        self.tags_rules = LINTER.load_rules(Path(__file__).resolve().parents[1] / "tags-rules.yaml")

    def inventory(self, content: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "gkr-test.yaml"
        path.write_text(content, encoding="utf-8")
        return LINTER.load_inventory([path])

    def test_abstract_single_image_is_flagged(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  comedy_scene:\n    - carefully planned announcement collapsing through a chain of interruptions\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        rules = {finding.rule for finding in findings}
        self.assertIn("invisible_intent", rules)
        self.assertIn("temporal_progression", rules)
        self.assertFalse([finding for finding in findings if finding.suggestion])

    def test_interpretive_modifier_and_transition_are_flagged(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  story_scene:\n"
            "    - familiar hero fragmenting into multiple silhouettes beneath a looming tower\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        found = {finding.rule for finding in findings}
        self.assertIn("interpretive_visual_modifier", found)
        self.assertIn("temporal_state_transition", found)
        self.assertIn("ambiguous_looming", found)

    def test_concrete_ing_pose_is_not_a_transition(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  story_scene:\n    - courier holding sealed case, kneeling beside damaged bicycle\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        self.assertNotIn("temporal_state_transition", {finding.rule for finding in findings})

    def test_hyphenated_specificity_shorthand_must_be_resolved(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  scene:\n    - operative, mission-specific equipment, compact rifle, radio harness\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        self.assertIn("underspecified_specificity", {finding.rule for finding in findings})
        post = LINTER.representability_issues("operative, mission-specific equipment, compact rifle")
        self.assertIn("underspecified_specificity", {code for code, _ in post})

    def test_plain_specific_does_not_trigger_hyphenated_specificity_rule(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  scene:\n    - conservator holding a specific labeled brush beside a numbered tray\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        self.assertNotIn("underspecified_specificity", {finding.rule for finding in findings})

    def test_fix_validation_requires_representability_warning_to_be_resolved(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  story_scene:\n    - familiar street, red storefront\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        accepted, rejected = LINTER.validate_suggestions(
            leaves, {leaves[0].uid: "familiar street, blue storefront"},
            findings, self.rules, self.tags_rules,
        )
        self.assertFalse(accepted)
        self.assertIn("targeted representability warning remains", rejected[leaves[0].uid][0])

    def test_explicit_panels_suppress_temporal_warning(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  horror_scene:\n    - four-panel page progressively replacing a face with an anatomy diagram\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        self.assertNotIn("temporal_progression", {finding.rule for finding in findings})

    def test_mode_is_read_from_header(self):
        leaves, _, _ = self.inventory(
            "# GLOBAL RULE: Read prompt.md (MODE: tags)\ngkr_test:\n  subject:\n    - red coat, raised sword\n"
        )
        self.assertEqual(leaves[0].mode, "tags")

    def test_only_selects_requested_categories_and_exactly_one_reference_hop(self):
        leaves, categories, findings = self.inventory(
            "# MODE: tags\n"
            "gkr_test:\n"
            "  spotlight_covers:\n"
            "    - __gkr_test/cover_subject__, comic_cover\n"
            "  cover_subject:\n"
            "    - __gkr_test/deep_subject__, 1woman\n"
            "  deep_subject:\n"
            "    - knight\n"
            "  unrelated:\n"
            "    - landscape\n"
        )
        selected_leaves, selected_categories, selected_findings, roots = LINTER.filter_inventory(
            leaves, categories, findings, ["spotlight_covers"],
        )
        self.assertEqual(roots, {("gkr_test", "spotlight_covers")})
        self.assertEqual(
            set(selected_categories),
            {("gkr_test", "spotlight_covers"), ("gkr_test", "cover_subject")},
        )
        self.assertEqual(
            {leaf.category for leaf in selected_leaves},
            {"spotlight_covers", "cover_subject"},
        )
        self.assertFalse(selected_findings)
        graph = LINTER.graph_findings(
            selected_leaves, selected_categories, categories, partial=True,
        )
        self.assertNotIn("missing_reference", {finding.rule for finding in graph})

    def test_only_accepts_repeated_comma_and_qualified_names_and_rejects_unknown(self):
        leaves, categories, findings = self.inventory(
            "gkr_test:\n"
            "  spotlight_us_comics:\n"
            "    - city\n"
            "  spotlight_covers:\n"
            "    - comic cover\n"
        )
        _, selected, _, roots = LINTER.filter_inventory(
            leaves, categories, findings,
            ["gkr_test/spotlight_us_comics,spotlight_covers"],
        )
        self.assertEqual(set(selected), roots)
        self.assertEqual(len(roots), 2)
        with self.assertRaisesRegex(ValueError, "--only category not found: missing"):
            LINTER.filter_inventory(leaves, categories, findings, ["missing"])

    def test_only_depth_controls_recursive_reference_traversal(self):
        leaves, categories, findings = self.inventory(
            "gkr_test:\n"
            "  root:\n    - __gkr_test/child__\n"
            "  child:\n    - __gkr_test/grandchild__\n"
            "  grandchild:\n    - __gkr_test/great_grandchild__\n"
            "  great_grandchild:\n    - subject\n"
            "  unrelated:\n    - landscape\n"
        )
        expected = {
            0: {"root"},
            1: {"root", "child"},
            2: {"root", "child", "grandchild"},
            3: {"root", "child", "grandchild", "great_grandchild"},
            20: {"root", "child", "grandchild", "great_grandchild"},
        }
        for depth, names in expected.items():
            _, selected, _, _ = LINTER.filter_inventory(
                leaves, categories, findings, ["root"], depth,
            )
            self.assertEqual({category for _, category in selected}, names)
        with self.assertRaisesRegex(ValueError, "zero or greater"):
            LINTER.filter_inventory(leaves, categories, findings, ["root"], -1)

    def test_semantic_duplicate_checker_reports_closest_prior_leaf_with_context(self):
        leaves = [
            LINTER.Leaf("a", "test.yaml", "gkr", "covers", 0, 10,
                        "comic_cover, red car, desert", (), "tags"),
            LINTER.Leaf("b", "test.yaml", "gkr", "covers", 1, 11,
                        "comic_cover, crimson automobile, sandy desert", (), "tags"),
            LINTER.Leaf("c", "test.yaml", "gkr", "covers", 2, 12,
                        "comic_cover, blue ocean, sailboat", (), "tags"),
            LINTER.Leaf("d", "test.yaml", "gkr", "other", 0, 20,
                        "comic_cover, crimson automobile, sandy desert", (), "tags"),
        ]

        class FakeEmbeddingClient:
            def embed(self, inputs):
                vectors = {
                    "comic cover, red car, desert": [1.0, 0.0],
                    "comic cover, crimson automobile, sandy desert": [0.999, 0.01],
                    "comic cover, blue ocean, sailboat": [0.0, 1.0],
                }
                return [vectors[value] for value in inputs]

        findings = LINTER.semantic_duplicate_findings(
            leaves, FakeEmbeddingClient(), threshold=0.95,
        )
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.rule, "semantic_duplicate_leaf")
        self.assertEqual(finding.line, 11)
        self.assertIn("closest earlier leaf: test.yaml:10", finding.evidence)
        self.assertIn("comic_cover, red car, desert", finding.evidence)
        self.assertIn("cosine similarity:", finding.evidence)

    def test_semantic_duplicate_fix_must_fall_below_threshold(self):
        leaves = [
            LINTER.Leaf("prior", "test.yaml", "gkr", "covers", 0, 10,
                        "red car in desert", (), "narrative"),
            LINTER.Leaf("target", "test.yaml", "gkr", "covers", 1, 11,
                        "crimson automobile on sand", (), "narrative"),
        ]
        finding = LINTER.Finding(
            "warning", "semantic_duplicate_leaf", "near duplicate",
            "test.yaml", 11, "covers", "target",
        )

        class FakeEmbeddingClient:
            def embed(self, inputs):
                vectors = {
                    "red car in desert": [1.0, 0.0],
                    "scarlet vehicle among dunes": [0.999, 0.01],
                    "blue sailboat on rough ocean": [0.0, 1.0],
                }
                return [vectors[value] for value in inputs]

        accepted, rejected = LINTER.validate_suggestions(
            leaves, {"target": "scarlet vehicle among dunes"}, [finding],
            self.rules, self.tags_rules,
            semantic_duplicate_client=FakeEmbeddingClient(),
            semantic_duplicate_threshold=0.95,
        )
        self.assertNotIn("target", accepted)
        self.assertIn("semantic duplicate remains after rewrite", rejected["target"][0])
        accepted, rejected = LINTER.validate_suggestions(
            leaves, {"target": "blue sailboat on rough ocean"}, [finding],
            self.rules, self.tags_rules,
            semantic_duplicate_client=FakeEmbeddingClient(),
            semantic_duplicate_threshold=0.95,
        )
        self.assertEqual(accepted, {"target": "blue sailboat on rough ocean"})
        self.assertFalse(rejected)

    def test_json_response_parser_uses_last_valid_array_amid_commentary(self):
        content = (
            "```json\n[{\"id\": \"first\"}]\n```\n"
            "I noticed a mistake and corrected it.\n"
            "```json\n[{\"id\": \"corrected\", \"leaves\": []}]\n```"
        )
        self.assertEqual(
            LINTER.parse_json_array_response(content),
            [{"id": "corrected", "leaves": []}],
        )

    def test_json_response_parser_accepts_malformed_single_backtick_fence(self):
        content = "`json\n[{\"id\": \"item\"}]\n`\nextra explanation"
        self.assertEqual(LINTER.parse_json_array_response(content), [{"id": "item"}])

    def test_incomplete_fix_suggestion_keeps_missing_leaf_unresolved(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n"
            "    - first bad token\n"
            "    - second bad token\n"
        )
        findings = [
            LINTER.Finding(
                "warning", "test_rule", "needs repair", leaf.file, leaf.line,
                leaf.category, leaf.uid,
            )
            for leaf in leaves
        ]
        original_request = LINTER.llm_json_request
        self.addCleanup(setattr, LINTER, "llm_json_request", original_request)
        LINTER.llm_json_request = lambda **kwargs: [
            {"id": leaves[0].uid, "suggested_rewrite": "first repaired token", "rationale": "fixed"},
            {"id": leaves[1].uid, "suggested_rewrite": "", "rationale": "unable"},
        ]
        fix_args = SimpleNamespace(
            model="test", base_url="http://localhost:11434/v1", api_key_env="PATH",
            fix_rules="", fix_severity="both", canonical_tag_suggestions=False,
            batch_size=20, canonical_tag_candidate_count=5, canonical_tag_style="underscore",
            verbose=False,
        )
        suggestions, rationales = LINTER.llm_suggest_fixes(leaves, findings, fix_args)
        self.assertEqual(suggestions, {leaves[0].uid: "first repaired token"})
        self.assertEqual(rationales, {leaves[0].uid: "fixed"})
        self.assertNotIn(leaves[1].uid, suggestions)

    def test_duplicate_findings_are_not_sent_for_invented_content_repairs(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  router:\n    - __gkr_test/subject__\n"
            "  subject:\n    - person\n"
        )
        leaf = next(item for item in leaves if item.category == "router")
        finding = LINTER.Finding(
            "warning", "cross_category_duplicate_leaf", "duplicate", leaf.file, leaf.line,
            leaf.category, leaf.uid,
        )
        original_request = LINTER.llm_json_request
        self.addCleanup(setattr, LINTER, "llm_json_request", original_request)
        LINTER.llm_json_request = lambda **kwargs: self.fail("duplicate repair must not call the LLM")
        fix_args = SimpleNamespace(
            model="test", base_url="http://localhost:11434/v1", api_key_env="PATH",
            fix_rules="", fix_severity="both", canonical_tag_suggestions=False,
            batch_size=20, canonical_tag_candidate_count=5, canonical_tag_style="underscore",
            verbose=False,
        )
        self.assertEqual(LINTER.llm_suggest_fixes(leaves, [finding], fix_args), ({}, {}))

    def test_corrective_fix_request_includes_validator_feedback(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n    - bone armor\n"
        )
        leaf = leaves[0]
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review", leaf.file, leaf.line,
            leaf.category, leaf.uid,
        )
        captured = {}
        original_request = LINTER.llm_json_request
        self.addCleanup(setattr, LINTER, "llm_json_request", original_request)

        def fake_request(**kwargs):
            captured.update(kwargs)
            return [{"id": leaf.uid, "suggested_rewrite": "plate_armor", "rationale": "compound"}]

        LINTER.llm_json_request = fake_request
        fix_args = SimpleNamespace(
            model="test", base_url="http://localhost:11434/v1", api_key_env="PATH",
            canonical_tag_suggestions=False, canonical_tag_candidate_count=5,
            canonical_tag_style="underscore", batch_size=20, max_fix_retries=2,
            verbose=False,
        )
        suggestions, _ = LINTER.llm_correct_rejected_fixes(
            leaves, [finding], {leaf.uid: "bone, armor"},
            {leaf.uid: ["compound relationship lost"]}, fix_args, 1,
        )
        self.assertEqual(suggestions[leaf.uid], "plate_armor")
        self.assertEqual(captured["items"][0]["repair_policy_version"], 2)
        self.assertIn("compound relationship lost", captured["items"][0]["rejection_reasons"])

    def test_verification_pass_number_produces_independent_request_key(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n    - grey suit\n"
        )
        leaf = leaves[0]
        captured = []
        original_request = LINTER.llm_json_request
        self.addCleanup(setattr, LINTER, "llm_json_request", original_request)

        def fake_request(**kwargs):
            captured.append(kwargs["items"][0])
            return [{"id": leaf.uid, "classification": "pass", "reason": "safe", "violations": []}]

        LINTER.llm_json_request = fake_request
        args = SimpleNamespace(
            model="test", base_url="http://localhost:11434/v1", api_key_env="PATH",
            verification_batch_size=15, verbose=False,
        )
        suggestions = {leaf.uid: "grey_suit"}
        LINTER.llm_verify_fixes(leaves, suggestions, [], args, pass_number=1)
        LINTER.llm_verify_fixes(leaves, suggestions, [], args, pass_number=2)
        self.assertEqual([item["verification_pass"] for item in captured], [1, 2])
        self.assertTrue(all(item["verification_policy_version"] == 3 for item in captured))

    def test_exact_canonical_item_is_supplied_to_visual_review(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n"
            "    - sleeping detective, dreaming, notebook with sketches\n"
        )
        vocabulary = LINTER.DanbooruVocabulary({"dreaming", "sleeping_detective"})
        self.assertEqual(
            LINTER.exact_canonical_tag_items(leaves[0], vocabulary),
            ["dreaming", "sleeping_detective"],
        )

    def test_canonical_word_inside_larger_phrase_is_not_visual_exception(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n"
            "    - detective dreaming of victory\n"
        )
        vocabulary = LINTER.DanbooruVocabulary({"dreaming"})
        self.assertEqual(LINTER.exact_canonical_tag_items(leaves[0], vocabulary), [])

    def test_missing_mode_defaults_to_narrative(self):
        leaves, _, _ = self.inventory("gkr_test:\n  subject:\n    - red coat\n")
        self.assertEqual(leaves[0].mode, "narrative")

    def test_tags_mode_rejects_sequential_format(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  action_scene:\n    - four-panel sequence, changing face\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertIn("tags_sequential_format", {finding.rule for finding in findings})

    def test_tags_mode_validates_structured_limited_palettes(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  spotlight_covers:\n"
            "    - cover, (red, copper, blue) limited_palette\n"
            "    - red_dress, blue_eyes, street\n"
            "    - cover, mahogany and gold palette\n"
            "    - cover, (deep red, gold) limited_palette\n"
            "    - cover, (red, red) limited_palette\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        palette_findings = [finding for finding in findings if finding.rule == "tags_limited_palette"]
        self.assertEqual(len(palette_findings), 3)
        self.assertEqual(
            {finding.line for finding in palette_findings},
            {6, 7, 8},
        )

    def test_tags_mode_rejects_two_page_and_adjacent_poses(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  layout:\n"
            "    - wide two-page shot, simultaneous adjacent poses\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertIn("tags_sequential_format", {finding.rule for finding in findings})

    def test_tags_mode_rejects_page_spanning_and_gutter_language(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  layout:\n"
            "    - wide shot spanning both pages, central gutter clear\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertIn("tags_sequential_format", {finding.rule for finding in findings})

    def test_tags_mode_rejects_multi_view_design_sheet(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  layout:\n"
            "    - character design sheet, front, side and back views\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertIn("tags_multi_view_format", {finding.rule for finding in findings})

    def test_tags_mode_allows_panel_border_rendering_signature(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  style:\n    - panel-border framing, single image, no sequential panels, screentone\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertNotIn("tags_sequential_format", {finding.rule for finding in findings})

    def test_tags_mode_flags_long_phrase_and_excess_weights(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n"
            "    - (very tall armored knight:1.1), (red cape:1.1), (raised sword:1.1), "
            "(burning shield:1.1), ruined ancient castle gate under crimson twilight\n"
        )
        rules = {finding.rule for finding in LINTER.tags_mode_findings(leaves, self.tags_rules)}
        self.assertIn("tags_long_phrase", rules)
        self.assertIn("tags_excessive_weights", rules)

    def test_tags_mode_flags_brush_used_as_style_shorthand(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  style:\n"
            "    - bold brush contours, dry-brush shadows, visible brushwork\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        brush = [finding for finding in findings if finding.rule == "ambiguous_brush_medium"]
        self.assertEqual(len(brush), 1)
        self.assertIn("bold brush contours", brush[0].evidence)

    def test_tags_mode_allows_a_literal_brush_object(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n"
            "    - calligrapher holding broad brush, ink-stained fingers\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertNotIn("ambiguous_brush_medium", {finding.rule for finding in findings})

    def test_narrative_mode_skips_tags_checks(self):
        leaves, _, _ = self.inventory(
            "# MODE: narrative\ngkr_test:\n  scene:\n    - a knight is standing beside the ruined castle gate\n"
        )
        self.assertFalse(LINTER.tags_mode_findings(leaves, self.tags_rules))

    def test_tags_mode_allows_long_visible_relationship(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  action:\n"
            "    - bright lightning arcing between both hands and nearby metal\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertNotIn("tags_long_phrase", {finding.rule for finding in findings})

    def test_valid_limited_palette_is_exempt_from_generic_phrase_checks(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  palette:\n"
            "    - (black, white, red) limited_palette\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertNotIn("tags_long_phrase", {finding.rule for finding in findings})
        vocabulary = LINTER.DanbooruVocabulary({"black", "white", "red", "limited_palette"})
        self.assertEqual(LINTER.canonical_tag_findings(leaves, vocabulary), [])

    def test_top_level_comma_split_preserves_weighted_phrase(self):
        parts = LINTER.split_top_level_commas(
            "(prison tattoos, worn prison garment:1.1), box of letters"
        )
        self.assertEqual(parts, ["(prison tattoos, worn prison garment:1.1)", " box of letters"])

    def test_tags_mode_flags_dangling_relation_fragment(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n"
            "    - obsolete robot, handwritten notes, clutched in claw hand\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertIn("tags_dangling_relation", {finding.rule for finding in findings})

    def test_spatial_from_to_is_not_temporal(self):
        leaves, _, initial = self.inventory(
            "gkr_test:\n  mystery_scene:\n    - string running from a locked door to an open transom\n"
        )
        findings = initial + LINTER.pattern_findings(leaves, self.rules)
        self.assertNotIn("temporal_progression", {finding.rule for finding in findings})

    def test_missing_reference_is_error(self):
        leaves, categories, initial = self.inventory(
            'gkr_test:\n  random:\n    - "__gkr_test/missing__"\n'
        )
        findings = initial + LINTER.graph_findings(leaves, categories)
        self.assertIn("missing_reference", {finding.rule for finding in findings})

    def test_camera_and_panel_pool_conflict(self):
        leaves, categories, initial = self.inventory(
            "gkr_test:\n"
            "  camera:\n    - close-up\n"
            "  scenes:\n    - four-panel sequence showing a changing object\n"
            '  combo:\n    - "__gkr_test/scenes__, __gkr_test/camera__"\n'
        )
        findings = initial + LINTER.graph_findings(leaves, categories)
        self.assertIn("camera_format_conflict", {finding.rule for finding in findings})

    def test_valid_references_pass_graph_checks(self):
        leaves, categories, initial = self.inventory(
            "gkr_test:\n"
            "  subject:\n    - mechanic tightening a cable clamp\n"
            '  random:\n    - "__gkr_test/subject__"\n'
        )
        findings = initial + LINTER.graph_findings(leaves, categories)
        self.assertFalse([finding for finding in findings if finding.severity == "error"])

    def test_unreachable_category_reports_router_and_manual_remedies(self):
        leaves, categories, initial = self.inventory(
            "gkr_test:\n"
            "  unused_pool:\n    - mechanic\n"
            "  scene:\n    - street\n"
            '  random:\n    - "__gkr_test/scene__"\n'
        )
        findings = initial + LINTER.graph_findings(leaves, categories)
        unreachable = [finding for finding in findings if finding.rule == "unreachable_category"]
        self.assertEqual([finding.category for finding in unreachable], ["unused_pool"])
        self.assertIn("random", unreachable[0].message)
        self.assertIn("remove the unused pool", unreachable[0].message)
        self.assertFalse(unreachable[0].leaf_id)

    def test_tags_mode_reports_empty_comma_phrase(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  action:\n    - diving from rooftop,\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertIn("tags_empty_phrase", {finding.rule for finding in findings})

    def test_reference_before_comma_is_not_an_empty_phrase(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  action:\n"
            '    - "__gkr_test/subject__, muscle_car, theft, comic, money bags on pavement"\n'
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertNotIn("tags_empty_phrase", {finding.rule for finding in findings})

    def test_duplicate_check_normalizes_tag_order_weights_and_underscores(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n"
            "  first:\n    - muscles, (dynamic_pose:1.2)\n"
            "  second:\n    - dynamic pose, muscles\n"
        )
        findings = LINTER.duplicate_leaf_findings(leaves)
        self.assertEqual([finding.rule for finding in findings], ["cross_category_duplicate_leaf"])
        markdown = LINTER.render(findings, leaves, "markdown")
        self.assertIn("**Duplicate comparison:**", markdown)
        self.assertIn("**Current leaf:** category **`second`**", markdown)
        self.assertIn("**Earlier matching leaf:** category **`first`**", markdown)
        self.assertIn("gkr-test.yaml:4`", markdown)
        self.assertIn("`dynamic pose, muscles`", markdown)

    def test_route_motif_probability_accounts_for_nested_routes(self):
        leaves, categories, _ = self.inventory(
            "gkr_test:\n"
            "  scenes:\n    - folded map\n    - empty street\n"
            "  random:\n    - \"__gkr_test/scenes__\"\n    - quiet portrait\n"
        )
        rules = {"route_motifs": {"maps": {
            "roots": ["random"], "max_probability": 0.20,
            "regex": [r"\bmaps?\b"],
        }}}
        findings = LINTER.route_motif_findings(categories, rules)
        self.assertEqual(len(findings), 1)
        self.assertIn("25.0%", findings[0].message)

    def test_namespace_route_exclusion_is_recursive(self):
        leaves, categories, _ = self.inventory(
            "gkr_test:\n"
            "  design:\n    - character sheet\n"
            '  middle:\n    - "__gkr_test/design__"\n'
            '  random:\n    - "__gkr_test/middle__"\n'
        )
        rules = {"namespace_policies": {"gkr_test": {
            "route_exclusions": {"random": ["design"]}
        }}}
        findings = LINTER.namespace_policy_findings(leaves, categories, rules)
        self.assertIn("route_contract_violation", {finding.rule for finding in findings})

    def test_namespace_budget_uses_recursive_expansion_probability(self):
        leaves, categories, _ = self.inventory(
            "gkr_test:\n"
            "  scene:\n    - one, two, three\n    - one\n"
            '  random:\n    - "__gkr_test/scene__"\n'
        )
        rules = {"namespace_policies": {"gkr_test": {"prompt_budgets": {
            "random": {"max_items": 2, "max_words": 20, "target_items": 1}
        }}}}
        findings = LINTER.namespace_policy_findings(leaves, categories, rules)
        self.assertEqual(len(findings), 1)
        self.assertIn("50.0%", findings[0].message)

    def test_details_audit_is_mode_aware(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "details.md"
            path.write_text(
                "# Image prompt details\n\n## `image.png`\n\n- Prompt mode: `Tags`\n\n"
                "### Post-LLM prompt\n\n```text\n1other, hero, raised sword, wide shot\n```\n",
                encoding="utf-8",
            )
            audits = LINTER.audit_post_prompts(path)
            self.assertEqual(audits[0].status, "compliant")

    def test_details_audit_flags_unresolved_tags_alternative(self):
        issues = LINTER.validate_tag_prompt("1other, red or blue coat, close-up")
        self.assertTrue(any("alternative" in issue for issue in issues))

    def test_annotated_details_inserts_status_in_matching_section(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "details.md"
            destination = Path(temporary) / "details.audited.md"
            source.write_text(
                "# Image prompt details\n\n## `one.png`\n\n- Prompt mode: `Tags`\n\n"
                "### Post-LLM prompt\n\n```text\n1other, hero\n```\n\n"
                "## `two.png`\n\n- Prompt mode: `Narrative`\n",
                encoding="utf-8",
            )
            audits = [
                LINTER.PromptAudit("one.png", "Tags", "noncompliant", ["too many tags"], ""),
                LINTER.PromptAudit("two.png", "Narrative", "compliant", [], ""),
            ]
            LINTER.write_annotated_details(source, destination, audits)
            result = destination.read_text(encoding="utf-8")
            one_section, two_section = result.split("## `two.png`")
            self.assertIn("- Audit status: **noncompliant**", one_section)
            self.assertIn("- Audit issue `audit_issue`: too many tags", one_section)
            self.assertIn("- Audit status: **compliant**", two_section)
            self.assertNotIn("# Post-prompt validation", result)

    def test_combined_markdown_report_contains_both_sections(self):
        audits = [LINTER.PromptAudit("image.png", "Tags", "compliant", [], "1other, hero")]
        report = LINTER.combine_reports("# Wildcard lint\n", audits, "markdown")
        self.assertIn("# Wildcard lint", report)
        self.assertIn("# Post-prompt validation", report)

    def test_combined_json_report_is_valid_json(self):
        audits = [LINTER.PromptAudit("image.png", "Tags", "compliant", [], "1other, hero")]
        report = LINTER.combine_reports('{"summary": {}}\n', audits, "json")
        parsed = json.loads(report)
        self.assertIn("wildcard_lint", parsed)
        self.assertIn("post_prompt_validation", parsed)

    def test_combined_run_keeps_post_prompt_audit_out_of_default_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "gkr-test.yaml"
            details = root / "details.md"
            report = root / "fixed-report.md"
            annotated = root / "details.audited.md"
            source.write_text("# MODE: tags\ngkr_test:\n  scene:\n    - red_hair\n", encoding="utf-8")
            details.write_text(
                "## `image.png`\n\n- Prompt mode: `Tags`\n\n### Post-LLM prompt\n\n```text\n1other, red_hair\n```\n",
                encoding="utf-8",
            )
            argv = [
                "wildcard_linter.py", str(source), "--validate-post-prompts", str(details),
                "--annotated-details", str(annotated), "--format", "markdown",
                "--output", str(report), "--fail-on", "never",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(LINTER.main(), 0)
            self.assertNotIn("# Post-prompt validation", report.read_text(encoding="utf-8"))
            self.assertIn("- Audit status: **compliant**", annotated.read_text(encoding="utf-8"))

    def test_combined_run_can_write_separate_or_explicitly_combined_post_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "gkr-test.yaml"
            details = root / "details.md"
            lint_report = root / "lint.md"
            post_report = root / "post.md"
            source.write_text("# MODE: tags\ngkr_test:\n  scene:\n    - red_hair\n", encoding="utf-8")
            details.write_text(
                "## `image.png`\n\n- Prompt mode: `Tags`\n\n### Post-LLM prompt\n\n```text\n1other, red_hair\n```\n",
                encoding="utf-8",
            )
            separate_argv = [
                "wildcard_linter.py", str(source), "--validate-post-prompts", str(details),
                "--post-prompt-output", str(post_report), "--format", "markdown",
                "--output", str(lint_report), "--fail-on", "never",
            ]
            with patch.object(sys, "argv", separate_argv):
                self.assertEqual(LINTER.main(), 0)
            self.assertNotIn("# Post-prompt validation", lint_report.read_text(encoding="utf-8"))
            self.assertIn("# Post-prompt validation", post_report.read_text(encoding="utf-8"))
            combined_argv = [
                "wildcard_linter.py", str(source), "--validate-post-prompts", str(details),
                "--include-post-prompt-report", "--format", "markdown",
                "--output", str(lint_report), "--fail-on", "never",
            ]
            with patch.object(sys, "argv", combined_argv):
                self.assertEqual(LINTER.main(), 0)
            self.assertIn("# Post-prompt validation", lint_report.read_text(encoding="utf-8"))

    def test_fix_report_marks_only_unfixed_findings_unresolved(self):
        leaves = [
            LINTER.Leaf("fixed", "test.yaml", "gkr", "scene", 0, 1, "old", (), "tags"),
            LINTER.Leaf("open", "test.yaml", "gkr", "scene", 1, 2, "other", (), "tags"),
        ]
        findings = [
            LINTER.Finding("warning", "first", "fixed issue", "test.yaml", 1, "scene", "fixed"),
            LINTER.Finding("warning", "second", "open issue", "test.yaml", 2, "scene", "open"),
        ]
        markdown = LINTER.render(
            findings, leaves, "markdown", fix_attempted=True, fixed_leaf_ids={"fixed"},
        )
        self.assertEqual(markdown.count("[UNRESOLVED]"), 2)  # summary hint plus one finding
        self.assertNotIn("`first` · **[UNRESOLVED]**", markdown)
        self.assertIn("`second` · **[UNRESOLVED]**", markdown)
        payload = json.loads(LINTER.render(
            findings, leaves, "json", fix_attempted=True, fixed_leaf_ids={"fixed"},
        ))
        self.assertEqual(payload["summary"]["unresolved"], 1)
        self.assertEqual([item["fix_status"] for item in payload["findings"]], ["fixed", "unresolved"])

    def test_report_without_fix_attempt_has_no_unresolved_marker(self):
        finding = LINTER.Finding("warning", "rule", "message", "test.yaml", 1, "scene", "id")
        report = LINTER.render([finding], [], "markdown")
        self.assertNotIn("[UNRESOLVED]", report)

    def test_danbooru_subject_counter_whitelist(self):
        self.assertFalse(LINTER.validate_tag_prompt("2others, family, long table"))
        self.assertFalse(LINTER.validate_tag_prompt("multiple_others, crowd, town square"))
        self.assertTrue(any("invalid Danbooru" in issue for issue in LINTER.validate_tag_prompt("1family, long table")))
        self.assertTrue(any("invalid Danbooru" in issue for issue in LINTER.validate_tag_prompt("7others, group shot")))
        self.assertTrue(any("cannot replace" in issue for issue in LINTER.validate_tag_prompt("solo, portrait")))

    def test_post_prompt_audit_classifies_service_and_representability_failures(self):
        service = LINTER.representability_issues("404 page not found")
        abstract = LINTER.representability_issues("familiar street, impossible machine looming")
        self.assertEqual(service[0][0], "invalid_service_response")
        self.assertIn("nonvisual_modifier", {code for code, _ in abstract})
        self.assertIn("unspecified_impossibility", {code for code, _ in abstract})
        self.assertIn("ambiguous_scale", {code for code, _ in abstract})

    def test_optional_danbooru_vocabulary_warns_only_unknown_underscore_tags(self):
        known = {"1other", "holding_book"}
        detailed = LINTER.validate_tag_prompt_detailed(
            "1other, holding_book, invented_visual_tag, literal visual phrase", danbooru_tags=known
        )
        unknown = [message for code, message in detailed if code == "unknown_canonical_tag"]
        self.assertEqual(unknown, ["unknown underscore-style Danbooru tag: invented_visual_tag"])

    def test_danbooru_vocabulary_loads_counts_and_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tags.csv"
            path.write_text(
                'tag,category,count,alias\nholding_book,0,500,"book holding,holding a book"\n',
                encoding="utf-8",
            )
            vocabulary = LINTER.load_danbooru_tags(path)
        self.assertIn("holding_book", vocabulary)
        self.assertEqual(vocabulary.post_counts["holding_book"], 500)
        self.assertEqual(vocabulary.aliases["holding_a_book"], {"holding_book"})

    def test_canonical_tag_findings_apply_only_to_tags_mode(self):
        vocabulary = LINTER.DanbooruVocabulary(
            {"holding_book", "old_book", "red_hair"},
            {"holding_book": 500, "old_book": 200, "red_hair": 400}, {},
        )
        tags_leaf = LINTER.Leaf(
            "tags", "test.yaml", "gkr", "scene", 0, 1,
            "1other, holding book, vibrant red hair, holding_old_book", (), "tags",
        )
        narrative_leaf = LINTER.Leaf("narrative", "test.yaml", "gkr", "scene", 1, 2, "holding_old_book", (), "narrative")
        findings = LINTER.canonical_tag_findings([tags_leaf, narrative_leaf], vocabulary, 2)
        self.assertEqual({finding.leaf_id for finding in findings}, {"tags"})
        self.assertEqual(
            {finding.rule for finding in findings},
            {"canonical_tag_normalization", "canonical_tag_contained_span", "unknown_canonical_tag"},
        )
        unknown = next(finding for finding in findings if finding.rule == "unknown_canonical_tag")
        self.assertIn("holding_book", unknown.message)
        contained = next(finding for finding in findings if finding.rule == "canonical_tag_contained_span")
        self.assertIn("red_hair", contained.message)
        self.assertIn("unmatched words: vibrant", contained.message)

    def test_contained_tag_search_uses_longest_multiword_nonoverlapping_spans(self):
        vocabulary = LINTER.DanbooruVocabulary(
            {"red", "red_hair", "hair", "blue_eyes"},
            {"red_hair": 500, "blue_eyes": 400}, {},
        )
        spans = LINTER.contained_canonical_spans("vibrant red hair and blue eyes", vocabulary)
        self.assertEqual([span["canonical"] for span in spans], ["red_hair", "blue_eyes"])
        self.assertNotIn("red", {span["canonical"] for span in spans})

    def test_canonical_tag_style_can_render_spaces(self):
        vocabulary = LINTER.DanbooruVocabulary({"red_hair"}, {"red_hair": 500}, {})
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "hair", 0, 1, "vibrant red hair", (), "tags")
        guidance = LINTER.canonical_tag_guidance(leaf, vocabulary, 5, "spaces")
        self.assertEqual(guidance[0]["canonical_ids"], ["red_hair"])
        self.assertEqual(guidance[0]["candidates"], ["red hair"])

    def test_canonical_candidate_recognizes_conservative_plural_variants(self):
        vocabulary = LINTER.DanbooruVocabulary(
            {"holding_book", "blue_eyes", "witch", "gloves"},
            {"holding_book": 500, "blue_eyes": 400, "witch": 300, "gloves": 200}, {},
        )
        self.assertEqual(LINTER.canonical_candidates("holding_books", vocabulary), ["holding_book"])
        self.assertEqual(LINTER.canonical_candidates("blue_eye", vocabulary), ["blue_eyes"])
        self.assertEqual(LINTER.canonical_candidates("witches", vocabulary), ["witch"])
        self.assertEqual(LINTER.canonical_candidates("glove", vocabulary), ["gloves"])

    def test_contained_span_can_recognize_plural_as_singular(self):
        vocabulary = LINTER.DanbooruVocabulary({"red_hair"}, {"red_hair": 500}, {})
        spans = LINTER.contained_canonical_spans("vibrant red hairs", vocabulary)
        self.assertEqual([span["canonical"] for span in spans], ["red_hair"])

    def test_hyphenated_phrase_matches_space_equivalent_canonical_tag(self):
        vocabulary = LINTER.DanbooruVocabulary({"high_contrast"}, {"high_contrast": 500}, {})
        for phrase in ("high-contrast", "high‑contrast", "high–contrast", "high—contrast"):
            leaf = LINTER.Leaf("id", "test.yaml", "gkr", "style", 0, 1, phrase, (), "tags")
            guidance = LINTER.canonical_tag_guidance(leaf, vocabulary, 5, "underscore")
            self.assertEqual(guidance[0]["status"], "separator_normalized_match")
            self.assertEqual(guidance[0]["candidates"], ["high_contrast"])

    def test_literal_hyphenated_canonical_tag_wins_before_separator_normalization(self):
        vocabulary = LINTER.DanbooruVocabulary(
            {"x-ray", "x_ray"}, {"x-ray": 500, "x_ray": 400}, {},
        )
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "style", 0, 1, "x-ray", (), "tags")
        self.assertEqual(LINTER.canonical_tag_guidance(leaf, vocabulary, 5, "underscore"), [])

    def test_shared_head_composition_finds_overlapping_attribute_tags(self):
        vocabulary = LINTER.DanbooruVocabulary(
            {"glowing_eye", "red_eyes"}, {"glowing_eye": 500, "red_eyes": 1000}, {},
        )
        policy = {"enabled": True, "shared_head_attributes": True, "minimum_matches": 2, "allow_inflection_match": True}
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "eyes", 0, 1, "glowing red eye", (), "tags")
        guidance = LINTER.canonical_tag_guidance(leaf, vocabulary, 5, "underscore", policy)
        self.assertEqual(guidance[0]["status"], "shared_head_composition")
        self.assertEqual(guidance[0]["candidates"], ["glowing_eye", "red_eyes"])
        self.assertEqual(guidance[0]["components"][0]["match"], "exact")
        self.assertEqual(guidance[0]["components"][1]["match"], "inflection")
        self.assertEqual(guidance[0]["unmatched_words"], [])

    def test_shared_head_composition_requires_configured_minimum_matches(self):
        vocabulary = LINTER.DanbooruVocabulary({"red_eyes"}, {"red_eyes": 1000}, {})
        policy = {"enabled": True, "minimum_matches": 2, "allow_inflection_match": True}
        self.assertIsNone(LINTER.shared_head_canonical_composition("glowing red eye", vocabulary, policy))

    def test_shared_head_composition_is_a_canonical_finding(self):
        vocabulary = LINTER.DanbooruVocabulary(
            {"glowing_eye", "red_eyes"}, {"glowing_eye": 500, "red_eyes": 1000}, {},
        )
        policy = {"enabled": True, "minimum_matches": 2, "allow_inflection_match": True}
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "eyes", 0, 1, "glowing red eye", (), "tags")
        findings = LINTER.canonical_tag_findings([leaf], vocabulary, 5, "underscore", policy)
        self.assertEqual(findings[0].rule, "canonical_tag_composition")
        self.assertIn("glowing_eye, red_eyes", findings[0].message)

    def test_multiple_quantity_tag_deterministically_subsumes_singular_tag(self):
        vocabulary = LINTER.DanbooruVocabulary({"sword", "multiple_swords"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "person, sword, multiple_swords", (), "tags",
        )
        findings = LINTER.canonical_tag_redundancy_findings([leaf], vocabulary)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "canonical_tag_redundancy")
        rewrites, resolved = LINTER.deterministic_canonical_repairs([leaf], findings)
        self.assertEqual(rewrites[leaf.uid], "person, multiple_swords")
        self.assertEqual(len(resolved), 1)

    def test_relationship_tag_does_not_subsume_object_tag(self):
        vocabulary = LINTER.DanbooruVocabulary({"sword", "holding_sword"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "person, sword, holding_sword", (), "tags",
        )
        self.assertEqual(LINTER.canonical_tag_redundancy_findings([leaf], vocabulary), [])

    def test_exact_redundancy_keeps_more_highly_weighted_canonical_item(self):
        vocabulary = LINTER.DanbooruVocabulary({"sword"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "person, sword, (sword:1.2)", (), "tags",
        )
        findings = LINTER.canonical_tag_redundancy_findings([leaf], vocabulary)
        rewrites, _ = LINTER.deterministic_canonical_repairs([leaf], findings)
        self.assertEqual(rewrites[leaf.uid], "person, (sword:1.2)")

    def test_deterministic_canonical_repairs_combine_safe_items_per_leaf(self):
        vocabulary = LINTER.DanbooruVocabulary(
            {"grey_suit", "high_contrast", "glowing_flower", "blue_flower"},
            aliases={"gray_suit": {"grey_suit"}},
        )
        policy = {"enabled": True, "minimum_matches": 2, "allow_inflection_match": True}
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "gray_suit, high-contrast, glowing blue flower", (), "tags",
        )
        findings = LINTER.canonical_tag_findings([leaf], vocabulary, 5, "underscore", policy)
        rewrites, resolved = LINTER.deterministic_canonical_repairs([leaf], findings)
        self.assertEqual(
            rewrites["id"], "grey_suit, high_contrast, glowing_flower, blue_flower"
        )
        self.assertEqual(len(resolved), 3)

    def test_deterministic_canonical_repairs_preserve_single_candidate_weight(self):
        vocabulary = LINTER.DanbooruVocabulary({"grey_suit"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1, "(grey suit:1.2), standing", (), "tags",
        )
        findings = LINTER.canonical_tag_findings([leaf], vocabulary)
        rewrites, _ = LINTER.deterministic_canonical_repairs([leaf], findings)
        self.assertEqual(rewrites["id"], "(grey_suit:1.2), standing")

        accepted, accepted_resolved, rejected = LINTER.validate_deterministic_canonical_repairs(
            [leaf], rewrites, LINTER.deterministic_canonical_repairs([leaf], findings)[1],
            self.rules, self.tags_rules, vocabulary,
        )
        self.assertEqual(accepted, rewrites)
        self.assertTrue(accepted_resolved)
        self.assertEqual(rejected, {})

    def test_deterministic_canonical_repairs_leave_partial_contained_span_for_review(self):
        vocabulary = LINTER.DanbooruVocabulary({"through_window"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1, "looking through_window", (), "tags",
        )
        findings = LINTER.canonical_tag_findings([leaf], vocabulary)
        rewrites, resolved = LINTER.deterministic_canonical_repairs([leaf], findings)
        self.assertEqual(rewrites, {})
        self.assertEqual(resolved, set())

    def test_canonical_rewrite_must_resolve_targeted_issue(self):
        vocabulary = LINTER.DanbooruVocabulary({"holding_book"}, {"holding_book": 500}, {})
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "scene", 0, 1, "1other, holding_old_book", (), "tags")
        findings = LINTER.canonical_tag_findings([leaf], vocabulary)
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {"id": "1other, holding_older_book"}, findings,
            self.rules, self.tags_rules, vocabulary,
        )
        self.assertFalse(accepted)
        self.assertTrue(any("targeted canonical-tag issue remains" in reason for reason in rejected["id"]))

    def test_unknown_canonical_tag_is_a_nonblocking_audit_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            details = Path(temporary) / "details.md"
            details.write_text(
                "## `image.png`\n\n- Prompt mode: `Tags`\n\n"
                "### Pre-LLM prompt\n\n```text\nsource\n```\n\n"
                "### Post-LLM prompt\n\n```text\n1other, invented_visual_tag\n```\n",
                encoding="utf-8",
            )
            audits = LINTER.audit_post_prompts(details, {"1other"})
        self.assertEqual(audits[0].status, "compliant")
        self.assertTrue(LINTER.prompt_audit_has_warning(audits))

    def test_exact_canonical_item_overrides_llm_visual_test_false_positive(self):
        vocabulary = LINTER.DanbooruVocabulary({"science_fiction"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 10,
            "mechanic, space_station, science_fiction", (), "tags",
        )
        reviewed = {
            "classification": "definite_failure",
            "failed_test": "Visual test",
            "reason": "The tag 'science_fiction' is an interpretive genre label.",
        }
        self.assertTrue(
            LINTER.canonical_visual_test_false_positive(reviewed, leaf, vocabulary)
        )
        self.assertEqual(
            LINTER.canonical_visual_test_matches(reviewed, leaf, vocabulary),
            ["science_fiction"],
        )

        larger_phrase = LINTER.Leaf(
            "id2", "test.yaml", "gkr", "scene", 0, 11,
            "mechanic, science_fiction atmosphere", (), "tags",
        )
        self.assertFalse(
            LINTER.canonical_visual_test_false_positive(reviewed, larger_phrase, vocabulary)
        )
        self.assertFalse(LINTER.canonical_visual_test_false_positive(
            {**reviewed, "failed_test": "Compatibility test"}, leaf, vocabulary,
        ))

    def test_comprehension_check_protects_canonical_term_but_not_missing_relationship(self):
        vocabulary = LINTER.DanbooruVocabulary({"teamwork", "holding", "energy", "protecting", "crowd"})
        teamwork = LINTER.Leaf(
            "team", "test.yaml", "gkr", "scene", 0, 10,
            "2others, jumping, teamwork, dual_wielding", (), "tags",
        )
        intrinsic = {
            "failed_test": "Comprehension test",
            "reason": "The tag 'teamwork' is an abstract relationship that requires visible evidence.",
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(intrinsic, teamwork, vocabulary),
            ("comprehension-test", ["teamwork"]),
        )
        holding = LINTER.Leaf(
            "hold", "test.yaml", "gkr", "scene", 0, 11,
            "1man, holding, concrete debris", (), "tags",
        )
        relational = {
            "failed_test": "Comprehension test",
            "reason": "The tag 'holding' is incomplete and does not specify what is being held.",
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(relational, holding, vocabulary),
            ("", []),
        )
        energy = LINTER.Leaf(
            "energy", "test.yaml", "gkr", "scene", 0, 12,
            "energy, (bridge:1.1), construction, superhero_costume", (), "tags",
        )
        abstract_options = {
            "failed_test": "Comprehension",
            "reason": (
                "The term 'energy' is too abstract. It doesn't specify if the character is "
                "emitting energy, absorbing it, or if the bridge is made of energy."
            ),
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(abstract_options, energy, vocabulary),
            ("comprehension-test", ["energy"]),
        )
        protecting = LINTER.Leaf(
            "protecting", "test.yaml", "gkr", "scene", 0, 13,
            "protecting crowd", (), "tags",
        )
        supplied_target = {
            "failed_test": "Comprehension",
            "reason": (
                "The term 'protecting' is an abstract motive/role. It does not specify the "
                "visible action or spatial relationship between the hero and the crowd."
            ),
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(supplied_target, protecting, vocabulary),
            ("comprehension-test", ["protecting"]),
        )

    def test_single_moment_check_protects_atomic_canonical_transition_tag(self):
        vocabulary = LINTER.DanbooruVocabulary({"transformation", "before_and_after", "jumping", "time_stop"})
        leaf = LINTER.Leaf(
            "transform", "test.yaml", "gkr", "scene", 0, 10,
            "shadow, transformation, monster, urban", (), "tags",
        )
        reviewed = {
            "failed_test": "Single-moment test",
            "reason": "The tag 'transformation' describes a transition across time, not a stable visible state.",
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(reviewed, leaf, vocabulary),
            ("single-moment-test", ["transformation"]),
        )
        multi = LINTER.Leaf(
            "multi", "test.yaml", "gkr", "scene", 0, 11,
            "1woman, before_and_after, transformation", (), "tags",
        )
        multi_review = {
            "failed_test": "Single-moment test",
            "reason": "The tag 'before_and_after' requires multiple states across time.",
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(multi_review, multi, vocabulary),
            ("", []),
        )
        contextual = LINTER.Leaf(
            "fleet", "test.yaml", "gkr", "scene", 0, 12,
            "fleet, battleship, space, jumping", (), "tags",
        )
        contextual_review = {
            "failed_test": "Single-moment test",
            "reason": "The tag 'jumping' in the context of a space fleet implies a transition across time.",
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(contextual_review, contextual, vocabulary),
            ("", []),
        )
        stopped_time = LINTER.Leaf(
            "time", "test.yaml", "gkr", "scene", 0, 13,
            "time_stop, (water_drop:1.2), frozen, rain, raindrops, time_stop", (), "tags",
        )
        visibility_review = {
            "failed_test": "Single-moment test",
            "reason": (
                "The tag 'time_stop' is an abstract concept/state of time rather than a visible "
                "mark. While frozen rain is visible, 'time_stop' itself is not a visual element."
            ),
        }
        self.assertEqual(
            LINTER.canonical_llm_false_positive_matches(visibility_review, stopped_time, vocabulary),
            ("single-moment-test", ["time_stop"]),
        )

    def test_llm_replacement_candidates_render_as_alternatives_not_leaf_rewrite(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 10,
            "tower, monolithic, extreme low angle", (), "tags",
        )
        finding = LINTER.Finding(
            "error", "Visual test", "Monolithic is interpretive.",
            leaf.file, leaf.line, leaf.category, leaf.uid,
            alternatives=("windowless stone shaft", "sheer concrete facade"), source="llm",
        )
        markdown = LINTER.render([finding], [leaf], "markdown", fix_attempted=True)
        self.assertIn("Potential replacements — LLM generated", markdown)
        self.assertIn("**Source leaf:**\n\n```text\ntower, monolithic, extreme low angle\n```", markdown)
        text_report = LINTER.render([finding], [leaf], "text", fix_attempted=True)
        self.assertIn("Source leaf:\ntower, monolithic, extreme low angle", text_report)
        self.assertIn("- `windowless stone shaft`", markdown)
        self.assertNotIn("```diff", markdown)

    def test_canonical_evidence_is_highlighted_for_human_review(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 10,
            "looking through_window", (), "tags",
        )
        finding = LINTER.Finding(
            "warning", "canonical_tag_contained_span",
            "item contains canonical Danbooru span(s): through_window; unmatched words: looking",
            leaf.file, leaf.line, leaf.category, leaf.uid,
            evidence=json.dumps({
                "input": "looking through_window",
                "canonical_ids": ["through_window"],
                "unmatched_words": ["looking"],
            }),
        )
        markdown = LINTER.render([finding], [leaf], "markdown")
        self.assertIn("**Canonical analysis:**", markdown)
        self.assertIn("Canonical tags extracted from source: **`through_window`**", markdown)
        self.assertIn("Unmatched source words: **`looking`**", markdown)
        text_report = LINTER.render([finding], [leaf], "text")
        self.assertIn("Canonical tags extracted from source: through_window", text_report)
        self.assertIn("Unmatched source words: looking", text_report)

    def test_markdown_highlights_quoted_terms_found_in_source_leaf(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 10,
            "astronaut, gravity boots, moon", (), "tags",
        )
        finding = LINTER.Finding(
            "warning", "Visual test",
            "The term 'gravity boots' is not a verified canonical tag and is conceptual.",
            leaf.file, leaf.line, leaf.category, leaf.uid, source="llm",
        )
        markdown = LINTER.render([finding], [leaf], "markdown")
        self.assertIn("The term **`gravity boots`** is not a verified canonical tag", markdown)

        unrelated = LINTER.Finding(
            "warning", "Visual test", "The 'Visual test' policy rejected the term.",
            leaf.file, leaf.line, leaf.category, leaf.uid, source="llm",
        )
        unrelated_markdown = LINTER.render([unrelated], [leaf], "markdown")
        self.assertIn("The 'Visual test' policy", unrelated_markdown)

    def test_accepted_rewrite_is_labeled_as_applied_in_fixed_report(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 10,
            "glowing blue_flower", (), "tags",
        )
        finding = LINTER.Finding(
            "warning", "canonical_tag_composition", "decompose tags",
            leaf.file, leaf.line, leaf.category, leaf.uid,
            suggestion="glowing_flower, blue_flower",
        )
        applied = LINTER.render(
            [finding], [leaf], "markdown", fix_attempted=True, fixed_leaf_ids={leaf.uid},
        )
        self.assertIn("Applied fix — LLM generated and written to fixed output", applied)
        self.assertNotIn("Potential fix — LLM generated", applied)
        potential = LINTER.render([finding], [leaf], "markdown", fix_attempted=False)
        self.assertIn("Potential fix — LLM generated", potential)

    def test_prompt_audit_summary_counts_unique_pairs(self):
        audits = [
            LINTER.PromptAudit("one.png", "Tags", "noncompliant", ["bad"], "1family", ["subject_count_failure"], "family"),
            LINTER.PromptAudit("two.png", "Tags", "noncompliant", ["bad"], "1family", ["subject_count_failure"], "family"),
        ]
        summary = json.loads(LINTER.render_prompt_audit(audits, "json"))["summary"]
        self.assertEqual(summary["images"]["total"], 2)
        self.assertEqual(summary["unique_prompt_pairs"]["total"], 1)
        self.assertEqual(summary["issues_by_unique_pair"]["subject_count_failure"], 1)

    def test_llm_response_cache_avoids_second_request(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": '[{"id":"one"}]'}}]}).encode()

        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(
                no_llm_cache=False, llm_cache_dir=Path(temporary),
                llm_cache_max_age_minutes=None, timeout=10, verbose=False,
            )
            kwargs = dict(
                args=args, endpoint="http://example.test/v1/chat/completions", api_key="secret",
                model="test", instruction="review", items=[{"id": "one", "text": "hero"}],
                call_name="LLM batch", batch_number=1,
                batch_total=1, offset=0, trace_path=None, response_event="response",
            )
            with patch.object(LINTER.urllib.request, "urlopen", return_value=Response()) as opened:
                first = LINTER.llm_json_request(**kwargs)
            with patch.object(LINTER.urllib.request, "urlopen", side_effect=AssertionError("network used")):
                second = LINTER.llm_json_request(**kwargs)
            self.assertEqual(first, second)
            self.assertEqual(opened.call_count, 1)

    def test_concept_response_repeated_ids_are_coalesced_before_caching(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                content = json.dumps([
                    {"id": "subject", "concepts": {"summary": "first", "search_queries": ["one"]}},
                    {"id": "subject", "concepts": {"summary": "second", "search_queries": ["two"]}},
                ])
                return json.dumps({"choices": [{"message": {"content": content}}]}).encode()

        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(
                no_llm_cache=False, llm_cache_dir=Path(temporary),
                llm_cache_max_age_minutes=None, timeout=10, verbose=False,
            )
            kwargs = dict(
                args=args, endpoint="http://example.test/v1/chat/completions", api_key="secret",
                model="test", instruction="concepts", items=[{"id": "subject", "count": 2}],
                call_name="concept generation", batch_number=1, batch_total=1, offset=0,
                trace_path=None, response_event="response",
            )
            with patch.object(LINTER.urllib.request, "urlopen", return_value=Response()):
                first = LINTER.llm_json_request(**kwargs)
            with patch.object(LINTER.urllib.request, "urlopen", side_effect=AssertionError("network used")):
                second = LINTER.llm_json_request(**kwargs)
            self.assertEqual(first, second)
            self.assertEqual(
                [concept["summary"] for concept in first[0]["concepts"]], ["first", "second"]
            )

    def test_llm_http_error_includes_response_body(self):
        args = SimpleNamespace(
            no_llm_cache=True, llm_cache_dir=None,
            llm_cache_max_age_minutes=None, timeout=10, verbose=False,
        )
        error = urllib.error.HTTPError(
            "http://example.test", 500, "Internal Server Error", {}, io.BytesIO(b'{"error":"out of memory"}')
        )
        with patch.object(LINTER.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "out of memory"):
                LINTER.llm_json_request(
                    args=args, endpoint="http://example.test", api_key="secret",
                    model="test", instruction="review", items=[{"id": "one", "text": "hero"}],
                    call_name="LLM batch", batch_number=3, batch_total=58, offset=20,
                    trace_path=None, response_event="response",
                )

    def test_llm_cache_reuses_unchanged_items_across_changed_batch(self):
        class Response:
            status = 200
            def __init__(self, request):
                body = json.loads(request.data)
                items = json.loads(body["messages"][1]["content"])
                self.payload = {"choices": [{"message": {"content": json.dumps([
                    {"id": item["id"], "classification": "pass"} for item in items
                ])}}]}
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return json.dumps(self.payload).encode()

        requests = []
        def respond(request, timeout):
            requests.append(json.loads(json.loads(request.data)["messages"][1]["content"]))
            return Response(request)

        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(
                no_llm_cache=False, llm_cache_dir=Path(temporary),
                llm_cache_max_age_minutes=None, timeout=10, verbose=False,
            )
            common = dict(
                args=args, endpoint="http://example.test", api_key="secret", model="test",
                instruction="review", call_name="LLM batch", batch_number=1,
                batch_total=1, offset=0, trace_path=None, response_event="response",
            )
            with patch.object(LINTER.urllib.request, "urlopen", side_effect=respond):
                LINTER.llm_json_request(items=[{"id": "a", "text": "hero"}, {"id": "b", "text": "castle"}], **common)
                result = LINTER.llm_json_request(items=[{"id": "new-a", "text": "hero"}, {"id": "b", "text": "forest"}], **common)
            self.assertEqual(len(requests), 2)
            self.assertEqual([item["id"] for item in requests[1]], ["b"])
            self.assertEqual([item["id"] for item in result], ["new-a", "b"])

    def test_llm_cache_max_age_deletes_expired_entry(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": '[{"id":"one"}]'}}]}).encode()

        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            args = SimpleNamespace(
                no_llm_cache=False, llm_cache_dir=cache_dir,
                llm_cache_max_age_minutes=None, timeout=10, verbose=False,
            )
            kwargs = dict(
                args=args, endpoint="http://example.test", api_key="secret", model="test",
                instruction="review", items=[{"id": "one", "text": "hero"}],
                call_name="LLM batch", batch_number=1, batch_total=1, offset=0,
                trace_path=None, response_event="response",
            )
            with patch.object(LINTER.urllib.request, "urlopen", return_value=Response()):
                LINTER.llm_json_request(**kwargs)
            cache_file = next(cache_dir.glob("*.json"))
            cached = json.loads(cache_file.read_text())
            cached["created"] = 0
            cache_file.write_text(json.dumps(cached))
            args.llm_cache_max_age_minutes = 5
            with patch.object(LINTER.urllib.request, "urlopen", return_value=Response()) as opened:
                LINTER.llm_json_request(**kwargs)
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(len(list(cache_dir.glob("*.json"))), 1)

    def test_trace_event_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            LINTER.trace_event(path, {"event": "request", "items": [{"id": "abc"}]})
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["event"], "request")
            self.assertNotIn("api_key", record)

    def test_text_report_emphasizes_llm_and_fix(self):
        finding = LINTER.Finding(
            "warning", "visual_test", "abstract idea", "example.yaml", 12,
            "scene", "abc", suggestion="person points to a marked map", source="llm"
        )
        report = LINTER.render([finding], [], "text")
        self.assertIn("[LLM]", report)
        self.assertIn("Potential fix [LLM-generated]:", report)
        self.assertIn("─", report)

    def test_markdown_report_emphasizes_llm(self):
        finding = LINTER.Finding("warning", "visual_test", "abstract idea", "example.yaml", source="llm")
        report = LINTER.render([finding], [], "markdown")
        self.assertIn("**LLM**", report)

    def test_router_only_leaf_has_no_literal_content(self):
        leaves, _, _ = self.inventory(
            'gkr_test:\n  random:\n    - "__gkr_test/scene__"\n  scene:\n    - mechanic tightening a clamp\n'
        )
        by_category = {leaf.category: leaf for leaf in leaves}
        self.assertFalse(LINTER.has_literal_content(by_category["random"]))
        self.assertTrue(LINTER.has_literal_content(by_category["scene"]))

    def test_report_contains_original_fix_diff(self):
        leaves, _, _ = self.inventory(
            "gkr_test:\n  scene:\n    - carefully planned announcement\n"
        )
        finding = LINTER.Finding(
            "warning", "visual_test", "abstract", leaves[0].file, leaves[0].line,
            leaves[0].category, leaves[0].uid, suggestion="student reading announcement sheet", source="llm"
        )
        report = LINTER.render([finding], leaves, "markdown")
        self.assertIn("```diff", report)
        self.assertIn("- carefully planned announcement", report)
        self.assertIn("+ student reading announcement sheet", report)

    def test_text_color_can_be_forced(self):
        finding = LINTER.Finding("warning", "visual_test", "abstract", "example.yaml")
        report = LINTER.render([finding], [], "text", color=True)
        self.assertIn("\033[33;1m", report)

    def test_write_fixed_file_creates_copy_and_preserves_original(self):
        leaves, _, _ = self.inventory(
            "gkr_test:\n  scene:\n    - carefully planned announcement\n"
        )
        source = Path(leaves[0].file)
        destination = source.with_name("gkr-test-fixed.yaml")
        original = source.read_text(encoding="utf-8")
        applied = LINTER.write_fixed_file(
            source, destination, leaves, {leaves[0].uid: "student reading announcement sheet"}
        )
        self.assertEqual(applied, 1)
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        self.assertIn("student reading announcement sheet", destination.read_text(encoding="utf-8"))

    def test_write_fixed_file_refuses_original_path(self):
        leaves, _, _ = self.inventory(
            "gkr_test:\n  scene:\n    - carefully planned announcement\n"
        )
        source = Path(leaves[0].file)
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            LINTER.write_fixed_file(source, source, leaves, {leaves[0].uid: "replacement"})

    def test_validation_rejects_changed_wildcard_reference(self):
        leaves, _, findings = self.inventory(
            '# MODE: tags\ngkr_test:\n  camera:\n    - close-up\n  combo:\n    - "subject, __gkr_test/camera__"\n'
        )
        leaf = next(item for item in leaves if item.category == "combo")
        accepted, rejected = LINTER.validate_suggestions(
            leaves, {leaf.uid: "subject, full body shot"}, findings,
            self.rules, self.tags_rules,
        )
        self.assertNotIn(leaf.uid, accepted)
        self.assertIn("wildcard references or their weights changed", rejected[leaf.uid])

    def test_validation_rejects_changed_alternative(self):
        leaves, _, findings = self.inventory(
            "# MODE: tags\ngkr_test:\n  accessory:\n"
            "    - high-tech visor or goggles, glowing lens\n"
        )
        accepted, rejected = LINTER.validate_suggestions(
            leaves, {leaves[0].uid: "high-tech visor, goggles, glowing lens"}, findings,
            self.rules, self.tags_rules,
        )
        self.assertNotIn(leaves[0].uid, accepted)
        self.assertIn("alternative-choice markers changed", rejected[leaves[0].uid])

    def test_canonical_literal_repair_rejects_compound_atomization(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "archetype", "leaf-1",
            evidence=json.dumps({
                "input": "bone armor",
                "candidates": ["bone", "armor", "bone_weapon"],
            }),
        )
        issues = LINTER.canonical_literal_atomization_issues(
            "1other, fur_cloak, bone, armor, tribal_paint", [finding]
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("atomizes 'bone armor'", issues[0])

    def test_canonical_literal_repair_rejects_partial_phrase_decomposition(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "vehicle", "leaf-1",
            evidence=json.dumps({
                "input": "shock absorber leg",
                "candidates": ["leg_armor", "peg_leg"],
            }),
        )
        issues = LINTER.canonical_literal_atomization_issues(
            "person, shock absorber, leg", [finding]
        )
        self.assertEqual(len(issues), 1)

    def test_canonical_literal_repair_rejects_dropped_modifier_or_material(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "vehicle", "leaf-1",
            evidence=json.dumps({"input": "photovoltaic cells", "candidates": ["ips_cells"]}),
        )
        issues = LINTER.canonical_literal_fact_loss_issues("vehicle, ips_cells", [finding])
        self.assertEqual(len(issues), 1)
        self.assertIn("photovoltaic", issues[0])

    def test_canonical_literal_repair_preserves_inflected_source_word(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "armor", "leaf-1",
            evidence=json.dumps({"input": "plated armor", "candidates": ["plate_armor"]}),
        )
        self.assertEqual(
            LINTER.canonical_literal_fact_loss_issues("person, plate_armor", [finding]), []
        )

    def test_canonical_literal_repair_accepts_compound_candidate(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "archetype", "leaf-1",
            evidence=json.dumps({
                "input": "oversized hoodie",
                "candidates": ["oversized_clothes", "hoodie"],
                "meaning_preserving_candidates": ["oversized_clothes"],
            }),
        )
        issues = LINTER.canonical_literal_atomization_issues(
            "1other, oversized_clothes, hoodie", [finding]
        )
        self.assertEqual(issues, [])

    def test_canonical_literal_repair_does_not_reuse_unrelated_existing_candidate(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "archetype", "leaf-1",
            evidence=json.dumps({
                "input": "dark velvet suit",
                "meaning_preserving_candidates": ["business_suit", "black_suit", "pant_suit"],
            }),
        )
        issues = LINTER.canonical_literal_atomization_issues(
            "1other, black_suit, dark, velvet, suit",
            [finding],
            "1other, black_suit, dark velvet suit",
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("dark velvet suit", issues[0])

    def test_canonical_literal_repair_rejects_partial_head_candidate(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "archetype", "leaf-1",
            evidence=json.dumps({
                "input": "starry nebula skin",
                "meaning_preserving_candidates": ["starry_skin"],
            }),
        )
        issues = LINTER.canonical_literal_atomization_issues(
            "1other, starry_skin, nebula", [finding], "1other, starry nebula skin"
        )
        self.assertEqual(len(issues), 1)

    def test_canonical_literal_repair_rejects_relation_recast_as_location(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "effect", "leaf-1",
            evidence=json.dumps({
                "input": "vines from ground",
                "meaning_preserving_candidates": ["on_ground"],
            }),
        )
        issues = LINTER.canonical_literal_atomization_issues(
            "vines, on_ground", [finding], "vines from ground"
        )
        self.assertEqual(len(issues), 1)

    def test_canonical_literal_repair_rejects_plural_compound_atomization(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "archetype", "leaf-1",
            evidence=json.dumps({"input": "ancient bronze bracers", "candidates": []}),
        )
        issues = LINTER.canonical_literal_atomization_issues(
            "1other, ancient, bronze, bracer", [finding]
        )
        self.assertEqual(len(issues), 1)

    def test_canonical_literal_repair_rejects_plural_loss(self):
        finding = LINTER.Finding(
            "warning", "canonical_literal_concept", "review compound", "test.yaml", 1,
            "archetype", "leaf-1",
            evidence=json.dumps({"input": "white flowing robes", "candidates": []}),
        )
        issues = LINTER.canonical_literal_fact_loss_issues("1other, white, flowing, robe", [finding])
        self.assertTrue(any("robes" in issue for issue in issues))

    def test_validation_rejects_changed_explicit_subject_count(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "5others, student, classroom", (), "tags",
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "5students, classroom"}, [], self.rules, self.tags_rules,
        )
        self.assertEqual(accepted, {})
        self.assertIn("explicit subject-count facts changed", rejected[leaf.uid])

    def test_validation_rejects_unsupported_added_concept(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "(debris:1.2), floating, utility_belt", (), "tags",
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "superhero, (debris:1.2), floating, utility_belt"}, [],
            self.rules, self.tags_rules,
        )
        self.assertEqual(accepted, {})
        self.assertTrue(any("unsupported source concept 'superhero'" in reason for reason in rejected[leaf.uid]))

    def test_validation_rejects_general_descriptive_phrase_split(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "effect", 0, 1,
            "golden energy aura, light_rays", (), "tags",
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "gold, energy, aura, light_rays"}, [],
            self.rules, self.tags_rules,
        )
        self.assertEqual(accepted, {})
        self.assertTrue(any("splits cohesive source phrase" in reason for reason in rejected[leaf.uid]))

        for original, rewrite in (
            ("dusty street, cowboy", "dust, street, cowboy"),
            ("rainy street, cyberpunk", "rain, street, cyberpunk"),
            ("ink splash, dancer", "ink, splashing, dancer"),
            ("steaming tea, table", "steam, tea, table"),
            ("plumed helmet, warrior", "plume, helmet, warrior"),
        ):
            self.assertTrue(
                LINTER.general_phrase_cohesion_issues(original, rewrite),
                f"expected cohesion rejection for {original!r} -> {rewrite!r}",
            )

    def test_validation_rejects_unknown_underscore_neighbor_substitution(self):
        vocabulary = LINTER.DanbooruVocabulary({"exhaust_pipe", "steam"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "person, factory, steam_pipe", (), "tags",
        )
        finding = LINTER.Finding(
            "warning", "unknown_canonical_tag", "unknown", "test.yaml", 1,
            "scene", leaf.uid,
            evidence=json.dumps({
                "input": "steam_pipe", "status": "unknown", "candidates": ["exhaust_pipe"],
            }),
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "person, factory, exhaust_pipe"}, [finding],
            self.rules, self.tags_rules, vocabulary,
        )
        self.assertEqual(accepted, {})
        self.assertTrue(any("protected source tag 'steam_pipe'" in reason for reason in rejected[leaf.uid]))

    def test_validation_rejects_loss_of_unknown_underscore_modifier(self):
        vocabulary = LINTER.DanbooruVocabulary({"sword"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "weapon_on_floor, pointed_sword", (), "tags",
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "weapon_on_floor, sword"}, [],
            self.rules, self.tags_rules, vocabulary,
        )
        self.assertEqual(accepted, {})
        self.assertTrue(any("protected source tag 'pointed_sword'" in reason for reason in rejected[leaf.uid]))

    def test_validation_rejects_case_only_rewrite(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "1man, sketchbook, Cairo street", (), "tags",
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "1man, sketchbook, cairo street"}, [],
            self.rules, self.tags_rules,
        )
        self.assertEqual(accepted, {})
        self.assertIn(
            "repair changes only letter casing and does not resolve a representational issue",
            rejected[leaf.uid],
        )

    def test_validation_rejects_bare_standalone_descriptor_deletion(self):
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "scene", 0, 1,
            "2people, long white table, stark, modernist", (), "tags",
        )
        finding = LINTER.Finding(
            "error", "Visual test", "The term 'stark' is interpretive.",
            leaf.file, leaf.line, leaf.category, leaf.uid, source="llm",
        )
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "2people, long white table, modernist"}, [finding],
            self.rules, self.tags_rules,
        )
        self.assertEqual(accepted, {})
        self.assertTrue(any("standalone source descriptor 'stark'" in reason for reason in rejected[leaf.uid]))

    def test_fix_manifest_separates_accepted_repair_from_rejected_enhancement(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "manifest.json"
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "scene", 0, 7, "sword, sword", (), "tags")
        LINTER.write_fix_manifest(
            path, [leaf], {leaf.uid: "invented rewrite"}, {leaf.uid: "sword"},
            {leaf.uid: ["semantic verification failed"]}, {leaf.uid: "rationale"}, [],
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"], {
            "proposed": 1, "accepted": 1, "rejected": 0,
            "accepted_with_rejected_enhancement": 1,
        })
        record = payload["fixes"][0]
        self.assertEqual(record["suggested_rewrite"], "sword")
        self.assertEqual(record["rejection_reasons"], [])
        self.assertEqual(record["rejected_enhancement"], "invented rewrite")

    def test_unresolved_candidates_rank_safer_rejected_attempt(self):
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "scene", 0, 7, "dark velvet suit", (), "tags")
        history = {
            leaf.uid: [
                {
                    "rewrite": "superhero, dark, velvet, suit", "stage": "initial suggestion",
                    "rationale": "", "rejection_reasons": [
                        "repair adds unsupported source concept 'superhero'",
                        "repair splits cohesive source phrase 'dark velvet suit' into separate items",
                    ],
                },
                {
                    "rewrite": "black_suit, dark velvet suit", "stage": "correction retry 1",
                    "rationale": "", "rejection_reasons": ["semantic verification: candidate is redundant"],
                },
            ],
        }
        selected = LINTER.rank_unresolved_candidates(
            [leaf], {}, history, {leaf.uid},
        )
        self.assertEqual(selected[leaf.uid]["rewrite"], "black_suit, dark velvet suit")
        self.assertEqual(len(selected[leaf.uid]["alternatives"]), 1)

    def test_unresolved_yaml_layers_candidate_over_fixed_baseline(self):
        leaves, _, _ = self.inventory(
            "gkr_test:\n  scene:\n    - first source leaf\n    - second source leaf\n"
        )
        source = Path(leaves[0].file)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "review.yaml"
        applied = LINTER.write_fixed_file(
            source, destination, leaves,
            {leaves[0].uid: "accepted fixed leaf", leaves[1].uid: "best rejected candidate"},
            "# REVIEW_CANDIDATES: true\n",
        )
        content = destination.read_text(encoding="utf-8")
        self.assertEqual(applied, 2)
        self.assertTrue(content.startswith("# REVIEW_CANDIDATES: true\n"))
        self.assertIn("accepted fixed leaf", content)
        self.assertIn("best rejected candidate", content)

    def test_unresolved_report_groups_findings_by_leaf(self):
        leaf = LINTER.Leaf("id", "test.yaml", "gkr", "scene", 0, 7, "source", (), "tags")
        findings = [
            LINTER.Finding("warning", "rule_one", "first issue", leaf.file, leaf.line, leaf.category, leaf.uid),
            LINTER.Finding("error", "rule_two", "second issue", leaf.file, leaf.line, leaf.category, leaf.uid),
        ]
        candidate = {
            leaf.uid: {
                "baseline": "fixed baseline", "rewrite": "rejected candidate",
                "stage": "correction retry 1", "rejection_reasons": ["lost detail"],
                "alternatives": [],
            },
        }
        report = LINTER.render_unresolved_report([leaf], findings, candidate, {leaf.uid})
        self.assertEqual(report.count("## `scene` · line 7"), 1)
        self.assertIn("fixed baseline", report)
        self.assertIn("rejected candidate", report)
        self.assertIn("`rule_one`: first issue", report)
        self.assertIn("`rule_two`: second issue", report)

    def test_valid_partial_repair_may_retain_ambiguous_literal_phrase(self):
        vocabulary = LINTER.DanbooruVocabulary({"armor", "plate_armor", "grey_suit"})
        leaf = LINTER.Leaf(
            "id", "test.yaml", "gkr", "archetype", 0, 1,
            "heavy plated armor, grey suit", (), "tags",
        )

        class FakeRetriever:
            embedding_client = None

            def candidates(self, value, limit):
                return ["plate_armor"] if value == "heavy plated armor" else []

        retriever = FakeRetriever()
        findings = LINTER.canonical_tag_findings([leaf], vocabulary)
        findings += LINTER.canonical_literal_concept_findings([leaf], vocabulary, retriever)
        accepted, rejected = LINTER.validate_suggestions(
            [leaf], {leaf.uid: "heavy plated armor, grey_suit"}, findings,
            self.rules, self.tags_rules, vocabulary, canonical_retriever=retriever,
        )
        self.assertEqual(accepted, {leaf.uid: "heavy plated armor, grey_suit"})
        self.assertEqual(rejected, {})

    def test_markdown_report_explains_rejected_leaf_repair_once(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  archetype:\n    - bone armor, tribal paint\n"
        )
        leaf = leaves[0]
        findings = [
            LINTER.Finding("warning", "canonical_literal_concept", phrase, leaf.file, leaf.line,
                           leaf.category, leaf.uid)
            for phrase in ("bone armor", "tribal paint")
        ]
        markdown = LINTER.render(
            findings, leaves, "markdown", fix_attempted=True,
            proposed_suggestions={leaf.uid: "bone, armor, tribal, paint"},
            rejected_suggestions={leaf.uid: ["compound relationship lost"]},
        )
        self.assertIn("2 findings across 1 leaves", markdown)
        self.assertEqual(markdown.count("**Rejected automatic repair:**"), 1)
        self.assertIn("compound relationship lost", markdown)

    def test_validation_rejects_new_dangling_relation(self):
        leaves, _, findings = self.inventory(
            "# MODE: tags\ngkr_test:\n  subject:\n"
            "    - obsolete robot, handwritten notes clutched in claw hand\n"
        )
        accepted, rejected = LINTER.validate_suggestions(
            leaves, {leaves[0].uid: "obsolete robot, handwritten notes, clutched in claw hand"}, findings,
            self.rules, self.tags_rules,
        )
        self.assertNotIn(leaves[0].uid, accepted)
        self.assertTrue(any("tags_dangling_relation" in reason for reason in rejected[leaves[0].uid]))

    def test_validation_rejects_sequential_paraphrase(self):
        leaves, _, initial = self.inventory(
            "# MODE: tags\ngkr_test:\n  layout:\n    - double-page spread\n"
        )
        findings = initial + LINTER.tags_mode_findings(leaves, self.tags_rules)
        accepted, rejected = LINTER.validate_suggestions(
            leaves, {leaves[0].uid: "wide two-page shot"}, findings,
            self.rules, self.tags_rules,
        )
        self.assertNotIn(leaves[0].uid, accepted)
        self.assertTrue(any("targeted error remains" in reason for reason in rejected[leaves[0].uid]))


if __name__ == "__main__":
    unittest.main()
