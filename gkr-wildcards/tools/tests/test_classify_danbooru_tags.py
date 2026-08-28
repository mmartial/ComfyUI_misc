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
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "classify_danbooru_tags.py"
SPEC = importlib.util.spec_from_file_location("classify_danbooru_tags", MODULE_PATH)
assert SPEC and SPEC.loader
CLASSIFIER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLASSIFIER
SPEC.loader.exec_module(CLASSIFIER)


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": json.dumps(self.content)}}]}).encode()


class ClassifierTests(unittest.TestCase):
    def test_overrides_support_simple_and_structured_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "overrides.yaml"
            path.write_text(
                "overrides:\n  ordinary_tag: general\n  reviewed_tag:\n    class: sensitive\n",
                encoding="utf-8",
            )
            self.assertEqual(
                CLASSIFIER.load_overrides(path),
                {"ordinary_tag": "general", "reviewed_tag": "sensitive"},
            )

    def test_openai_compatible_batch_response(self):
        response = [
            {"id": "0", "content_class": "general", "confidence": 0.9, "reason_code": "ordinary"},
            {"id": "1", "content_class": "ambiguous", "confidence": 0.4, "reason_code": "unclear"},
        ]
        with patch("urllib.request.urlopen", return_value=FakeResponse(response)) as opened:
            result = CLASSIFIER.request_batch(
                "http://localhost:11434/v1/chat/completions", "ollama", "qwen3:8b",
                [{"id": "0", "tag": "hero"}, {"id": "1", "tag": "opaque_term"}], 30,
            )
        self.assertEqual(result, response)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["model"], "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
