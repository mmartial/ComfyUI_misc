#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML>=6.0.2"]
# ///

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


TOOLS = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_module("wildcard_linter", TOOLS / "wildcard_linter.py")
load_module("danbooru_index", TOOLS / "danbooru_index.py")
GENERATOR = load_module("wildcard_generator", TOOLS / "wildcard_generator.py")


def args(**overrides):
    values = {"max_added_categories": 30, "max_category_depth": 6}
    values.update(overrides)
    return SimpleNamespace(**values)


class WildcardGeneratorTests(unittest.TestCase):
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
        leaves, _ = GENERATOR.validate_category_response(
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
        leaves, _ = GENERATOR.validate_category_response(
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
        leaves, _ = GENERATOR.validate_category_response(
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

        leaves, provenance = GENERATOR.validate_category_response(
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
        leaves, _ = GENERATOR.validate_category_response(
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
