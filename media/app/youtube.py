from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Mapping

import requests

from .errors import PipelineError

LOGGER = logging.getLogger("yt-shorts.youtube")
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def _error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:1200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        errors = error.get("errors")
        reason = ""
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            reason = str(errors[0].get("reason") or "")
        return f"{message or 'unknown error'}{f' ({reason})' if reason else ''}"
    return str(payload)[:1200]


def upload_short(
    *,
    path: Path,
    metadata: Mapping[str, Any],
    access_token: str,
    max_bytes: int,
) -> str:
    size = path.stat().st_size
    if size <= 0:
        raise PipelineError(f"{path.name} is empty")
    if size > max_bytes:
        raise PipelineError(
            f"{path.name} is {size / 1024 / 1024:.1f} MiB, above the configured upload limit"
        )

    init_headers = {
        "authorization": f"Bearer {access_token}",
        "content-type": "application/json; charset=UTF-8",
        "x-upload-content-length": str(size),
        "x-upload-content-type": "video/mp4",
    }
    try:
        init = requests.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers=init_headers,
            json=dict(metadata),
            timeout=60,
        )
    except requests.RequestException as error:
        raise PipelineError(f"YouTube upload init failed: {error}") from error
    if not init.ok:
        raise PipelineError(
            f"YouTube upload init failed ({init.status_code}): {_error_detail(init)}"
        )
    session_url = init.headers.get("Location") or init.headers.get("location")
    if not session_url:
        raise PipelineError("YouTube did not return a resumable upload session URL")

    offset = 0
    last_error = ""
    for attempt in range(5):
        headers = {
            "content-type": "video/mp4",
            "content-length": str(size - offset),
        }
        if offset > 0:
            headers["content-range"] = f"bytes {offset}-{size - 1}/{size}"
        try:
            with path.open("rb") as stream:
                if offset:
                    stream.seek(offset)
                response = requests.put(
                    session_url,
                    data=stream,
                    headers=headers,
                    timeout=(30, 600),
                )
        except requests.RequestException as error:
            last_error = str(error)
            time.sleep(min(20, 2**attempt))
            continue

        if response.status_code in (200, 201):
            payload = response.json()
            video_id = payload.get("id")
            if not isinstance(video_id, str) or not video_id:
                raise PipelineError("YouTube accepted the upload but returned no video ID")
            return video_id

        if response.status_code == 308:
            range_header = response.headers.get("Range") or response.headers.get("range")
            if range_header and "-" in range_header:
                try:
                    offset = int(range_header.rsplit("-", 1)[1]) + 1
                except ValueError:
                    offset = 0
            else:
                offset = 0
            continue

        last_error = f"HTTP {response.status_code}: {_error_detail(response)}"
        if 500 <= response.status_code < 600:
            time.sleep(min(20, 2**attempt))
            continue
        break

    raise PipelineError(f"YouTube upload failed: {last_error or 'unknown upload error'}")
