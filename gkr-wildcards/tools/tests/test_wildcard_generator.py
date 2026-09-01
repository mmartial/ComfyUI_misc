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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_module("danbooru_index", TOOLS / "danbooru_index.py")
load_module("wildcard_linter", TOOLS / "wildcard_linter.py")
GENERATOR = load_module("wildcard_generator", TOOLS / "wildcard_generator.py")


def args(**overrides):
    values = {"max_added_categories": 30, "max_category_depth": 6}
    values.update(overrides)
    return SimpleNamespace(**values)


class WildcardGeneratorTests(unittest.TestCase):
    def test_generation_prompts_require_functional_query_expansion_and_compound_decomposition(self):
        concept = GENERATOR.concept_instruction("policy")
        generation = GENERATOR.generation_instruction(
            "policy", sys.modules["wildcard_linter"].DanbooruVocabulary({"greenhouse", "glass", "dome"}),
        )
        self.assertIn("common visible functional terms", concept)
        self.assertIn("biodome", concept)
        self.assertIn("combination of candidates", generation)
        self.assertIn("greenhouse, glass, dome", generation)
        self.assertIn("contextually plausible combination", generation)
        self.assertIn("at least half of its content", generation)

    def test_colored_verbose_log_highlights_corrective_retry(self):
        stream = io.StringIO()
        with patch.object(GENERATOR.sys, "stderr", stream):
            GENERATOR.log(
                SimpleNamespace(verbose=True, color="always"),
                "category test; corrective retry 1/3",
            )
        output = stream.getvalue()
        self.assertIn("\033[36;1m[wildcard-generator]\033[0m", output)
        self.assertIn("\033[33;1mcorrective retry 1/3\033[0m", output)

    def test_skeleton_header_parses_do_not_use_tags(self):
        skeleton = self.skeleton(
            "# MODE: tags\n"
            "# DO_NOT_USE_TAGS: [comic, western_comics_(style)]\n"
            "gkr_test:\n  subject: []\n"
        )
        self.assertEqual(skeleton.excluded_tags, {"comic", "western_comics_(style)"})

    def test_skeleton_header_parses_canonical_policy(self):
        skeleton = self.skeleton(
            "# MODE: tags\n# CANONICAL_POLICY: prefer\ngkr_test:\n  subject: []\n"
        )
        self.assertEqual(skeleton.canonical_policy, "prefer")
        defaulted = self.skeleton("# MODE: tags\ngkr_test:\n  subject: []\n")
        self.assertEqual(defaulted.canonical_policy, "flexible")
        with self.assertRaisesRegex(ValueError, "CANONICAL_POLICY must be one of"):
            self.skeleton(
                "# MODE: tags\n# CANONICAL_POLICY: sometimes\ngkr_test:\n  subject: []\n"
            )

    def test_prefer_preserves_known_modifier_object_composition(self):
        index_module = sys.modules["danbooru_index"]

        class FakeIndex:
            def hybrid_search(self, query, vector, limit):
                mapping = {
                    "velvet seat": [
                        index_module.SearchResult("seat", 100, 0.91, "hybrid"),
                        index_module.SearchResult("velvet", 50, 0.84, "hybrid"),
                    ],
                }
                return mapping.get(query, [])

        response = {
            "leaves": ["motor_vehicle, velvet seat, city_lights"],
            "provenance": [{"canonical_tags": ["motor_vehicle", "city_lights"], "literal_fallbacks": []}],
        }
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary(
            {"motor_vehicle", "seat", "velvet", "city_lights"}
        )
        guidance = GENERATOR.retrieve_literal_fallback_guidance(
            response, vocabulary, FakeIndex(), None, "", 5,
        )
        self.assertEqual(guidance, [])

    def test_prefer_retrieves_whole_phrase_and_component_tag_palette(self):
        index_module = sys.modules["danbooru_index"]

        class FakeIndex:
            def hybrid_search(self, query, vector, limit):
                mapping = {
                    "crumbling": [index_module.SearchResult("ruins", 100, 0.9, "hybrid")],
                    "stone": [index_module.SearchResult("stone_wall", 100, 0.9, "hybrid")],
                    "cloister": [index_module.SearchResult("arch", 100, 0.8, "semantic")],
                }
                return mapping.get(query, [])

        response = {
            "leaves": ["crumbling stone cloister"],
            "provenance": [{"canonical_tags": [], "literal_fallbacks": []}],
        }
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary(
            {"ruins", "stone_wall", "arch"}
        )
        guidance = GENERATOR.retrieve_literal_fallback_guidance(
            response, vocabulary, FakeIndex(), None, "", 5,
        )
        self.assertEqual(guidance[0]["candidate_tag_set_palette"], ["ruins", "stone_wall", "arch"])
        self.assertEqual(
            [item["text"] for item in guidance[0]["component_guidance"]],
            ["crumbling", "stone", "cloister"],
        )

    def test_prefer_requires_justified_literal_provenance_and_strict_forbids_literals(self):
        allowed = {"motor_vehicle", "city_lights"}
        leaf = "motor_vehicle, velvet seat, city_lights"
        missing = {
            "leaves": [leaf],
            "provenance": [{"canonical_tags": list(allowed), "literal_fallbacks": []}],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError, "lacks justified provenance for: velvet seat"
        ):
            GENERATOR.validate_category_response(
                "vehicle", missing, 1, "gkr_test", [], allowed, canonical_policy="prefer",
            )
        justified = {
            "leaves": [leaf],
            "provenance": [{
                "canonical_tags": list(allowed),
                "literal_fallbacks": [{
                    "text": "velvet seat",
                    "reason": "The candidates lose the upholstered material relationship.",
                    "candidates_considered": ["seat", "velvet"],
                }],
            }],
        }
        leaves, _, _ = GENERATOR.validate_category_response(
            "vehicle", justified, 1, "gkr_test", [], allowed, canonical_policy="prefer",
        )
        self.assertEqual(leaves, [leaf])
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError, "contains literal fallback.*velvet seat"
        ):
            GENERATOR.validate_category_response(
                "vehicle", missing, 1, "gkr_test", [], allowed, canonical_policy="strict",
            )

    def test_final_attempt_downgrades_prefer_provenance_instead_of_raising(self):
        # Regression test: after every corrective retry is exhausted, a prefer-mode
        # provenance bookkeeping mismatch (the fallback text/reason no longer lines
        # up with the leaf) used to be an unrecoverable hard failure with no
        # --interactive escape hatch, discarding the whole category (and, before the
        # content-preservation fix, every other category already generated in the
        # same call). It is now a downgradable paperwork issue: the tags themselves
        # are still fine, so the category is accepted and the mismatch is reported.
        allowed = {"motor_vehicle", "city_lights"}
        leaf = "motor_vehicle, velvet seat, city_lights"
        missing = {
            "leaves": [leaf],
            "provenance": [{"canonical_tags": list(allowed), "literal_fallbacks": []}],
        }
        leaves, provenance, downgraded = GENERATOR.validate_category_response(
            "vehicle", missing, 1, "gkr_test", [], allowed, canonical_policy="prefer",
            final_attempt=True,
        )
        self.assertEqual(leaves, [leaf])
        self.assertEqual(provenance, missing["provenance"])
        self.assertEqual(len(downgraded["provenance_issues"]), 1)
        self.assertIn("lacks justified provenance for: velvet seat", downgraded["provenance_issues"][0])

    def test_final_attempt_still_enforces_strict_mode(self):
        # Strict mode's fallback-existence violation is a real content-policy
        # problem (a literal fallback was used when none are permitted at all),
        # not a paperwork mismatch, so it must never be downgraded.
        allowed = {"motor_vehicle", "city_lights"}
        leaf = "motor_vehicle, velvet seat, city_lights"
        missing = {
            "leaves": [leaf],
            "provenance": [{"canonical_tags": list(allowed), "literal_fallbacks": []}],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError, "contains literal fallback.*velvet seat"
        ):
            GENERATOR.validate_category_response(
                "vehicle", missing, 1, "gkr_test", [], allowed, canonical_policy="strict",
                final_attempt=True,
            )

    def test_final_attempt_downgrades_leaf_count_shortfall(self):
        # Regression test for the same failure shape as the concept-generation
        # shortfall: after every corrective retry is exhausted, returning fewer
        # unique leaves than requested (but more than zero) is now accepted
        # rather than hard-failing the whole category.
        allowed: set[str] = set()
        response = {
            "leaves": ["one leaf", "two leaf"],
            "provenance": [
                {"canonical_tags": [], "literal_fallbacks": []},
                {"canonical_tags": [], "literal_fallbacks": []},
            ],
        }
        leaves, provenance, downgraded = GENERATOR.validate_category_response(
            "subject", response, 5, "gkr_test", [], allowed, final_attempt=True,
        )
        self.assertEqual(leaves, ["one leaf", "two leaf"])
        self.assertEqual(len(provenance), 2)
        self.assertEqual(downgraded["leaf_shortfall"], 3)

    def test_leaf_count_shortfall_still_raises_without_final_attempt(self):
        allowed: set[str] = set()
        response = {
            "leaves": ["one leaf"],
            "provenance": [{"canonical_tags": [], "literal_fallbacks": []}],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError, "returned 1 unique leaves; expected 5"
        ):
            GENERATOR.validate_category_response("subject", response, 5, "gkr_test", [], allowed)

    def test_final_attempt_dedupes_within_chunk_duplicates_instead_of_raising(self):
        # Regression test: a model returning two leaves that normalize to the
        # same signature within one chunk's own response had no relief valve
        # at all, unlike cross-chunk duplicates (already relaxed via
        # forbidden_signatures) and motif repeats (already relaxed via
        # max_lead_motif_repeats). The duplicate is now removed, the chunk's
        # target implicitly shrinks by one (surfaced as leaf_shortfall), and
        # provenance stays aligned with the surviving leaves.
        allowed: set[str] = set()
        response = {
            "leaves": ["red sword", "(red:1.0) sword", "blue shield"],
            "provenance": [
                {"canonical_tags": [], "literal_fallbacks": [], "marker": "first"},
                {"canonical_tags": [], "literal_fallbacks": [], "marker": "duplicate"},
                {"canonical_tags": [], "literal_fallbacks": [], "marker": "third"},
            ],
        }
        leaves, provenance, downgraded = GENERATOR.validate_category_response(
            "subject", response, 3, "gkr_test", [], allowed, final_attempt=True,
        )
        self.assertEqual(leaves, ["red sword", "blue shield"])
        self.assertEqual([entry["marker"] for entry in provenance], ["first", "third"])
        self.assertEqual(downgraded["duplicate_positions"], ["1 and 2"])
        self.assertEqual(downgraded["leaf_shortfall"], 1)

    def test_within_chunk_duplicates_still_raise_without_final_attempt(self):
        allowed: set[str] = set()
        response = {
            "leaves": ["red sword", "(red:1.0) sword"],
            "provenance": [
                {"canonical_tags": [], "literal_fallbacks": []},
                {"canonical_tags": [], "literal_fallbacks": []},
            ],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError, "contains normalized duplicate leaves at positions 1 and 2"
        ):
            GENERATOR.validate_category_response("subject", response, 2, "gkr_test", [], allowed)

    def test_authoritative_vocabulary_accepts_exact_tag_outside_retrieval_palette(self):
        response = {
            "leaves": ["person, padded_jacket"],
            "provenance": [{"canonical_tags": ["padded_jacket"], "literal_fallbacks": []}],
        }
        leaves, _, _ = GENERATOR.validate_category_response(
            "subject", response, 1, "gkr_test", [], {"person"},
            canonical_vocabulary={"person", "padded_jacket"},
            canonical_policy="prefer",
        )
        self.assertEqual(leaves, response["leaves"])

    def test_authoritative_vocabulary_rejects_invented_underscore_tag(self):
        for leaf in ("person, padded_doublet", "person, (padded_doublet:1.2)"):
            response = {
                "leaves": [leaf],
                "provenance": [{"canonical_tags": ["padded_doublet"], "literal_fallbacks": []}],
            }
            with self.assertRaisesRegex(
                GENERATOR.CategoryValidationError, "outside the authoritative vocabulary",
            ):
                GENERATOR.validate_category_response(
                    "subject", response, 1, "gkr_test", [], {"person"},
                    canonical_vocabulary={"person", "padded_jacket", "neck_ruff"},
                )

    def test_prefer_does_not_treat_recognized_relationship_compositions_as_fallbacks(self):
        canonical = {
            "standing", "against_mirror", "holding", "black_rose", "steering_wheel",
        }
        leaf = (
            "standing against_mirror, holding black_rose, "
            "hands on (steering_wheel:1.2)"
        )
        self.assertEqual(GENERATOR.literal_fallback_phrases(leaf, canonical), [])

    def test_excluded_header_tags_are_rejected_in_plain_weighted_and_space_forms(self):
        for leaf in (
            "person, comic, street",
            "person, (comic:1.2), street",
            "person, western comics (style), street",
        ):
            response = {
                "leaves": [leaf],
                "provenance": [{"canonical_tags": [], "literal_fallbacks": []}],
            }
            with self.assertRaisesRegex(
                GENERATOR.CategoryValidationError, "uses tags excluded by the skeleton header"
            ):
                GENERATOR.validate_category_response(
                    "subject", response, 1, "gkr_test", [], set(),
                    forbidden_tags={"comic", "western_comics_(style)"},
                )

    def test_limited_palette_requires_exact_syntax_and_real_color_names(self):
        valid = {
            "leaves": ["cover, (red, copper, blue) limited_palette"],
            "provenance": [{"canonical_tags": ["limited_palette"], "literal_fallbacks": []}],
        }
        leaves, _, _ = GENERATOR.validate_category_response(
            "spotlight_covers", valid, 1, "gkr_test", [], set()
        )
        self.assertEqual(leaves, valid["leaves"])

        for palette in (
            "mahogany and gold palette",
            "(mahogany, gold) limited_palette",
            "(deep red, gold) limited_palette",
            "(red, gold) limited palette",
        ):
            invalid = {
                "leaves": [f"cover, {palette}"],
                "provenance": [{"canonical_tags": [], "literal_fallbacks": []}],
            }
            with self.assertRaisesRegex(
                GENERATOR.CategoryValidationError, "invalid limited-palette syntax or non-color names"
            ):
                GENERATOR.validate_category_response(
                    "spotlight_covers", invalid, 1, "gkr_test", [], set()
                )

    def skeleton(self, source: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "skeleton.yaml"
        path.write_text(source, encoding="utf-8")
        return GENERATOR.parse_skeleton(path)

    def test_parses_global_and_category_generator_comments(self):
        skeleton = self.skeleton(
            "# MODE: tags\n"
            "# GENERATOR: THEME: original heroes\n"
            "gkr_hero:\n"
            "  # Ordinary documentation.\n"
            "  # GENERATOR: Create 30 leaves with visible costume evidence.\n"
            "  archetype: []\n"
            "  # GENERATOR: Complete single-image scenes.\n"
            "  hero_scene:\n"
            "    []\n"
        )
        self.assertEqual(skeleton.namespace, "gkr_hero")
        self.assertEqual(skeleton.global_directives, ["THEME: original heroes"])
        self.assertEqual(skeleton.categories[0].directives, ["Create 30 leaves with visible costume evidence."])
        self.assertEqual(skeleton.categories[1].directives, ["Complete single-image scenes."])

    def test_requires_tags_mode_and_empty_categories(self):
        with self.assertRaisesRegex(ValueError, "MODE: tags"):
            self.skeleton("gkr_bad:\n  subject: []\n")
        with self.assertRaisesRegex(ValueError, "must be empty"):
            self.skeleton("# MODE: tags\ngkr_bad:\n  subject:\n    - existing\n")

    def test_explicit_count_overrides_planner_and_defaults_apply(self):
        skeleton = self.skeleton(
            "# MODE: tags\ngkr_hero:\n"
            "  # GENERATOR: Create 30 leaves.\n"
            "  archetype: []\n"
            "  spotlight: []\n"
            "  random: []\n"
        )
        result = {
            "categories": [
                {"name": "archetype", "kind": "component", "count": 5, "dependencies": []},
                {"name": "spotlight", "kind": "spotlight", "dependencies": ["archetype"]},
                {"name": "random", "kind": "router", "dependencies": []},
            ]
        }
        plan = GENERATOR.parse_plan(result, skeleton, args())
        by_name = {item.name: item for item in plan}
        self.assertEqual(by_name["archetype"].count, 30)
        self.assertEqual(by_name["spotlight"].count, 50)
        self.assertEqual(by_name["random"].dependencies, ["spotlight"])

    def test_spotlight_only_skeleton_rejects_all_planner_added_categories(self):
        skeleton = self.skeleton(
            "# MODE: tags\ngkr_comics:\n"
            "  # GENERATOR: Create 200 spotlights.\n"
            "  spotlight_european_comics: []\n"
        )
        result = {"categories": [
            {"name": "spotlight_european_comics", "kind": "spotlight", "count": 200},
            {"name": "eu_character_archetypes", "kind": "component", "count": 20},
            {"name": "eu_scene", "kind": "scene", "count": 12,
             "dependencies": ["eu_character_archetypes"]},
            {"name": "random_european_comics", "kind": "router", "count": 0,
             "dependencies": ["spotlight_european_comics", "eu_scene"]},
        ]}
        plan = GENERATOR.parse_plan(result, skeleton, args())
        self.assertEqual([item.name for item in plan], ["spotlight_european_comics"])
        self.assertEqual(plan[0].count, 200)

    def test_namespaced_random_is_forced_to_required_router(self):
        skeleton = self.skeleton(
            "# MODE: tags\ngkr_villains:\n  villain_scene: []\n  superhero_villains_random: []\n"
        )
        result = {"categories": [
            {"name": "villain_scene", "kind": "scene", "dependencies": []},
            {
                "name": "superhero_villains_random", "kind": "component",
                "dependencies": ["villain_scene"],
            },
        ]}
        plan = GENERATOR.parse_plan(result, skeleton, args())
        router = next(item for item in plan if item.name == "superhero_villains_random")
        self.assertEqual(router.kind, "router")
        self.assertEqual(router.count, 0)

    def test_invalid_plan_is_corrected_with_validation_feedback(self):
        skeleton = self.skeleton(
            "# MODE: tags\ngkr_test:\n  scene: []\n  random: []\n"
        )

        class FakeSession:
            def __init__(self):
                self.args = args(max_planner_retries=1, verbose=False)
                self.calls = []

            def request(self, name, instruction, items):
                self.calls.append((name, items))
                categories = [
                    {"name": "scene", "kind": "scene", "dependencies": []},
                    {"name": "orphan", "kind": "component", "dependencies": []},
                    {"name": "random", "kind": "router", "dependencies": ["scene"]},
                ]
                if name == "generation plan correction":
                    self.validation_error = items[0]["validation_error"]
                    self.correction_attempt = items[0]["correction_attempt"]
                    categories[-1]["dependencies"].append("orphan")
                return [{"id": "plan", "categories": categories}]

        session = FakeSession()
        plan = GENERATOR.generate_valid_plan(session, skeleton, "policy")
        self.assertIn("unreachable", session.validation_error)
        self.assertEqual(session.correction_attempt, 1)
        self.assertEqual([name for name, _ in session.calls], [
            "generation plan", "generation plan correction",
        ])
        self.assertEqual(next(item for item in plan if item.name == "random").dependencies, ["scene", "orphan"])

    def test_required_categories_are_restored_and_added_categories_are_capped(self):
        skeleton = self.skeleton("# MODE: tags\ngkr_hero:\n  required_scene: []\n")
        result = {"categories": [
            {"name": f"extra_{index}", "kind": "component", "dependencies": []}
            for index in range(5)
        ]}
        plan = GENERATOR.parse_plan(result, skeleton, args(max_added_categories=2))
        self.assertEqual([item.name for item in plan], ["extra_0", "extra_1", "required_scene"])
        self.assertTrue(next(item for item in plan if item.name == "required_scene").required)

    def test_depth_and_cycles_are_rejected(self):
        chain = [
            GENERATOR.CategoryPlan("a", "router", "", 0, ["b"]),
            GENERATOR.CategoryPlan("b", "combo", "", 1, ["c"]),
            GENERATOR.CategoryPlan("c", "component", "", 1, []),
        ]
        with self.assertRaisesRegex(ValueError, "depth 3"):
            GENERATOR.validate_plan_depth(chain, 2)
        chain[-1].dependencies = ["a"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            GENERATOR.validate_plan_depth(chain, 6)

    def test_categories_unreachable_from_router_are_rejected(self):
        plans = [
            GENERATOR.CategoryPlan("used", "scene", "", 1, []),
            GENERATOR.CategoryPlan("unused", "component", "", 1, []),
            GENERATOR.CategoryPlan("random", "router", "", 0, ["used"]),
        ]
        with self.assertRaisesRegex(ValueError, "unreachable.*unused"):
            GENERATOR.validate_plan_routes(plans)

    def test_rendered_output_is_valid_yaml_and_preserves_directives(self):
        skeleton = self.skeleton(
            "# MODE: tags\n# GENERATOR: THEME: heroes\ngkr_hero:\n"
            "  # GENERATOR: Visible archetypes.\n  archetype: []\n"
        )
        plans = [
            GENERATOR.CategoryPlan("archetype", "component", "", 1, [], True),
            GENERATOR.CategoryPlan("costume", "component", "", 1, [], False),
        ]
        rendered = GENERATOR.render_wildcard(
            skeleton, plans, {"archetype": ["masked hero, red cape"], "costume": ["segmented armor"]}
        )
        import yaml
        parsed = yaml.safe_load(rendered)
        self.assertEqual(parsed["gkr_hero"]["archetype"], ["masked hero, red cape"])
        self.assertIn("# GENERATOR: Visible archetypes.", rendered)
        self.assertIn("Added by wildcard_generator.py", rendered)

    def test_default_artifacts_match_linter_naming(self):
        fixed, report, manifest = GENERATOR.default_artifact_paths(Path("gkr-hero.yaml"))
        self.assertEqual(fixed, Path("gkr-hero.fixed.yaml"))
        self.assertEqual(report, Path("gkr-hero.fixed-report.md"))
        self.assertEqual(manifest, Path("gkr-hero.generation.json"))

    def test_duplicate_generation_finding_uses_final_file_lines_and_readable_pairs(self):
        fixed = Path("/tmp/gkr-test.fixed.yaml")
        linter = sys.modules["wildcard_linter"]
        leaves = [
            linter.Leaf("a", str(fixed), "gkr_test", "covers", 0, 12,
                        "cover, red", (), "tags"),
            linter.Leaf("b", str(fixed), "gkr_test", "covers", 1, 13,
                        "cover, blue", (), "tags"),
            linter.Leaf("c", str(fixed), "gkr_test", "covers", 2, 14,
                        "cover, red", (), "tags"),
        ]
        issue = {
            "category": "covers", "rule": "duplicate_leaves_across_chunks",
            "duplicates": [{
                "prior_position": 1, "prior_leaf": "old original",
                "current_position": 3, "current_leaf": "old duplicate",
            }],
        }
        finding = GENERATOR.duplicate_generation_finding(issue, leaves, fixed)
        self.assertEqual(finding.line, 14)
        self.assertIn(f"duplicate: {fixed}:14", finding.evidence)
        self.assertIn(f"original:  {fixed}:12", finding.evidence)
        self.assertIn("    cover, red", finding.evidence)
        report = linter.render([finding], leaves, "markdown", fix_attempted=True)
        self.assertIn("```text\nPair 1", report)

    def test_generation_issue_finding_handles_every_known_rule(self):
        # Regression test for a real crash: report generation iterated
        # session.generation_issues and assumed any rule it didn't explicitly
        # name was "unused_declared_dependencies", accessing
        # issue["missing_dependencies"] unconditionally. Adding the four
        # graceful-degradation rules in this file without also updating that
        # renderer produced a bare KeyError that crashed the run after a full,
        # successful 200-leaf category generation. Every rule that can
        # actually be appended to generation_issues must be covered here.
        fixed = Path("/tmp/gkr-test.fixed.yaml")
        issues = [
            {
                "category": "subject", "rule": "trimmed_excess_generated_concepts",
                "expected_count": 2, "removed_concepts": [{"summary": "excess"}],
            },
            {
                "category": "subject", "rule": "trimmed_excess_generated_leaves",
                "expected_count": 2, "removed_leaves": ["excess leaf"],
            },
            {
                "category": "subject", "rule": "repeated_component_lead_motifs",
                "motifs": {"sword": [{"position": 1, "leaf": "sword, red"}, {"position": 2, "leaf": "sword, blue"}]},
            },
            {
                "category": "subject", "rule": "unused_declared_dependencies",
                "missing_dependencies": ["other_category"],
            },
            {
                "category": "subject", "rule": "undersized_concept_chunk",
                "chunk_index": 4, "requested_count": 25, "accepted_count": 20, "shortfall": 5,
            },
            {
                "category": "subject", "rule": "undersized_leaf_chunk",
                "chunk_index": 4, "requested_count": 25, "accepted_count": 20, "shortfall": 5,
            },
            {
                "category": "subject", "rule": "unresolved_prefer_provenance",
                "chunk_index": 0, "issues": ["leaf 4 lacks justified provenance for: drilling"],
            },
            {
                "category": "subject", "rule": "unresolved_within_chunk_duplicate_leaves",
                "chunk_index": 0, "duplicate_positions": ["1 and 2"],
            },
        ]
        for issue in issues:
            finding = GENERATOR.generation_issue_finding(issue, [], fixed)
            self.assertEqual(finding.rule, issue["rule"])
            self.assertEqual(finding.category, "subject")

    def test_generation_issue_finding_never_crashes_on_an_unrecognized_rule(self):
        # The defensive fallback must produce a usable Finding, not a KeyError,
        # for a rule this function has no specific handling for -- including
        # one missing its own "category" key, the actual shape of the crash.
        fixed = Path("/tmp/gkr-test.fixed.yaml")
        finding = GENERATOR.generation_issue_finding(
            {"rule": "some_future_rule_not_yet_handled"}, [], fixed,
        )
        self.assertEqual(finding.rule, "some_future_rule_not_yet_handled")
        self.assertIn("does not have specific handling for", finding.message)

    def test_exact_canonical_normalization_is_rewritten_without_llm(self):
        linter = sys.modules["wildcard_linter"]
        leaves = [
            linter.Leaf(
                "plain", "test.yaml", "gkr", "scene", 0, 10,
                "1man, grey suit, street", (), "tags",
            ),
            linter.Leaf(
                "weighted", "test.yaml", "gkr", "scene", 1, 11,
                "1woman, (blue coat:1.2), rain", (), "tags",
            ),
        ]
        findings = [
            linter.Finding(
                "warning", "canonical_tag_normalization", "normalize", leaf.file,
                leaf.line, leaf.category, leaf.uid,
                json.dumps({
                    "input": source, "status": "exact_normalized_match",
                    "candidates": [candidate],
                }),
            )
            for leaf, source, candidate in (
                (leaves[0], "grey suit", "grey_suit"),
                (leaves[1], "blue coat", "blue_coat"),
            )
        ]
        rewrites = GENERATOR.deterministic_canonical_rewrites(leaves, findings)
        self.assertEqual(rewrites["plain"], "1man, grey_suit, street")
        self.assertEqual(rewrites["weighted"], "1woman, (blue_coat:1.2), rain")

    def test_post_repair_report_marks_remaining_warning_unresolved(self):
        linter = sys.modules["wildcard_linter"]
        leaf = linter.Leaf(
            "leaf-1", "gkr-hero.fixed.yaml", "gkr_hero", "scene", 0, 4,
            "familiar hero", (), "tags",
        )
        finding = linter.Finding(
            "warning", "interpretive_visual_modifier", "needs visible evidence",
            leaf.file, leaf.line, leaf.category, leaf.uid,
        )
        report = linter.render(
            [finding], [leaf], "markdown", fix_attempted=True, fixed_leaf_ids=set()
        )
        self.assertIn("[UNRESOLVED]", report)
        self.assertIn("Unresolved after fixed-output generation: 1", report)

    def test_generation_is_concept_then_retrieval_then_realization(self):
        index_module = sys.modules["danbooru_index"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        csv_path = root / "tags.csv"
        index_path = root / "tags.sqlite"
        csv_path.write_text("name,post_count\nsuperhero_landing,1000\n", encoding="utf-8")
        index_module.build_index(csv_path, index_path)
        index = index_module.DanbooruIndex(index_path)
        self.addCleanup(index.close)
        skeleton = self.skeleton("# MODE: tags\ngkr_hero:\n  scene: []\n  random: []\n")
        plans = [
            GENERATOR.CategoryPlan("scene", "scene", "hero scene", 1, [], True),
            GENERATOR.CategoryPlan("random", "router", "", 0, ["scene"], True),
        ]

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    batch_categories=3, retrieval_candidates=5, content_profile="general", verbose=False,
                    max_category_retries=1, interactive=False,
                )
                self.calls = []
                self.provenance = {}

            def request(self, name, instruction, items):
                self.calls.append(name)
                if name == "concept generation":
                    return [{"id": "scene", "concepts": [{
                        "summary": "three point hero landing", "search_queries": ["superhero landing"]
                    }]}]
                candidates = items[0]["concepts"][0]["candidates"]
                self.assert_candidate = candidates[0]["tag"]
                return [{
                    "id": "scene", "leaves": ["superhero_landing"],
                    "provenance": [{"canonical_tags": ["superhero_landing"], "literal_fallbacks": []}],
                }]

        session = FakeSession()
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary({"superhero_landing"})
        generated = GENERATOR.generate_categories(
            session, skeleton, plans, "policy", vocabulary, index, None, ""
        )
        self.assertEqual(session.calls, ["concept generation", "category generation"])
        self.assertEqual(session.assert_candidate, "superhero_landing")
        self.assertEqual(generated["scene"], ["superhero_landing"])
        self.assertEqual(generated["random"], ["__gkr_hero/scene__"])

    def test_prefer_mode_revises_draft_literals_with_retrieved_candidates(self):
        index_module = sys.modules["danbooru_index"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        csv_path = root / "tags.csv"
        index_path = root / "tags.sqlite"
        csv_path.write_text("name,post_count\nseat,1000\nvelvet,500\n", encoding="utf-8")
        index_module.build_index(csv_path, index_path)
        index = index_module.DanbooruIndex(index_path)
        self.addCleanup(index.close)
        skeleton = self.skeleton(
            "# MODE: tags\n# CANONICAL_POLICY: prefer\ngkr_test:\n  subject: []\n"
        )
        plan = GENERATOR.CategoryPlan("subject", "component", "seat subject", 1, [], True)

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    batch_categories=3, category_chunk_size=25, retrieval_candidates=5,
                    content_profile="general", verbose=False, canonical_policy=None,
                    max_category_retries=1, interactive=False,
                )
                self.calls = []
                self.provenance = {}
                self.generation_issues = []

            def request(self, name, instruction, items):
                self.calls.append(name)
                if name == "concept generation":
                    return [{"id": "subject", "concepts": [{
                        "summary": "ornate chair", "search_queries": ["seat velvet"],
                    }]}]
                if name == "category generation":
                    return [{
                        "id": "subject", "leaves": ["velvet seat"],
                        "provenance": [{"canonical_tags": [], "literal_fallbacks": []}],
                    }]
                self.assert_guidance = items[0]["literal_fallback_guidance"]
                return [{
                    "id": "subject", "leaves": ["seat, velvet"],
                    "provenance": [{
                        "canonical_tags": ["seat", "velvet"], "literal_fallbacks": [],
                    }],
                }]

        session = FakeSession()
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary({"seat", "velvet"})
        generated = GENERATOR.generate_categories(
            session, skeleton, [plan], "policy", vocabulary, index, None, "",
        )
        self.assertEqual(session.calls, ["concept generation", "category generation"])
        self.assertEqual(generated["subject"], ["velvet seat"])

    def test_generated_leaves_get_their_own_text_recorded_on_provenance(self):
        # The linter's --spotlight-intents keys off leaf_text recorded on each
        # provenance entry, so the manifest must be self-contained -- a reader
        # must not need to positionally align it with the separately rendered
        # YAML, which may already have been edited by the time it is read.
        index_module = sys.modules["danbooru_index"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        csv_path = root / "tags.csv"
        index_path = root / "tags.sqlite"
        csv_path.write_text("name,post_count\nsafari_jacket,1000\n", encoding="utf-8")
        index_module.build_index(csv_path, index_path)
        index = index_module.DanbooruIndex(index_path)
        self.addCleanup(index.close)
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  spotlight_european_comics: []\n")
        plan = GENERATOR.CategoryPlan("spotlight_european_comics", "spotlight", "spotlight", 1, [], True)

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    batch_categories=3, category_chunk_size=25, retrieval_candidates=5,
                    content_profile="general", verbose=False, canonical_policy=None,
                    max_category_retries=1, interactive=False,
                )
                self.calls = []
                self.provenance = {}
                self.generation_issues = []

            def request(self, name, instruction, items):
                self.calls.append(name)
                if name == "concept generation":
                    return [{"id": "spotlight_european_comics", "concepts": [{
                        "summary": "adventurer with map", "search_queries": ["safari jacket"],
                    }]}]
                return [{
                    "id": "spotlight_european_comics",
                    "leaves": ["1man, safari_jacket, jungle, ruins"],
                    "provenance": [{
                        "canonical_tags": ["safari_jacket"], "literal_fallbacks": [],
                        "intent": "An adventurer surveys jungle ruins.",
                    }],
                }]

        session = FakeSession()
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary({"safari_jacket"})
        GENERATOR.generate_categories(
            session, skeleton, [plan], "policy", vocabulary, index, None, "",
        )
        entry = session.provenance["spotlight_european_comics"][0]
        self.assertEqual(entry["leaf_text"], "1man, safari_jacket, jungle, ruins")
        self.assertEqual(entry["intent"], "An adventurer surveys jungle ruins.")

    def test_content_from_earlier_successful_categories_survives_a_later_failure(self):
        # Regression test for a real failed run: two independent categories with no
        # dependencies land in the same wave/batch and are requested together. Before
        # this fix, generate_categories only returned its accumulated dict on success,
        # so a later category exhausting its corrective retries and raising discarded
        # every category already completed in the same call -- including ones that
        # succeeded -- because the caller's `content` variable was never assigned.
        # content is now mutated in place, so it must retain "category_a_safe" even
        # though "category_b_failing" raises.
        index_module = sys.modules["danbooru_index"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        csv_path = root / "tags.csv"
        index_path = root / "tags.sqlite"
        csv_path.write_text("name,post_count\ntag_one,1000\n", encoding="utf-8")
        index_module.build_index(csv_path, index_path)
        index = index_module.DanbooruIndex(index_path)
        self.addCleanup(index.close)
        skeleton = self.skeleton(
            "# MODE: tags\ngkr_test:\n  category_a_safe: []\n  category_b_failing: []\n"
        )
        plans = [
            GENERATOR.CategoryPlan("category_a_safe", "component", "safe", 1, [], True),
            GENERATOR.CategoryPlan("category_b_failing", "component", "bad", 1, [], True),
        ]

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    batch_categories=3, category_chunk_size=25, retrieval_candidates=5,
                    content_profile="general", verbose=False, canonical_policy=None,
                    max_category_retries=1, interactive=False,
                )
                self.calls = []
                self.provenance = {}
                self.generation_issues = []

            def request(self, name, instruction, items):
                self.calls.append(name)
                ids = {item["id"] for item in items}
                if name == "concept generation":
                    return [
                        {"id": category_id, "concepts": [{"summary": "x", "search_queries": ["x"]}]}
                        for category_id in ids
                    ]
                results = []
                if "category_a_safe" in ids:
                    results.append({
                        "id": "category_a_safe", "leaves": ["safe leaf"],
                        "provenance": [{"canonical_tags": [], "literal_fallbacks": []}],
                    })
                if "category_b_failing" in ids:
                    # Always returns the wrong leaf count (0 instead of 1), a
                    # structural error that is never downgraded, so every corrective
                    # retry fails identically and generate_categories must raise.
                    results.append({"id": "category_b_failing", "leaves": [], "provenance": []})
                return results

        session = FakeSession()
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary(set())
        content: dict[str, list[str]] = {}
        with self.assertRaises((GENERATOR.CategoryValidationError, RuntimeError)):
            GENERATOR.generate_categories(
                session, skeleton, plans, "policy", vocabulary, index, None, "", content,
            )
        self.assertEqual(content.get("category_a_safe"), ["safe leaf"])
        self.assertNotIn("category_b_failing", content)

    def test_excess_concepts_are_trimmed_and_recorded(self):
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  subject: []\n")
        plan = GENERATOR.CategoryPlan("subject", "component", "subject", 2, [], True)

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(content_profile="general", verbose=False)
                self.generation_issues = []

            def request(self, name, instruction, items):
                return [{"id": "subject", "concepts": [
                    {"summary": "first", "search_queries": ["first"]},
                    {"summary": "second", "search_queries": ["second"]},
                    {"summary": "excess", "search_queries": ["excess"]},
                ]}]

        session = FakeSession()
        concepts = GENERATOR.generate_concepts(session, skeleton, [plan], "policy")
        self.assertEqual([item["summary"] for item in concepts["subject"]], ["first", "second"])
        self.assertEqual(session.generation_issues[0]["rule"], "trimmed_excess_generated_concepts")
        self.assertEqual(session.generation_issues[0]["removed_concepts"][0]["summary"], "excess")

    def test_concept_shortfall_after_retries_shrinks_chunk_and_is_recorded(self):
        # Regression test for a real failed run: exhausting corrective retries
        # used to always raise, discarding the entire run even when the model
        # returned a genuine majority of usable concepts (not zero). A
        # persistent partial shortfall is now accepted: this chunk's target
        # shrinks to match what was actually achieved (so leaf realization
        # asks for a matching count), and the gap is recorded so the outer
        # accumulation loop can request the remainder in a later chunk instead
        # of the whole run aborting.
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  subject: []\n")
        plan = GENERATOR.CategoryPlan("subject", "component", "subject", 25, [], True)

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    content_profile="general", verbose=False,
                    max_category_retries=1, concept_continuation_buffer=3,
                )
                self.generation_issues = []

            def request(self, name, instruction, items):
                # Always returns the same 20 concepts regardless of how many
                # more were asked for, simulating a model that has run out of
                # novel ideas within this chunk.
                return [{"id": "subject", "concepts": [
                    {"summary": f"concept {i}", "search_queries": [f"concept {i}"]}
                    for i in range(20)
                ]}]

        session = FakeSession()
        concepts = GENERATOR.generate_concepts(session, skeleton, [plan], "policy")
        self.assertEqual(len(concepts["subject"]), 20)
        self.assertEqual(plan.count, 20)
        issue = session.generation_issues[0]
        self.assertEqual(issue["rule"], "undersized_concept_chunk")
        self.assertEqual(issue["requested_count"], 25)
        self.assertEqual(issue["accepted_count"], 20)
        self.assertEqual(issue["shortfall"], 5)

    def test_zero_concepts_after_retries_still_raises(self):
        # A genuine zero-usable-concepts result is not a partial shortfall to
        # gracefully accept -- it indicates a deeper problem, so this must
        # still raise rather than silently produce a zero-leaf chunk.
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  subject: []\n")
        plan = GENERATOR.CategoryPlan("subject", "component", "subject", 5, [], True)

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    content_profile="general", verbose=False,
                    max_category_retries=1, concept_continuation_buffer=3,
                )
                self.generation_issues = []

            def request(self, name, instruction, items):
                return [{"id": "subject", "concepts": []}]

        session = FakeSession()
        with self.assertRaisesRegex(RuntimeError, "returned 0 new unique concepts"):
            GENERATOR.generate_concepts(session, skeleton, [plan], "policy")

    def test_repeated_id_single_concept_objects_are_coalesced(self):
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  subject: []\n")
        plan = GENERATOR.CategoryPlan("subject", "component", "subject", 2, [], True)

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    content_profile="general", verbose=False, max_category_retries=0,
                )
                self.generation_issues = []

            def request(self, name, instruction, items):
                return [
                    {"id": "subject", "concepts": {
                        "summary": "first", "search_queries": ["first query"],
                    }},
                    {"id": "subject", "concepts": {
                        "summary": "second", "search_queries": ["second query"],
                    }},
                ]

        concepts = GENERATOR.generate_concepts(FakeSession(), skeleton, [plan], "policy")
        self.assertEqual(
            [concept["summary"] for concept in concepts["subject"]], ["first", "second"]
        )

    def test_partial_concept_corrections_fill_missing_continuation(self):
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  subject: []\n")
        plan = GENERATOR.CategoryPlan("subject", "component", "subject", 3, [], True)

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    content_profile="general", verbose=False, max_category_retries=2,
                )
                self.generation_issues = []
                self.corrections = []

            def request(self, name, instruction, items):
                if name == "concept generation":
                    return [{"id": "subject", "concepts": [{"summary": "first"}]}]
                self.corrections.append(items[0])
                summary = "second" if len(self.corrections) == 1 else "third"
                return [{"id": "subject", "concepts": [{"summary": summary}]}]

        session = FakeSession()
        concepts = GENERATOR.generate_concepts(session, skeleton, [plan], "policy")
        self.assertEqual(
            [concept["summary"] for concept in concepts["subject"]],
            ["first", "second", "third"],
        )
        self.assertEqual([item["continuation_needed"] for item in session.corrections], [2, 1])
        self.assertEqual([item["requested_count"] for item in session.corrections], [4, 2])
        self.assertEqual(
            session.corrections[1]["avoid_concept_summaries"], ["first", "second"]
        )

    def test_requested_count_allows_descriptive_text_before_prompts(self):
        directives = ["Create 200 complete, highly specific single-image prompts."]
        self.assertEqual(GENERATOR.requested_count(directives, "spotlight"), 200)
        self.assertTrue(GENERATOR.has_explicit_count(directives))

    def test_requested_count_recognizes_spotlights_noun(self):
        directives = [
            "Create 200 complete single-image European comics spotlights across traditions."
        ]
        self.assertEqual(GENERATOR.requested_count(directives, "spotlight"), 200)
        self.assertTrue(GENERATOR.has_explicit_count(directives))

    def test_requested_count_resolves_ranged_phrasing_to_the_upper_bound(self):
        # Regression test: the middle span used to allow crossing another digit
        # run, so re.search's leftmost-match behavior bound to the FIRST number
        # ("Create between 20 and 30 leaves" resolved to 20, not 30).
        directives = ["# GENERATOR: Create between 20 and 30 leaves for this category"]
        self.assertEqual(GENERATOR.requested_count(directives, "component"), 30)

    def test_requested_count_still_finds_single_number_directives(self):
        directives = ["# GENERATOR: Create 12 items for this pool"]
        self.assertEqual(GENERATOR.requested_count(directives, "component"), 12)

    def test_category_batches_never_shares_a_batch_with_its_own_dependency(self):
        # Regression test: batching used to slice the flat topologically-sorted
        # list by fixed size with no wave boundary, so a small dependency-free
        # wave could spill into the next wave and a combo/scene category would be
        # generated in the same LLM call as its own dependency -- meaning
        # dependency_examples would be {} because the dependency's leaves did not
        # exist yet within that call.
        plans = [
            GENERATOR.CategoryPlan("hero_props", "component", "props", 5, [], True),
            GENERATOR.CategoryPlan("hero_pose", "component", "pose", 5, [], True),
            GENERATOR.CategoryPlan("hero_combo", "combo", "combo", 5, ["hero_props", "hero_pose"], True),
            GENERATOR.CategoryPlan("hero_scene", "scene", "scene", 5, ["hero_combo"], True),
        ]
        batches = GENERATOR.category_batches(plans, 3)
        for batch in batches:
            names = {plan.name for plan in batch}
            for plan in batch:
                self.assertFalse(
                    set(plan.dependencies) & names,
                    f"{plan.name} shares a batch with its own dependency: {names}",
                )
        # Every plan is still scheduled exactly once, in a valid dependency order.
        ordered_names = [plan.name for batch in batches for plan in batch]
        self.assertEqual(sorted(ordered_names), sorted(plan.name for plan in plans))
        self.assertLess(ordered_names.index("hero_props"), ordered_names.index("hero_combo"))
        self.assertLess(ordered_names.index("hero_pose"), ordered_names.index("hero_combo"))
        self.assertLess(ordered_names.index("hero_combo"), ordered_names.index("hero_scene"))

    def test_session_record_usage_enforces_token_budget_immediately(self):
        # Regression test: --max-total-tokens used to be checked only once per
        # repair stage (before the whole multi-batch llm_review/llm_suggest_fixes
        # call), not once per underlying HTTP call, so a large file could blow
        # past the configured budget by an arbitrary multiple within one stage.
        # record_usage is the linter's llm_usage_callback, invoked after every
        # individual HTTP response including each internal batch of a
        # multi-batch stage, so checking the budget there closes that gap.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "gkr-test.yaml"
        session_args = SimpleNamespace(
            output=output, interactive_overrides=None, model="test-model",
            base_url="http://localhost:11434/v1", api_key_env="PATH",
            max_total_tokens=100,
        )
        session = GENERATOR.Session(session_args, None)
        session.record_usage({"total_tokens": 40})
        self.assertEqual(session.reported_tokens, 40)
        with self.assertRaises(RuntimeError):
            session.record_usage({"total_tokens": 70})
        self.assertEqual(session.reported_tokens, 110)

    def test_large_category_is_generated_and_aggregated_in_chunks(self):
        index_module = sys.modules["danbooru_index"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        csv_path = root / "tags.csv"
        index_path = root / "tags.sqlite"
        csv_path.write_text("name,post_count\ntag_one,1000\ntag_two,900\n", encoding="utf-8")
        index_module.build_index(csv_path, index_path)
        index = index_module.DanbooruIndex(index_path)
        self.addCleanup(index.close)
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  spotlight: []\n")
        plans = [GENERATOR.CategoryPlan("spotlight", "spotlight", "test", 2, [], True)]

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    batch_categories=3, category_chunk_size=1, retrieval_candidates=5,
                    content_profile="general", verbose=False, max_category_retries=1,
                    interactive=False,
                )
                self.calls = []
                self.provenance = {}
                self.generation_issues = []

            def request(self, name, instruction, items):
                self.calls.append((name, items))
                chunk = items[0]["chunk_index"]
                tag = "tag_one" if chunk == 0 else "tag_two"
                if name == "concept generation":
                    return [{"id": "spotlight", "concepts": [{
                        "summary": f"concept {chunk}", "search_queries": [tag]
                    }]}]
                return [{
                    "id": "spotlight", "leaves": [tag],
                    "provenance": [{"canonical_tags": [tag], "literal_fallbacks": []}],
                }]

        session = FakeSession()
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary({"tag_one", "tag_two"})
        generated = GENERATOR.generate_categories(
            session, skeleton, plans, "policy", vocabulary, index, None, ""
        )
        self.assertEqual(generated["spotlight"], ["tag_one", "tag_two"])
        self.assertEqual([name for name, _ in session.calls], [
            "concept generation", "category generation",
            "concept generation", "category generation",
        ])
        self.assertEqual(session.calls[2][1][0]["avoid_concept_summaries"], ["concept 0"])
        self.assertEqual(session.calls[3][1][0]["avoid_duplicate_leaves"], ["tag_one"])

    def test_exhausted_cross_chunk_duplicates_are_recorded_and_do_not_abort(self):
        index_module = sys.modules["danbooru_index"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        csv_path = root / "tags.csv"
        index_path = root / "tags.sqlite"
        csv_path.write_text("name,post_count\ntag_one,1000\n", encoding="utf-8")
        index_module.build_index(csv_path, index_path)
        index = index_module.DanbooruIndex(index_path)
        self.addCleanup(index.close)
        skeleton = self.skeleton("# MODE: tags\ngkr_test:\n  spotlight: []\n")
        plans = [GENERATOR.CategoryPlan("spotlight", "component", "test", 2, [], True)]

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    batch_categories=3, category_chunk_size=1, retrieval_candidates=5,
                    content_profile="general", verbose=False, max_category_retries=0,
                    interactive=False,
                )
                self.provenance = {}
                self.generation_issues = []

            def request(self, name, instruction, items):
                if name == "concept generation":
                    chunk = items[0]["chunk_index"]
                    return [{"id": "spotlight", "concepts": [{
                        "summary": f"concept {chunk}", "search_queries": ["tag_one"]
                    }]}]
                return [{
                    "id": "spotlight", "leaves": ["tag_one"],
                    "provenance": [{"canonical_tags": ["tag_one"], "literal_fallbacks": []}],
                }]

        session = FakeSession()
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary({"tag_one"})
        generated = GENERATOR.generate_categories(
            session, skeleton, plans, "policy", vocabulary, index, None, ""
        )
        self.assertEqual(generated["spotlight"], ["tag_one", "tag_one"])
        issue = session.generation_issues[0]
        self.assertEqual(issue["rule"], "duplicate_leaves_across_chunks")
        self.assertEqual(issue["duplicates"][0]["prior_position"], 1)
        self.assertEqual(issue["duplicates"][0]["current_position"], 2)
        motif_issue = session.generation_issues[1]
        self.assertEqual(motif_issue["rule"], "repeated_component_lead_motifs")
        self.assertEqual(
            [entry["position"] for entry in motif_issue["motifs"]["tag_one"]], [1, 2]
        )

    def test_invalid_palette_tag_is_reported_and_corrected(self):
        index_module = sys.modules["danbooru_index"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        csv_path = root / "tags.csv"
        index_path = root / "tags.sqlite"
        csv_path.write_text("name,post_count\nsuperhero_landing,1000\n", encoding="utf-8")
        index_module.build_index(csv_path, index_path)
        index = index_module.DanbooruIndex(index_path)
        self.addCleanup(index.close)
        skeleton = self.skeleton("# MODE: tags\ngkr_hero:\n  hero_action: []\n")
        plans = [GENERATOR.CategoryPlan("hero_action", "component", "hero action", 1, [], True)]

        class FakeSession:
            def __init__(self):
                self.args = SimpleNamespace(
                    batch_categories=3, retrieval_candidates=5, content_profile="general", verbose=False,
                    max_category_retries=1, interactive=False,
                )
                self.calls = []
                self.provenance = {}

            def request(self, name, instruction, items):
                self.calls.append((name, items))
                if name == "concept generation":
                    return [{"id": "hero_action", "concepts": [{
                        "summary": "superhero landing", "search_queries": ["superhero landing"]
                    }]}]
                if name == "category generation":
                    return [{
                        "id": "hero_action", "leaves": ["invented_hero_tag"],
                        "provenance": [{"canonical_tags": ["invented_hero_tag"], "literal_fallbacks": []}],
                    }]
                self.validation_error = items[0]["validation_error"]
                return [{
                    "id": "hero_action", "leaves": ["superhero_landing"],
                    "provenance": [{"canonical_tags": ["superhero_landing"], "literal_fallbacks": []}],
                }]

        session = FakeSession()
        vocabulary = sys.modules["wildcard_linter"].DanbooruVocabulary({"superhero_landing"})
        generated = GENERATOR.generate_categories(session, skeleton, plans, "policy", vocabulary, index, None, "")
        self.assertEqual(generated["hero_action"], ["superhero_landing"])
        self.assertIn("invented_hero_tag", session.validation_error)
        self.assertEqual(session.calls[-1][1][0]["correction_attempt"], 1)
        self.assertEqual([name for name, _ in session.calls], [
            "concept generation", "category generation", "category correction",
        ])

    def test_invalid_palette_error_names_all_tags(self):
        response = {
            "leaves": ["outside_one, outside_two"],
            "provenance": [{"canonical_tags": ["outside_two", "outside_one"], "literal_fallbacks": []}],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError, "outside_one, outside_two"
        ):
            GENERATOR.validate_category_response("hero_action", response, 1, "gkr_hero", [], {"inside_tag"})

    def test_hyphenated_underscore_tag_is_validated_as_a_complete_tag(self):
        response = {
            "leaves": ["heads-up_display, goggles"],
            "provenance": [{"canonical_tags": ["heads-up_display"], "literal_fallbacks": []}],
        }
        leaves, _, _ = GENERATOR.validate_category_response(
            "superhero_gear", response, 1, "gkr_hero", [], {"heads-up_display"}
        )
        self.assertEqual(leaves, ["heads-up_display, goggles"])

    def test_parenthesized_qualifier_is_part_of_canonical_tag(self):
        response = {
            "leaves": ["crowd, subway, western_comics_(style), comic"],
            "provenance": [{
                "canonical_tags": ["western_comics_(style)"], "literal_fallbacks": []
            }],
        }
        leaves, _, _ = GENERATOR.validate_category_response(
            "spotlight_us_comics", response, 1, "gkr_comics", [],
            {"western_comics_(style)"},
        )
        self.assertEqual(leaves, ["crowd, subway, western_comics_(style), comic"])
        self.assertEqual(
            GENERATOR.UNDERSCORE_TAG_RE.findall("portal_(object), mercury_(element)"),
            ["portal_(object)", "mercury_(element)"],
        )

    def test_apostrophe_is_part_of_canonical_tag(self):
        response = {
            "leaves": ["close-up, (brushing_another's_hair:1.1), affectionate, caress"],
            "provenance": [{
                "canonical_tags": ["brushing_another's_hair"], "literal_fallbacks": []
            }],
        }
        leaves, _, _ = GENERATOR.validate_category_response(
            "romance_scene", response, 1, "gkr_comics", [],
            {"brushing_another's_hair"},
        )
        self.assertEqual(len(leaves), 1)
        self.assertEqual(
            GENERATOR.UNDERSCORE_TAG_RE.findall(
                "brushing_another's_hair, (brushing_another's_hair:1.1)"
            ),
            ["brushing_another's_hair", "brushing_another's_hair"],
        )

    def test_generated_category_must_use_every_declared_dependency(self):
        response = {
            "leaves": ["street, dynamic_pose"],
            "provenance": [{"canonical_tags": ["street", "dynamic_pose"], "literal_fallbacks": []}],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError,
            "does not use declared dependencies: hero_action, superhero_combo",
        ):
            GENERATOR.validate_category_response(
                "superhero_scene", response, 1, "gkr_hero",
                ["superhero_combo", "hero_action"], {"street", "dynamic_pose"},
            )

        leaves, provenance, _ = GENERATOR.validate_category_response(
            "superhero_scene", response, 1, "gkr_hero",
            ["superhero_combo", "hero_action"], {"street", "dynamic_pose"},
            require_dependencies=False,
        )
        self.assertEqual(leaves, ["street, dynamic_pose"])
        self.assertEqual(len(provenance), 1)

    def test_generated_category_rejects_normalized_duplicate_leaves(self):
        response = {
            "leaves": ["muscles, (dynamic_pose:1.2)", "dynamic pose, muscles"],
            "provenance": [
                {"canonical_tags": ["muscles", "dynamic_pose"], "literal_fallbacks": []},
                {"canonical_tags": ["muscles", "dynamic_pose"], "literal_fallbacks": []},
            ],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError, "normalized duplicate leaves at positions 1 and 2"
        ):
            GENERATOR.validate_category_response(
                "hero_action", response, 2, "gkr_hero", [], {"muscles", "dynamic_pose"}
            )

    def test_small_component_pool_limits_repeated_lead_motifs(self):
        leaves = [
            "pith_helmet, khaki safari suit",
            "pith_helmet, suit, rugged explorer",
            "pith_helmet, combat_helmet, explorer",
            "fedora, trench_coat, detective",
            "aviator_cap, flight_goggles, pilot",
            "hooded_cloak, jewelry, mage",
        ]
        response = {
            "leaves": leaves,
            "provenance": [
                {"canonical_tags": [], "literal_fallbacks": []} for _ in leaves
            ],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError,
            "repeats a lead motif more than 1 times: pith_helmet at positions 1, 2, 3",
        ):
            GENERATOR.validate_category_response(
                "eu_character_archetypes", response, 6, "gkr_comics", [],
                {"pith_helmet", "combat_helmet", "trench_coat", "aviator_cap",
                 "flight_goggles", "hooded_cloak"},
                max_lead_motif_repeats=1,
            )

    def test_component_lead_motif_cannot_repeat_from_prior_chunk(self):
        response = {
            "leaves": ["pith_helmet, combat_helmet, explorer"],
            "provenance": [{"canonical_tags": ["pith_helmet", "combat_helmet"],
                            "literal_fallbacks": []}],
        }
        with self.assertRaisesRegex(
            GENERATOR.CategoryValidationError,
            "reuses lead motifs from earlier chunks: pith_helmet at positions 1",
        ):
            GENERATOR.validate_category_response(
                "eu_character_archetypes", response, 1, "gkr_comics", [],
                {"pith_helmet", "combat_helmet"}, max_lead_motif_repeats=1,
                forbidden_lead_motifs={"pith_helmet"},
            )

    def test_excess_leaves_are_trimmed_with_aligned_provenance(self):
        response = {
            "leaves": ["first", "second", "excess"],
            "provenance": [
                {"canonical_tags": ["first"], "literal_fallbacks": []},
                {"canonical_tags": ["second"], "literal_fallbacks": []},
            ],
        }
        trimmed, removed = GENERATOR.trim_excess_category_response(response, 2)
        self.assertEqual(trimmed["leaves"], ["first", "second"])
        self.assertEqual(len(trimmed["provenance"]), 2)
        self.assertEqual(removed, ["excess"])
        self.assertEqual(response["leaves"], ["first", "second", "excess"])

    def test_generated_category_can_use_dependencies_across_different_leaves(self):
        response = {
            "leaves": [
                "__gkr_hero/superhero_combo__, street",
                "__gkr_hero/hero_action__, rooftop",
            ],
            "provenance": [
                {"canonical_tags": ["street"], "literal_fallbacks": []},
                {"canonical_tags": ["rooftop"], "literal_fallbacks": []},
            ],
        }
        leaves, _, _ = GENERATOR.validate_category_response(
            "superhero_scene", response, 2, "gkr_hero",
            ["superhero_combo", "hero_action"], {"street", "rooftop"},
        )
        self.assertEqual(len(leaves), 2)

    def test_interactive_override_can_accept_or_replace_invalid_tags(self):
        response = {
            "leaves": ["mask, heads-up_display"],
            "provenance": [{
                "canonical_tags": ["mask", "heads-up_display"], "literal_fallbacks": []
            }],
        }
        answers = iter(["face_mask", ""])
        prompts = []
        def answer(prompt):
            prompts.append(prompt)
            return next(answers)
        corrected, replacements = GENERATOR.apply_interactive_tag_overrides(
            "superhero_gear", response, {"mask", "heads-up_display"}, answer
        )
        self.assertEqual(replacements, {"heads-up_display": "face_mask", "mask": "mask"})
        self.assertEqual(corrected["leaves"], ["mask, face_mask"])
        self.assertEqual(corrected["provenance"][0]["canonical_tags"], ["mask", "face_mask"])
        self.assertIn("Leaf 1: mask, heads-up_display", prompts[0])
        self.assertIn("category 'superhero_gear'", prompts[0])

    def test_interactive_override_shows_every_affected_leaf(self):
        response = {
            "leaves": ["mask, city", "mask, rooftop", "unrelated"],
            "provenance": [
                {"canonical_tags": ["mask"], "literal_fallbacks": []},
                {"canonical_tags": ["mask"], "literal_fallbacks": []},
                {"canonical_tags": [], "literal_fallbacks": ["unrelated"]},
            ],
        }
        prompts = []
        GENERATOR.apply_interactive_tag_overrides(
            "villain", response, {"mask"}, lambda prompt: prompts.append(prompt) or "face_mask"
        )
        self.assertIn("Leaf 1: mask, city", prompts[0])
        self.assertIn("Leaf 2: mask, rooftop", prompts[0])
        self.assertNotIn("Leaf 3", prompts[0])

    def test_interactive_bare_tag_does_not_match_parenthesized_qualifier(self):
        response = {
            "leaves": [
                "mole_(animal), bone, red clay",
                "toad_(animal), yellow_skin, ashes",
                "animal, translucent, glowing_petals",
            ],
            "provenance": [
                {"canonical_tags": ["mole_(animal)", "bone"], "literal_fallbacks": []},
                {"canonical_tags": ["toad_(animal)", "yellow_skin"], "literal_fallbacks": []},
                {"canonical_tags": ["animal", "translucent"], "literal_fallbacks": []},
            ],
        }
        prompts = []
        corrected, replacements = GENERATOR.apply_interactive_tag_overrides(
            "creature_spotlight", response, {"animal"},
            lambda prompt: prompts.append(prompt) or "lynx",
        )
        self.assertEqual(replacements, {"animal": "lynx"})
        self.assertNotIn("Leaf 1", prompts[0])
        self.assertNotIn("Leaf 2", prompts[0])
        self.assertIn("Leaf 3: animal, translucent, glowing_petals", prompts[0])
        self.assertEqual(corrected["leaves"][0], "mole_(animal), bone, red clay")
        self.assertEqual(corrected["leaves"][1], "toad_(animal), yellow_skin, ashes")
        self.assertEqual(corrected["leaves"][2], "lynx, translucent, glowing_petals")

    def test_existing_interactive_override_is_reused_without_prompt(self):
        response = {
            "leaves": ["incoming_mail, envelope"],
            "provenance": [{"canonical_tags": ["incoming_mail"], "literal_fallbacks": []}],
        }
        corrected, replacements = GENERATOR.apply_interactive_tag_overrides(
            "everyday_archetype", response, {"incoming_mail"},
            lambda _: self.fail("saved override should not prompt"),
            existing={"incoming_mail": "mail"},
        )
        self.assertEqual(replacements, {"incoming_mail": "mail"})
        self.assertEqual(corrected["leaves"], ["mail, envelope"])

    def test_session_persists_interactive_overrides_immediately(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "gkr-test.yaml"
        session_args = SimpleNamespace(
            output=output, interactive_overrides=None, model="test-model",
            base_url="http://localhost:11434/v1", api_key_env="PATH",
        )
        first = GENERATOR.Session(session_args, None)
        first.interactive_overrides = {"everyday_archetype": {"incoming_mail": "mail"}}
        first.save_interactive_overrides()
        second = GENERATOR.Session(session_args, None)
        self.assertEqual(second.interactive_overrides, first.interactive_overrides)
        self.assertEqual(
            second.interactive_override_path,
            output.resolve().with_suffix("").with_name("gkr-test.interactive-overrides.json"),
        )


if __name__ == "__main__":
    unittest.main()
