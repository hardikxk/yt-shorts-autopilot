from __future__ import annotations

from typing import Sequence

import numpy as np


def _smooth(values: np.ndarray, window: int = 3) -> np.ndarray:
    if values.size == 0:
        return values
    window = max(1, min(window, values.size))
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(values, kernel, mode="same")


def _score_windows(energy: np.ndarray, starts: np.ndarray, segment_seconds: float) -> np.ndarray:
    if energy.size == 0:
        return np.zeros(starts.shape, dtype=np.float64)
    cumulative = np.concatenate(([0.0], np.cumsum(energy, dtype=np.float64)))
    scores = np.empty(starts.shape, dtype=np.float64)
    length = energy.size
    for i, start in enumerate(starts):
        begin = int(max(0, min(length - 1, round(start))))
        end = int(max(begin + 1, min(length, round(start + segment_seconds))))
        total = cumulative[end] - cumulative[begin]
        scores[i] = total / max(1, end - begin)
    return scores


def select_segments(
    duration: float,
    energy: Sequence[float] | np.ndarray,
    count: int,
    segment_seconds: float,
    gap_seconds: float = 2.0,
    intro_skip: float = 2.0,
    outro_skip: float = 2.0,
) -> list[dict[str, float]]:
    """Pick non-overlapping high-energy windows.

    ``energy`` is one RMS value per second (empty means no audio track).
    The returned list is sorted by start time so uploads are chronological.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    if duration <= 0:
        raise ValueError("duration must be positive")
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be positive")
    if segment_seconds > duration:
        raise ValueError(
            f"source is {duration:.1f}s but each short needs {segment_seconds:.1f}s"
        )

    segment = float(min(segment_seconds, duration))
    margin = min(max(0.0, intro_skip), max(0.0, duration - segment) / 2)
    outro = min(max(0.0, outro_skip), max(0.0, duration - segment) / 2)
    min_start = margin
    max_start = duration - outro - segment
    if max_start < min_start:
        min_start = 0.0
        max_start = max(0.0, duration - segment)

    starts = np.arange(min_start, max_start + 0.001, 0.5, dtype=np.float64)
    if starts.size == 0:
        starts = np.array([max(0.0, min_start)], dtype=np.float64)

    energy_array = np.asarray(list(energy), dtype=np.float64)
    if energy_array.size > 0:
        energy_array = np.nan_to_num(energy_array, nan=0.0, posinf=0.0, neginf=0.0)
        energy_array = _smooth(energy_array, 3)

        if energy_array.size < duration:
            energy_array = np.pad(energy_array, (0, int(np.ceil(duration)) - energy_array.size))
        scores = _score_windows(energy_array, starts, segment)
    else:
        scores = np.zeros(starts.shape, dtype=np.float64)

    selected: list[float] = []
    if np.any(scores > 0):
        order = np.argsort(-scores, kind="stable")
        for index in order:
            candidate = float(starts[int(index)])
            if all(abs(candidate - prior) >= segment + gap_seconds - 1e-6 for prior in selected):
                selected.append(round(candidate, 2))
            if len(selected) >= count:
                break
    else:
        # No usable audio: distribute the available timeline evenly.
        available = max_start - min_start
        if count > 1:
            step = available / (count - 1)
            if step < segment - 1e-6:
                raise ValueError(
                    f"source is too short for {count} non-overlapping {segment:.0f}s segments "
                    f"(duration {duration:.1f}s)"
                )
        else:
            step = 0.0
        for index in range(count):
            selected.append(round(min_start + step * index, 2))

    if len(selected) < count:
        raise ValueError(
            f"source is too short for {count} non-overlapping {segment:.0f}s segments "
            f"(duration {duration:.1f}s, gap {gap_seconds:.1f}s)"
        )

    selected = sorted(selected[:count])
    return [
        {
            "index": index + 1,
            "startSeconds": round(float(start), 2),
            "durationSeconds": round(segment, 2),
        }
        for index, start in enumerate(selected)
    ]
