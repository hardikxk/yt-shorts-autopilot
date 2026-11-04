from __future__ import annotations

import unittest

from app.metadata import (
    MAX_TITLE,
    build_short_title,
    build_tags,
    build_upload_metadata,
    clean_title,
    wrap_title,
)


class MetadataTests(unittest.TestCase):
    def test_title_is_short_enough_and_has_shorts_hashtag(self) -> None:
        title = build_short_title("A very long " + ("word " * 80) + "title", 2, 4)
        self.assertLessEqual(len(title), MAX_TITLE)
        self.assertIn("#Shorts", title)
        self.assertIn("Part 2", title)
        self.assertNotIn("  ", title)

    def test_clean_title_removes_existing_hashtag(self) -> None:
        self.assertEqual(clean_title("  Hello   #Shorts world  "), "Hello world")

    def test_wrap_title_limits_lines(self) -> None:
        wrapped = wrap_title("one two three four five six seven eight nine ten", width=10, max_lines=2)
        self.assertEqual(len(wrapped.splitlines()), 2)

    def test_description_includes_source_and_attribution(self) -> None:
        metadata = build_upload_metadata(
            source_title="Demo",
            source_url="https://www.youtube.com/watch?v=abc123",
            channel_title="Demo Channel",
            license_name="creativeCommon",
            index=1,
            total=3,
            category_id="24",
            privacy="private",
            made_for_kids=False,
        )
        description = metadata["snippet"]["description"]
        self.assertIn("https://www.youtube.com/watch?v=abc123", description)
        self.assertIn("Creative Commons Attribution", description)
        self.assertIn("#Shorts", description)
        self.assertEqual(metadata["status"]["privacyStatus"], "private")
        self.assertFalse(metadata["status"]["selfDeclaredMadeForKids"])

    def test_tags_respect_total_budget(self) -> None:
        tags = build_tags("word " * 200, "A Channel")
        self.assertLessEqual(sum(len(tag) + 1 for tag in tags), 500)
        self.assertTrue(tags)


if __name__ == "__main__":
    unittest.main()
