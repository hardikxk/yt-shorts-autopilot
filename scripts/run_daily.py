#!/usr/bin/env python3
"""Daily GitHub Actions pipeline: select, download, render, upload.

Run locally with the same environment variables used by the workflow.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]


def load_local_env() -> None:
    """Load .env / .dev.vars for local runs without overriding Actions env."""
    for name in (".env", ".dev.vars"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)

    token_file = ROOT / ".secrets" / "youtube-token.json"
    if token_file.exists() and not os.getenv("YOUTUBE_REFRESH_TOKEN"):
        try:
            token_data = json.loads(token_file.read_text(encoding="utf-8"))
            if token_data.get("refresh_token"):
                os.environ["YOUTUBE_REFRESH_TOKEN"] = str(token_data["refresh_token"])
        except (OSError, json.JSONDecodeError):
            pass


load_local_env()
os.environ.setdefault("WORK_DIR", str(ROOT / ".work"))
sys.path.insert(0, str(ROOT / "media"))

from app.config import Settings  # noqa: E402
from app.errors import PipelineError  # noqa: E402
from app.media import (  # noqa: E402
    choose_segments,
    download_source,
    extract_energy,
    probe_media,
    render_all,
)
from app.metadata import build_upload_metadata  # noqa: E402
from app.youtube import upload_short  # noqa: E402

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_STATE = "state/used_videos.json"
SUMMARY_LIMIT = 500


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def load_config() -> dict[str, Any]:
    path = ROOT / "config" / "pipeline.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("source_mode", "channel")
    config.setdefault("rights_mode", "owned")
    config.setdefault("source_channel_ids", [])
    config.setdefault("region_code", "IN")
    config.setdefault("max_video_age_hours", 16)
    config.setdefault("min_source_seconds", 180)
    config.setdefault("max_source_seconds", 1800)
    config.setdefault("shorts_per_day", 4)
    config.setdefault("min_shorts", 3)
    config.setdefault("short_seconds", 45)
    config.setdefault("short_gap_seconds", 2)
    config.setdefault("upload_privacy", "private")
    config.setdefault("upload_category_id", "24")
    config.setdefault("upload_delay_seconds", 45)
    config.setdefault("made_for_kids", False)
    config.setdefault("dry_run", False)
    config.setdefault("max_upload_bytes", 94_371_840)
    config.setdefault("state_path", DEFAULT_STATE)

    if os.getenv("VIDEO_ID", "").strip():
        config["video_id"] = os.getenv("VIDEO_ID", "").strip()
    if env_bool("DRY_RUN", bool(config.get("dry_run"))):
        config["dry_run"] = True
    if env_bool("FORCE", False):
        config["force"] = True

    privacy_override = os.getenv("PRIVACY_OVERRIDE", "config").strip().lower()
    if privacy_override in {"private", "unlisted", "public"}:
        config["upload_privacy"] = privacy_override

    config["shorts_per_day"] = max(1, min(5, int(config["shorts_per_day"])))
    config["min_shorts"] = max(
        1, min(int(config["min_shorts"]), int(config["shorts_per_day"]))
    )
    config["short_seconds"] = max(10.0, min(180.0, float(config["short_seconds"])))
    config["max_video_age_hours"] = max(1, int(config["max_video_age_hours"]))
    return config


def state_file(config: dict[str, Any]) -> Path:
    path = Path(str(config.get("state_path", DEFAULT_STATE)))
    if not path.is_absolute():
        path = ROOT / path
    return path


def load_state(config: dict[str, Any]) -> dict[str, Any]:
    path = state_file(config)
    if not path.exists():
        return {"version": 1, "videos": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PipelineError(f"Could not read state file {path}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
        raise PipelineError(f"State file {path} has an invalid shape")
    return data


def save_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    path = state_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["videos"] = state["videos"][-SUMMARY_LIMIT:]
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def used_ids(state: dict[str, Any]) -> set[str]:
    return {
        str(entry.get("id"))
        for entry in state.get("videos", [])
        if entry.get("status") in {"completed", "partial"} and entry.get("id")
    }


def mark_used(
    state: dict[str, Any],
    video: dict[str, Any],
    status: str,
    shorts: list[dict[str, Any]],
) -> None:
    state["videos"].append(
        {
            "id": video["id"],
            "title": video.get("title", ""),
            "channelId": video.get("channelId", ""),
            "date": utc_now().date().isoformat(),
            "status": status,
            "shorts": [
                {
                    "index": short.get("index"),
                    "title": short.get("title"),
                    "youtubeVideoId": short.get("youtubeVideoId"),
                    "status": short.get("status"),
                }
                for short in shorts
            ],
            "updatedAt": iso_now(),
        }
    )


def refresh_access_token() -> str:
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()
    if not client_id or not client_secret or not refresh_token:
        raise PipelineError(
            "Missing YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, or YOUTUBE_REFRESH_TOKEN."
        )
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    payload = response.json() if response.content else {}
    if not response.ok or not payload.get("access_token"):
        detail = payload.get("error_description") or payload.get("error") or response.text
        raise PipelineError(f"YouTube token refresh failed ({response.status_code}): {detail}")
    return str(payload["access_token"])


def api_get(path: str, params: dict[str, str], access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{API_BASE}/{path}",
        params=params,
        headers={"authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    payload = response.json() if response.content else {}
    if not response.ok:
        message = payload.get("error", {}).get("message", response.text[:800])
        raise PipelineError(f"YouTube API {path} failed ({response.status_code}): {message}")
    return payload


def parse_duration(value: str | None) -> float:
    if not value:
        return 0.0
    import re

    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", value)
    if not match:
        return 0.0
    days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def map_video(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    content = item.get("contentDetails") or {}
    status = item.get("status") or {}
    video_id = str(item.get("id") or "")
    return {
        "id": video_id,
        "title": str(snippet.get("title") or "Untitled video"),
        "channelId": str(snippet.get("channelId") or ""),
        "channelTitle": str(snippet.get("channelTitle") or ""),
        "publishedAt": str(snippet.get("publishedAt") or ""),
        "durationSeconds": parse_duration(content.get("duration")),
        "categoryId": str(snippet.get("categoryId") or "24"),
        "license": str(status.get("license") or "youtube"),
        "privacyStatus": str(status.get("privacyStatus") or "public"),
        "uploadStatus": str(status.get("uploadStatus") or "processed"),
        "embeddable": status.get("embeddable") is not False,
        "liveBroadcastContent": str(snippet.get("liveBroadcastContent") or "none"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def fetch_videos(video_ids: list[str], access_token: str) -> list[dict[str, Any]]:
    unique = [video_id for video_id in dict.fromkeys(video_ids) if video_id]
    output: list[dict[str, Any]] = []
    for offset in range(0, len(unique), 50):
        chunk = unique[offset : offset + 50]
        payload = api_get(
            "videos",
            {
                "part": "snippet,contentDetails,status",
                "id": ",".join(chunk),
                "maxResults": "50",
            },
            access_token,
        )
        output.extend(map_video(item) for item in payload.get("items", []))
    return output


def list_channel_videos(channel_id: str, access_token: str) -> list[dict[str, Any]]:
    channel_payload = api_get(
        "channels",
        {"part": "contentDetails", "id": channel_id, "maxResults": "1"},
        access_token,
    )
    items = channel_payload.get("items") or []
    if not items:
        raise PipelineError(f"Channel not found: {channel_id}")
    uploads = (
        (items[0].get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads")
    )
    if not uploads:
        return []
    playlist_payload = api_get(
        "playlistItems",
        {"part": "contentDetails", "playlistId": uploads, "maxResults": "15"},
        access_token,
    )
    ids = [
        str((item.get("contentDetails") or {}).get("videoId") or "")
        for item in playlist_payload.get("items", [])
    ]
    return fetch_videos(ids, access_token)


def list_trending(region_code: str, access_token: str) -> list[dict[str, Any]]:
    payload = api_get(
        "videos",
        {
            "part": "snippet,contentDetails,status",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": "50",
        },
        access_token,
    )
    return [map_video(item) for item in payload.get("items", [])]


def search_recent_creative_commons(
    config: dict[str, Any], access_token: str
) -> list[dict[str, Any]]:
    published_after = (
        utc_now() - timedelta(hours=int(config["max_video_age_hours"]))
    ).isoformat().replace("+00:00", "Z")
    payload = api_get(
        "search",
        {
            "part": "snippet",
            "type": "video",
            "videoLicense": "creativeCommon",
            "order": "viewCount",
            "publishedAfter": published_after,
            "regionCode": str(config["region_code"]),
            "maxResults": "25",
        },
        access_token,
    )
    ids = [
        str(((item.get("id") or {}).get("videoId") or ""))
        for item in payload.get("items", [])
    ]
    return fetch_videos(ids, access_token)


def rights_reason(video: dict[str, Any], config: dict[str, Any]) -> str | None:
    mode = str(config["rights_mode"]).lower().replace("_", "")
    if mode in {"creativecommon", "cc"}:
        return None if video["license"] == "creativeCommon" else "not Creative Commons"
    if mode == "owned":
        own = str(config.get("own_channel_id") or "")
        if not own:
            raise PipelineError("rights_mode=owned requires own_channel_id in config")
        return None if video["channelId"] == own else "not your channel"
    if mode == "allowlist":
        allowed = {str(item) for item in config.get("source_channel_ids", [])}
        if not allowed:
            raise PipelineError("rights_mode=allowlist requires source_channel_ids")
        return None if video["channelId"] in allowed else "channel is not allowlisted"
    raise PipelineError(f"Unknown rights_mode: {config['rights_mode']}")


def select_video(
    config: dict[str, Any],
    access_token: str,
    known_used: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    if config.get("video_id"):
        candidates = fetch_videos([str(config["video_id"])], access_token)
        if not candidates:
            raise PipelineError(f"Video not found: {config['video_id']}")
    elif str(config["source_mode"]).lower() == "trending":
        candidates = list_trending(str(config["region_code"]), access_token)
    elif str(config["source_mode"]).lower() in {"search", "creative_commons"}:
        if str(config["rights_mode"]).lower().replace("_", "") not in {
            "creativecommon",
            "cc",
        }:
            raise PipelineError(
                "source_mode=search requires rights_mode=creativeCommon"
            )
        candidates = search_recent_creative_commons(config, access_token)
    elif str(config["source_mode"]).lower() == "channel":
        channel_ids = [str(item) for item in config.get("source_channel_ids", [])]
        if not channel_ids and config.get("own_channel_id"):
            channel_ids = [str(config["own_channel_id"])]
        if not channel_ids:
            raise PipelineError("source_mode=channel requires a channel ID")
        candidates = []
        for channel_id in channel_ids[:10]:
            candidates.extend(list_channel_videos(channel_id, access_token))
    else:
        raise PipelineError(f"Unknown source_mode: {config['source_mode']}")

    now = utc_now()
    max_age = timedelta(hours=int(config["max_video_age_hours"]))
    min_seconds = float(config["min_source_seconds"])
    max_seconds = float(config["max_source_seconds"])
    force = bool(config.get("force"))
    rejected: list[str] = []
    eligible: list[dict[str, Any]] = []

    for video in candidates:
        reasons: list[str] = []
        published = parse_published(video["publishedAt"])
        if published is None:
            reasons.append("missing publish time")
        elif now - published > max_age:
            reasons.append(f"older than {config['max_video_age_hours']}h")
        if video["durationSeconds"] < min_seconds:
            reasons.append(f"shorter than {min_seconds:.0f}s")
        if video["durationSeconds"] > max_seconds:
            reasons.append(f"longer than {max_seconds:.0f}s")
        if video["liveBroadcastContent"] != "none":
            reasons.append("live/upcoming")
        if video["privacyStatus"] != "public":
            reasons.append("not public")
        if not video["embeddable"]:
            reasons.append("not embeddable")
        if video["uploadStatus"] != "processed":
            reasons.append("not processed")
        if not force and video["id"] in known_used:
            reasons.append("already used")
        try:
            rights = rights_reason(video, config)
        except PipelineError:
            raise
        if rights:
            reasons.append(rights)
        if reasons:
            rejected.append(f"{video['id']} {video['title'][:60]}: {', '.join(reasons)}")
        else:
            eligible.append(video)

    if not eligible:
        return None, rejected[:20]

    if str(config["source_mode"]).lower() == "trending":
        eligible.sort(key=lambda item: item["publishedAt"], reverse=True)
    else:
        eligible.sort(key=lambda item: item["publishedAt"], reverse=True)
    return eligible[0], rejected[:20]


def send_email(subject: str, body: str) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    to = [item.strip() for item in os.getenv("ALERT_EMAIL_TO", "").split(",") if "@" in item]
    sender = os.getenv("ALERT_EMAIL_FROM", "").strip() or "onboarding@resend.dev"
    if not api_key or not to:
        print(f"[email skipped] {subject}\n{body}")
        return
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={"from": sender, "to": to, "subject": subject, "text": body},
            timeout=20,
        )
        if not response.ok:
            print(f"[email failed] Resend {response.status_code}: {response.text[:300]}")
    except requests.RequestException as error:
        print(f"[email failed] {error}")


def write_summary(markdown: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip() + "\n")


def run() -> int:
    config = load_config()
    settings = Settings.from_env()
    print("Config:", json.dumps({k: v for k, v in config.items() if k != "force"}, default=str))
    print("Mode:", "DRY RUN" if config.get("dry_run") else "UPLOAD")

    state = load_state(config)
    known_used = used_ids(state)
    access_token = refresh_access_token()

    video, rejected = select_video(config, access_token, known_used)
    if not video:
        lines = [
            "## No eligible source video",
            "",
            f"No video matched the {config['max_video_age_hours']}h / "
            f"{config['min_source_seconds']}-{config['max_source_seconds']}s / "
            f"`{config['rights_mode']}` filters.",
            "",
        ]
        if rejected:
            lines.extend(["Closest candidates:", "", "```", *rejected[:10], "```"])
        summary = "\n".join(lines)
        print(summary)
        write_summary(summary)
        send_email("[yt-shorts] No eligible source video", summary)
        return 0

    print(f"Selected: {video['id']} — {video['title']} ({video['channelTitle']})")
    work_dir = settings.work_root / f"{video['id']}-{utc_now().strftime('%Y%m%d-%H%M%S')}"
    work_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    try:
        source = download_source(video["id"], work_dir, settings)
        info = probe_media(source, settings)
        print(f"Downloaded: {source} ({info['duration']:.1f}s, {info['width']}x{info['height']})")
        if info["duration"] < config["min_source_seconds"]:
            raise PipelineError(
                f"Downloaded source is {info['duration']:.1f}s, below minimum {config['min_source_seconds']}s."
            )

        energy = extract_energy(source, settings) if info["hasAudio"] else []
        if not info["hasAudio"]:
            print("Source has no audio; rendering with silence.")
        requested = int(config["shorts_per_day"])
        minimum = int(config["min_shorts"])
        try:
            segments = choose_segments(
                duration=info["duration"],
                energy=energy,
                count=requested,
                short_seconds=float(config["short_seconds"]),
                gap_seconds=float(config["short_gap_seconds"]),
            )
        except ValueError as original:
            segments = []
            for candidate in range(requested - 1, minimum - 1, -1):
                try:
                    segments = choose_segments(
                        duration=info["duration"],
                        energy=energy,
                        count=candidate,
                        short_seconds=float(config["short_seconds"]),
                        gap_seconds=float(config["short_gap_seconds"]),
                    )
                except ValueError:
                    continue
                requested = candidate
                print(f"Source is short; producing {candidate} shorts instead.")
                break
            if not segments:
                raise original

        rendered = render_all(
            source=source,
            source_info=info,
            work_dir=work_dir,
            segments=segments,
            source_title=video["title"],
            settings=settings,
            max_bytes=int(config["max_upload_bytes"]),
        )
        print(f"Rendered {len(rendered)} shorts.")

        uploaded = 0
        for position, short in enumerate(rendered):
            if position > 0 and int(config["upload_delay_seconds"]) > 0:
                import time

                time.sleep(int(config["upload_delay_seconds"]))
            try:
                if config.get("dry_run"):
                    youtube_id = f"dryrun-{video['id']}-{short['index']}"
                else:
                    token = refresh_access_token()
                    metadata = build_upload_metadata(
                        source_title=video["title"],
                        source_url=video["url"],
                        channel_title=video["channelTitle"],
                        license_name=video["license"],
                        index=int(short["index"]),
                        total=len(rendered),
                        category_id=str(video.get("categoryId") or config["upload_category_id"]),
                        privacy=str(config["upload_privacy"]),
                        made_for_kids=bool(config["made_for_kids"]),
                    )
                    youtube_id = upload_short(
                        path=Path(short["path"]),
                        metadata=metadata,
                        access_token=token,
                        max_bytes=int(config["max_upload_bytes"]),
                    )
            except Exception as error:  # noqa: BLE001
                short["status"] = "failed"
                short["error"] = str(error)
                print(f"Short {short['index']} failed: {error}")
                continue
            short["youtubeVideoId"] = youtube_id
            short["status"] = "uploaded"
            uploaded += 1
            print(f"Uploaded short {short['index']}: https://youtube.com/shorts/{youtube_id}")

        if not config.get("dry_run") and uploaded:
            status = "completed" if uploaded == len(rendered) else "partial"
            mark_used(state, video, status, rendered)
            save_state(config, state)
            print(f"State updated: {video['id']} ({status}, {uploaded}/{len(rendered)} uploaded).")

        if uploaded < minimum:
            raise PipelineError(
                f"Only {uploaded}/{len(rendered)} shorts uploaded; minimum is {minimum}."
            )

        lines = [
            f"## {'Dry run' if config.get('dry_run') else 'Uploaded'} {uploaded} shorts",
            "",
            f"Source: [{video['title']}]({video['url']})",
            f"Channel: {video['channelTitle']}",
            f"Requested: {requested}; rendered: {len(rendered)}; uploaded: {uploaded}",
            "",
            "| # | Title | YouTube |",
            "|---|---|---|",
        ]
        for short in rendered:
            youtube_id = short.get("youtubeVideoId")
            link = (
                "dry run"
                if config.get("dry_run")
                else f"https://youtube.com/shorts/{youtube_id}"
                if youtube_id
                else "failed"
            )
            lines.append(f"| {short['index']} | {short['title'][:70]} | {link} |")
        summary = "\n".join(lines)
        write_summary(summary)
        send_email(
            f"[yt-shorts] {uploaded} shorts {'rendered' if config.get('dry_run') else 'uploaded'}",
            summary,
        )
        return 0
    finally:
        if config.get("dry_run"):
            shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    try:
        return run()
    except Exception as error:  # noqa: BLE001
        detail = f"{error}\n\n{traceback.format_exc()}"
        print(detail, file=sys.stderr)
        write_summary(f"## Run failed\n\n```\n{str(error)[:2000]}\n```\n")
        send_email("[yt-shorts] Run failed", detail[-4000:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
