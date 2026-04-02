"""
yt-dlp info.json → 정규화 메타데이터 JSON 변환.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


NORMALIZED_SCHEMA = {
    "ad_id": str,
    "source_url": str,
    "platform": "instagram",
    "collected_at": str,
    "title": str,
    "uploader": str,
    "uploader_id": str,
    "description": str,
    "hashtags": list,
    "is_paid_partnership": bool,
    "duration_sec": float,
    "view_count": int | None,
    "like_count": int | None,
    "comment_count": int | None,
    "thumbnail_path": str,
    "video_path": str,
    "tags": {
        "game_title": str | None,
        "developer": str | None,
        "genre": list,
        "art_style": list,
        "ad_hook_type": str | None,
        "target_audience": str | None,
        "visual_quality": str | None,
        "ai_notes": str | None,
        "classified_at": str | None,
        "classified_by": str | None,
    },
}


def empty_tags() -> dict:
    return {
        "game_title": None,
        "developer": None,
        "genre": [],
        "art_style": [],
        "ad_hook_type": None,
        "target_audience": None,
        "visual_quality": None,
        "ai_notes": None,
        "classified_at": None,
        "classified_by": None,
    }


def _now_kst_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat()


def parse_info_json(info_json_path: str, shortcode: str, output_root: str) -> dict:
    info_path = Path(info_json_path)
    payload = json.loads(info_path.read_text(encoding="utf-8"))

    resolved_shortcode = payload.get("id") or shortcode

    description = payload.get("description") or ""
    uploader = payload.get("uploader") or ""
    source_url = (
        payload.get("webpage_url")
        or payload.get("original_url")
        or f"https://www.instagram.com/reel/{resolved_shortcode}/"
    )
    root = Path(output_root).expanduser().resolve()
    thumbnail_path = root / resolved_shortcode / "thumbnail.jpg"
    video_path = root / resolved_shortcode / "video.mp4"

    return {
        "ad_id": resolved_shortcode,
        "source_url": source_url,
        "platform": "instagram",
        "collected_at": _now_kst_iso(),
        "title": f"{uploader}_{resolved_shortcode}" if uploader else f"unknown_{resolved_shortcode}",
        "uploader": uploader,
        "uploader_id": payload.get("uploader_id") or "",
        "description": description,
        "hashtags": re.findall(r"#(\w+)", description or ""),
        "is_paid_partnership": bool(
            payload.get("is_paid_partnership", payload.get("paid_video", False))
        ),
        "duration_sec": float(payload.get("duration") or 0.0),
        "view_count": payload.get("view_count"),
        "like_count": payload.get("like_count"),
        "comment_count": payload.get("comment_count"),
        "thumbnail_path": str(thumbnail_path.relative_to(root)),
        "video_path": str(video_path.relative_to(root)),
        "tags": empty_tags(),
    }


def normalize_info(info_json_path: str, video_path: str, thumbnail_path: str) -> dict:
    video = Path(video_path).expanduser().resolve()
    return parse_info_json(info_json_path, video.parent.name, str(video.parent.parent))


def save_metadata(metadata: dict, output_path: str) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
