from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .config import Settings
from .errors import PipelineError
from .metadata import build_short_title, wrap_title
from .segments import select_segments


def _tail(value: str, limit: int = 1800) -> str:
    return value[-limit:].strip()


def run_command(args: Sequence[str], *, timeout: int, label: str) -> str:
    try:
        result = subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PipelineError(f"{label} timed out after {timeout}s") from error
    if result.returncode != 0:
        raise PipelineError(
            f"{label} failed (exit {result.returncode}): {_tail(result.stderr or result.stdout)}"
        )
    return result.stdout


def probe_media(path: Path, settings: Settings) -> dict[str, Any]:
    output = run_command(
        [
            settings.ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
        label="ffprobe",
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise PipelineError("ffprobe returned invalid JSON") from error

    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        raise PipelineError("Source has no video stream")
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        duration = float(video_stream.get("duration") or 0)
    if duration <= 0:
        raise PipelineError("Could not determine source duration")
    return {
        "duration": duration,
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "hasAudio": any(stream.get("codec_type") == "audio" for stream in streams),
        "videoCodec": str(video_stream.get("codec_name") or ""),
    }


def download_source(
    video_id: str,
    work_dir: Path,
    settings: Settings,
    cookies_file: Path | None = None,
) -> Path:
    target = work_dir / "source.%(ext)s"
    url = f"https://www.youtube.com/watch?v={video_id}"
    args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--restrict-filenames",
        "--merge-output-format",
        "mp4",
        "--format",
        "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best[height<=1080]",
        "--output",
        str(target),
        "--print",
        "after_move:filepath",
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "--fragment-retries",
        "5",
        "--extractor-retries",
        "3",
        "--max-filesize",
        "2G",
    ]
    if cookies_file is not None:
        args.extend(["--cookies", str(cookies_file)])
    elif settings.ytdlp_cookies:
        args.extend(["--cookies", settings.ytdlp_cookies])
    pot_provider = os.getenv("YTDLP_POT_PROVIDER_URL", "").strip()
    if pot_provider:
        args.extend(
            [
                "--extractor-args",
                f"youtubepot-bgutilhttp:base_url={pot_provider}",
            ]
        )
    args.extend(settings.ytdlp_extra_args)
    args.append(url)

    output = run_command(args, timeout=1800, label="yt-dlp download")
    candidates: list[Path] = []
    for line in reversed(output.splitlines()):
        candidate = Path(line.strip())
        if candidate.is_file():
            candidates.append(candidate)
    candidates.extend(sorted(work_dir.glob("source.*")))
    for candidate in candidates:
        if candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}:
            if candidate.stat().st_size > 0:
                return candidate
    raise PipelineError(f"yt-dlp finished but no downloaded file was found.\n{_tail(output)}")


def extract_energy(path: Path, settings: Settings, sample_rate: int = 8000) -> np.ndarray:
    command = [
        settings.ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PipelineError("Audio extraction timed out") from error
    if result.returncode != 0 or not result.stdout:
        return np.array([], dtype=np.float64)

    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    window = max(1, sample_rate)
    usable = (samples.size // window) * window
    if usable == 0:
        return np.array([], dtype=np.float64)
    frames = samples[:usable].reshape(-1, window)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    return np.nan_to_num(rms.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)


def _filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _build_filter(
    title_file: Path,
    settings: Settings,
    duration: float,
    has_audio: bool,
) -> tuple[str, str]:
    fade_out = max(0.0, duration - 0.35)
    title_path = _filter_path(str(title_file))
    font_path = _filter_path(settings.font_file)
    video_filter = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=30:2,eq=brightness=-0.12:saturation=0.9[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
        f"[base]drawtext=fontfile='{font_path}':textfile='{title_path}':expansion=none:"
        "fontcolor=white:fontsize=46:line_spacing=10:box=1:boxcolor=black@0.55:"
        "boxborderw=22:x=(w-text_w)/2:y=140,"
        f"fade=t=in:st=0:d=0.3,fade=t=out:st={fade_out:.3f}:d=0.35[v]"
    )
    if has_audio:
        audio_filter = (
            f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,"
            f"afade=t=in:st=0:d=0.25,afade=t=out:st={fade_out:.3f}:d=0.35[a]"
        )
    else:
        audio_filter = "[1:a]anull[a]"
    return f"{video_filter};{audio_filter}", "v"


def render_short(
    *,
    source: Path,
    work_dir: Path,
    start_seconds: float,
    duration: float,
    title: str,
    index: int,
    has_audio: bool,
    settings: Settings,
) -> Path:
    title_file = work_dir / f"title-{index:02d}.txt"
    title_file.write_text(wrap_title(title), encoding="utf-8")
    output = work_dir / f"short-{index:02d}.mp4"
    filter_complex, video_label = _build_filter(title_file, settings, duration, has_audio)

    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(source),
    ]
    if not has_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )
    command.extend(
        [
            "-t",
            f"{duration:.3f}",
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{video_label}]",
            "-map",
            "[a]",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-maxrate",
            "6M",
            "-bufsize",
            "12M",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "1024",
            "-threads",
            "0",
            str(output),
        ]
    )
    run_command(command, timeout=900, label=f"ffmpeg render short {index}")
    if not output.exists() or output.stat().st_size == 0:
        raise PipelineError(f"ffmpeg produced no output for short {index}")
    return output


def validate_short(path: Path, settings: Settings, max_bytes: int) -> dict[str, Any]:
    size = path.stat().st_size
    if size <= 0:
        raise PipelineError(f"{path.name} is empty")
    if size > max_bytes:
        raise PipelineError(
            f"{path.name} is {size / 1024 / 1024:.1f} MiB, above the {max_bytes / 1024 / 1024:.1f} MiB upload limit"
        )
    info = probe_media(path, settings)
    if abs(info["width"] - 1080) > 2 or abs(info["height"] - 1920) > 2:
        raise PipelineError(
            f"{path.name} is {info['width']}x{info['height']}, expected 1080x1920"
        )
    if info["duration"] > 181:
        raise PipelineError(f"{path.name} is {info['duration']:.1f}s, above YouTube's 3 minute Shorts limit")
    return {"bytes": size, **info}


def render_all(
    *,
    source: Path,
    source_info: dict[str, Any],
    work_dir: Path,
    segments: list[dict[str, float]],
    source_title: str,
    settings: Settings,
    max_bytes: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    total = len(segments)
    for segment in segments:
        index = int(segment["index"])
        title = build_short_title(source_title, index, total)
        path = render_short(
            source=source,
            work_dir=work_dir,
            start_seconds=float(segment["startSeconds"]),
            duration=float(segment["durationSeconds"]),
            title=title,
            index=index,
            has_audio=bool(source_info.get("hasAudio")),
            settings=settings,
        )
        validation = validate_short(path, settings, max_bytes)
        output.append(
            {
                "index": index,
                "startSeconds": float(segment["startSeconds"]),
                "durationSeconds": float(segment["durationSeconds"]),
                "title": title,
                "status": "rendered",
                "path": str(path),
                "bytes": validation["bytes"],
            }
        )
    return output


def choose_segments(
    *,
    duration: float,
    energy: np.ndarray,
    count: int,
    short_seconds: float,
    gap_seconds: float,
) -> list[dict[str, float]]:
    return select_segments(
        duration=duration,
        energy=energy,
        count=count,
        segment_seconds=short_seconds,
        gap_seconds=gap_seconds,
        intro_skip=min(3.0, max(1.0, duration * 0.01)),
        outro_skip=min(3.0, max(1.0, duration * 0.01)),
    )
