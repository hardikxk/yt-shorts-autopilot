from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _which(value: str) -> str:
    if os.path.isabs(value) and Path(value).exists():
        return value
    found = shutil.which(value)
    return found or value


def _find_font() -> str:
    configured = os.getenv("FONT_FILE", "").strip()
    if configured and Path(configured).exists():
        return configured
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return candidates[0]


@dataclass(frozen=True)
class Settings:
    work_root: Path
    ffmpeg: str
    ffprobe: str
    font_file: str
    keep_workdir: bool
    ytdlp_cookies: str | None
    ytdlp_extra_args: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        work_root = Path(os.getenv("WORK_DIR", "/tmp/yt-shorts")).expanduser()
        work_root.mkdir(parents=True, exist_ok=True)
        cookies = os.getenv("YTDLP_COOKIES_FILE", "").strip() or None
        extra = tuple(
            part
            for part in os.getenv("YTDLP_EXTRA_ARGS", "").split("\x1f")
            if part.strip()
        )
        return cls(
            work_root=work_root,
            ffmpeg=_which(os.getenv("FFMPEG_BIN", "ffmpeg")),
            ffprobe=_which(os.getenv("FFPROBE_BIN", "ffprobe")),
            font_file=_find_font(),
            keep_workdir=os.getenv("KEEP_WORKDIR", "false").lower()
            in {"1", "true", "yes", "on"},
            ytdlp_cookies=cookies,
            ytdlp_extra_args=extra,
        )


settings = Settings.from_env()
