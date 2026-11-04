"""Local ffmpeg smoke test. Does not download or upload anything.

Creates a synthetic 30s source, renders one vertical short with audio and one
without audio, then probes both outputs. Run from the repo root:

    python scripts/smoke_render.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "media"))

from app.config import Settings  # noqa: E402
from app.media import probe_media, render_short, validate_short  # noqa: E402


def run(command: list[str]) -> None:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if process.returncode != 0:
        raise SystemExit(process.stderr[-3000:])


def main() -> int:
    settings = Settings.from_env()
    out = ROOT / "smoke-output"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    source = out / "source.mp4"
    run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(source),
        ]
    )
    source_info = probe_media(source, settings)
    print("source:", source_info)
    if not source_info["hasAudio"]:
        raise SystemExit("synthetic source unexpectedly has no audio")

    with_audio = render_short(
        source=source,
        work_dir=out,
        start_seconds=2,
        duration=10,
        title="Smoke Test: vertical render with audio",
        index=1,
        has_audio=True,
        settings=settings,
    )
    print("with audio:", validate_short(with_audio, settings, 94_371_840))

    silent_source = out / "source-silent.mp4"
    run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-an",
            "-c",
            "copy",
            str(silent_source),
        ]
    )
    silent = render_short(
        source=silent_source,
        work_dir=out,
        start_seconds=5,
        duration=8,
        title="Smoke Test: vertical render without audio",
        index=2,
        has_audio=False,
        settings=settings,
    )
    print("without audio:", validate_short(silent, settings, 94_371_840))
    print("ok:", with_audio, silent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
