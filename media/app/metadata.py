from __future__ import annotations

import re
import textwrap
from typing import Mapping

MAX_TITLE = 100
MAX_DESCRIPTION = 5000
MAX_TAGS_CHARS = 500


def _clean(value: str) -> str:
    value = re.sub(r"[\u0000-\u001f\u007f]", " ", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def clean_title(value: str) -> str:
    cleaned = _clean(value)
    cleaned = re.sub(r"#shorts?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("<", "").replace(">", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|")
    return cleaned or "Untitled video"


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def build_short_title(source_title: str, index: int, total: int) -> str:
    base = clean_title(source_title)
    suffix = f" | Part {index} #Shorts"
    room = MAX_TITLE - len(suffix)
    return f"{truncate(base, room)}{suffix}"


def wrap_title(title: str, width: int = 26, max_lines: int = 3) -> str:
    lines = textwrap.wrap(clean_title(title), width=width, break_long_words=True)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = truncate(lines[-1], max(4, width - 1))
    return "\n".join(lines)


def build_description(
    *,
    source_title: str,
    source_url: str,
    channel_title: str,
    license_name: str,
    index: int,
    total: int,
) -> str:
    attribution = ""
    if (license_name or "").lower() == "creativecommon":
        attribution = (
            "Source footage is licensed under Creative Commons Attribution (CC BY): "
            "https://creativecommons.org/licenses/by/4.0/. "
            f"Credit: {channel_title or 'the original creator'}."
        )
    else:
        attribution = (
            "Source footage used with permission from the rights holder. "
            f"Credit: {channel_title or 'the original creator'}."
        )

    description = "\n".join(
        [
            f"Part {index} of {total}.",
            "",
            f"Source: {source_title}",
            source_url,
            "",
            attribution,
            "",
            "This is a transformed short-form edit. If you are the rights holder and want this removed, contact the channel email in the About page.",
            "",
            "#Shorts #YouTubeShorts",
        ]
    )
    return truncate(description, MAX_DESCRIPTION)


def build_tags(source_title: str, channel_title: str | None = None) -> list[str]:
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}", source_title)
        if word.lower() not in {"the", "and", "for", "with", "this", "that", "from", "you"}
    ][:8]
    tags = ["shorts", "youtube shorts", "short video"]
    if channel_title:
        tags.append(_clean(channel_title)[:40])
    tags.extend(words)
    output: list[str] = []
    used = 0
    for tag in tags:
        tag = _clean(tag)
        if not tag:
            continue
        cost = len(tag) + 1
        if used + cost > MAX_TAGS_CHARS:
            break
        output.append(tag)
        used += cost
    return output


def build_upload_metadata(
    *,
    source_title: str,
    source_url: str,
    channel_title: str,
    license_name: str,
    index: int,
    total: int,
    category_id: str,
    privacy: str,
    made_for_kids: bool,
) -> Mapping[str, object]:
    return {
        "snippet": {
            "title": build_short_title(source_title, index, total),
            "description": build_description(
                source_title=source_title,
                source_url=source_url,
                channel_title=channel_title,
                license_name=license_name,
                index=index,
                total=total,
            ),
            "tags": build_tags(source_title, channel_title),
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
            "license": "youtube",
        },
    }
