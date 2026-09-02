from __future__ import annotations

import importlib.util
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "download_danbooru_tags.py"
SPEC = importlib.util.spec_from_file_location("download_danbooru_tags", MODULE_PATH)
assert SPEC and SPEC.loader
DOWNLOADER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DOWNLOADER
SPEC.loader.exec_module(DOWNLOADER)


class DownloadDanbooruTagsTests(unittest.TestCase):
    def test_collects_pages_and_stops_at_threshold(self) -> None:
        args = DOWNLOADER.parse_args(["--page-size", "2", "--min-post-count", "100", "--delay", "0"])
        pages = {
            1: [
                {"name": "holding_book", "post_count": 500, "category": 0, "is_deprecated": False},
                {"name": "red_hair", "post_count": 300, "category": 0, "is_deprecated": False},
            ],
            2: [
                {"name": "old_tag", "post_count": 150, "category": 0, "is_deprecated": True},
                {"name": "rare_tag", "post_count": 99, "category": 0, "is_deprecated": False},
            ],
        }
        with patch.object(DOWNLOADER, "fetch_page", side_effect=lambda _args, page: pages[page]) as fetch:
            tags = DOWNLOADER.collect_donmai_tags(args)
        self.assertEqual([tag["name"] for tag in tags], ["holding_book", "red_hair"])
        self.assertEqual(fetch.call_count, 2)

    def test_auto_falls_back_to_safebooru_org(self) -> None:
        args = DOWNLOADER.parse_args(["--min-post-count", "100", "--delay", "0"])
        fallback = [{"name": "holding_book", "post_count": 500}]
        with patch.object(DOWNLOADER, "collect_huggingface_tags", side_effect=RuntimeError("offline")):
            with patch.object(DOWNLOADER, "fetch_page", side_effect=RuntimeError("connection reset")):
                with patch.object(DOWNLOADER, "collect_safebooru_org_tags", return_value=fallback):
                    tags, source = DOWNLOADER.collect_tags(args)
        self.assertEqual(tags, fallback)
        self.assertEqual(source, "safebooru-org")

    def test_huggingface_csv_is_verified_filtered_and_normalized(self) -> None:
        args = DOWNLOADER.parse_args(["--source", "huggingface", "--min-post-count", "100"])
        payload = (
            b"tag,category,count,alias\n"
            b"holding_book,0,500,\n"
            b"rare_tag,0,99,\n"
            b"some_character,4,900,\n"
        )
        response = unittest.mock.MagicMock()
        response.read.return_value = payload
        response.__enter__.return_value = response
        with patch.object(DOWNLOADER, "HUGGINGFACE_SHA256", hashlib.sha256(payload).hexdigest()):
            with patch.object(DOWNLOADER.urllib.request, "urlopen", return_value=response):
                tags = DOWNLOADER.collect_huggingface_tags(args)
        self.assertEqual(tags, [{"name": "holding_book", "post_count": 500, "alias": ""}])

    def test_huggingface_checksum_mismatch_is_rejected(self) -> None:
        args = DOWNLOADER.parse_args(["--source", "huggingface"])
        response = unittest.mock.MagicMock()
        response.read.return_value = b"unexpected"
        response.__enter__.return_value = response
        with patch.object(DOWNLOADER.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                DOWNLOADER.collect_huggingface_tags(args)

    def test_parses_safebooru_org_xml(self) -> None:
        args = DOWNLOADER.parse_args([])
        response = unittest.mock.MagicMock()
        response.read.return_value = (
            b'<tags type="array"><tag type="0" count="500" name="holding_book" '
            b'ambiguous="false" id="1"/></tags>'
        )
        response.__enter__.return_value = response
        with patch.object(DOWNLOADER.urllib.request, "urlopen", return_value=response):
            records = DOWNLOADER.fetch_safebooru_org_page(args, 0)
        self.assertEqual(records[0]["name"], "holding_book")
        self.assertEqual(records[0]["category"], 0)

    def test_atomic_outputs_are_compatible_with_linter_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "tags.csv"
            json_path = Path(temporary) / "tags.metadata.json"
            DOWNLOADER.atomic_write_csv(csv_path, [{"name": "holding_book", "post_count": 500}])
            DOWNLOADER.atomic_write_json(json_path, {"tag_count": 1})
            self.assertEqual(csv_path.read_text(encoding="utf-8").splitlines(), ["name,post_count", "holding_book,500"])
            self.assertIn('"tag_count": 1', json_path.read_text(encoding="utf-8"))

    def test_rejects_invalid_page_size(self) -> None:
        with self.assertRaises(SystemExit):
            DOWNLOADER.parse_args(["--page-size", "1001"])


if __name__ == "__main__":
    unittest.main()
