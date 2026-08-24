#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML>=6.0.2"]
# ///

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "wildcard_linter.py"
SPEC = importlib.util.spec_from_file_location("wildcard_linter", MODULE_PATH)
assert SPEC and SPEC.loader
LINTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LINTER
SPEC.loader.exec_module(LINTER)


class WildcardLinterTests(unittest.TestCase):
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

    def test_missing_mode_defaults_to_narrative(self):
        leaves, _, _ = self.inventory("gkr_test:\n  subject:\n    - red coat\n")
        self.assertEqual(leaves[0].mode, "narrative")

    def test_tags_mode_rejects_sequential_format(self):
        leaves, _, _ = self.inventory(
            "# MODE: tags\ngkr_test:\n  action_scene:\n    - four-panel sequence, changing face\n"
        )
        findings = LINTER.tags_mode_findings(leaves, self.tags_rules)
        self.assertIn("tags_sequential_format", {finding.rule for finding in findings})

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
