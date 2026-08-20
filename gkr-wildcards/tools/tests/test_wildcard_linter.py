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


if __name__ == "__main__":
    unittest.main()
