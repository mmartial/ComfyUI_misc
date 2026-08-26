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
                {"name": "spotlight", "kind": "spotlight", "dependencies": []},
                {"name": "random", "kind": "router", "dependencies": []},
            ]
        }
        plan = GENERATOR.parse_plan(result, skeleton, args())
        by_name = {item.name: item for item in plan}
        self.assertEqual(by_name["archetype"].count, 30)
        self.assertEqual(by_name["spotlight"].count, 50)
        self.assertEqual(by_name["random"].dependencies, ["spotlight"])

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


if __name__ == "__main__":
    unittest.main()
