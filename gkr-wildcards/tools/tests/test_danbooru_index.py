#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "danbooru_index.py"
SPEC = importlib.util.spec_from_file_location("danbooru_index", MODULE_PATH)
assert SPEC and SPEC.loader
INDEX = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = INDEX
SPEC.loader.exec_module(INDEX)


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode()


class DanbooruIndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.csv = root / "tags.csv"
        self.sqlite = root / "tags.index.sqlite"
        self.csv.write_text(
            "name,post_count,alias\n"
            "superhero_landing,5000,three_point_landing|hero_landing\n"
            "night_vision_device,4000,night_vision_goggles\n"
            "carrying_person,3000,\n"
            "long_hair,9000,\n",
            encoding="utf-8",
        )
        INDEX.build_index(self.csv, self.sqlite)

    def test_exact_alias_and_lexical_retrieval(self):
        index = INDEX.DanbooruIndex(self.sqlite)
        self.addCleanup(index.close)
        self.assertEqual(index.lexical_search("superhero landing")[0].tag, "superhero_landing")
        alias = index.lexical_search("three point landing")[0]
        self.assertEqual((alias.tag, alias.match), ("superhero_landing", "alias"))
        self.assertIn("night_vision_device", {item.tag for item in index.lexical_search("night vision goggles")})

    def test_index_checksum_detects_changed_csv(self):
        index = INDEX.DanbooruIndex(self.sqlite)
        self.addCleanup(index.close)
        self.assertTrue(index.compatible_with_csv(self.csv))
        self.csv.write_text(self.csv.read_text() + "flying,1000,\n", encoding="utf-8")
        self.assertFalse(index.compatible_with_csv(self.csv))

    def test_general_profile_requires_classification_and_filters_rows(self):
        with self.assertRaisesRegex(ValueError, "requires a CSV with content_class"):
            INDEX.build_index(self.csv, Path(self.temporary.name) / "general.sqlite", "general")
        classified = Path(self.temporary.name) / "classified.csv"
        classified.write_text(
            "name,post_count,content_class\n"
            "hero,100,general\n"
            "mature_theme,50,sensitive\n"
            "unclear,1,ambiguous\n",
            encoding="utf-8",
        )
        path = Path(self.temporary.name) / "filtered.sqlite"
        info = INDEX.build_index(classified, path, "general")
        self.assertEqual((info["tag_count"], info["excluded_tag_count"]), (1, 2))
        index = INDEX.DanbooruIndex(path)
        self.addCleanup(index.close)
        self.assertTrue(index.contains_tag("hero"))
        self.assertFalse(index.contains_tag("mature_theme"))

    def test_embedding_client_accepts_array_and_restores_index_order(self):
        response = {
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        }
        client = INDEX.EmbeddingClient("http://localhost:11434/v1", "embeddinggemma", "ollama")
        with patch("urllib.request.urlopen", return_value=FakeResponse(response)) as opened:
            vectors = client.embed(["first", "second"])
        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["input"], ["first", "second"])
        self.assertEqual(request.full_url, "http://localhost:11434/v1/embeddings")

    def test_hybrid_retrieval_uses_stored_vectors(self):
        INDEX.write_embeddings(
            self.sqlite,
            {0: [1.0, 0.0], 1: [0.0, 1.0], 2: [0.7, 0.7], 3: [-1.0, 0.0]},
            {"embedding_model": "test", "embedding_base_url": "http://test/v1"},
        )
        index = INDEX.DanbooruIndex(self.sqlite)
        self.addCleanup(index.close)
        results = index.hybrid_search("unrelated wording", [1.0, 0.0], 2)
        self.assertEqual(results[0].tag, "superhero_landing")


if __name__ == "__main__":
    unittest.main()
