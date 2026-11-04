from __future__ import annotations

import unittest

import numpy as np

from app.segments import select_segments


class SegmentTests(unittest.TestCase):
    def test_selects_high_energy_non_overlapping_windows(self) -> None:
        energy = np.zeros(300)
        energy[18:28] = 1.0
        energy[90:102] = 0.8
        energy[180:195] = 0.9
        segments = select_segments(300, energy, 3, 45, gap_seconds=2)

        self.assertEqual(len(segments), 3)
        self.assertEqual([item["index"] for item in segments], [1, 2, 3])
        self.assertEqual([item["startSeconds"] for item in segments], sorted(item["startSeconds"] for item in segments))
        for earlier, later in zip(segments, segments[1:]):
            self.assertGreaterEqual(
                later["startSeconds"] - earlier["startSeconds"],
                45,
            )
        for segment in segments:
            self.assertGreaterEqual(segment["startSeconds"], 0)
            self.assertLessEqual(
                segment["startSeconds"] + segment["durationSeconds"],
                300.5,
            )

    def test_no_audio_distributes_evenly(self) -> None:
        segments = select_segments(180, [], 3, 45, gap_seconds=2)
        self.assertEqual(len(segments), 3)
        self.assertEqual(len({item["startSeconds"] for item in segments}), 3)
        for segment in segments:
            self.assertLessEqual(
                segment["startSeconds"] + segment["durationSeconds"],
                180.1,
            )

    def test_rejects_source_too_short_for_count(self) -> None:
        with self.assertRaises(ValueError):
            select_segments(120, np.ones(120), 3, 45, gap_seconds=2)

    def test_rejects_bad_input(self) -> None:
        with self.assertRaises(ValueError):
            select_segments(180, [], 0, 45)
        with self.assertRaises(ValueError):
            select_segments(0, [], 3, 45)
        with self.assertRaises(ValueError):
            select_segments(30, [], 1, 45)

    def test_single_segment(self) -> None:
        segments = select_segments(180, [], 1, 45)
        self.assertEqual(len(segments), 1)
        self.assertGreaterEqual(segments[0]["startSeconds"], 0)


if __name__ == "__main__":
    unittest.main()
