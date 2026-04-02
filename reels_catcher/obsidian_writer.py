"""
정규화 메타데이터 → Obsidian 마크다운 노트 생성.
출력 위치: {output_dir}/{game_title}/{shortcode}/note.md
           (game_title 없으면 {output_dir}/{shortcode}/note.md)
"""

from __future__ import annotations
import re
from pathlib import Path


# Obsidian vault 내에서 reels-catcher 폴더 이름 (symlink명과 일치해야 함)
VAULT_FOLDER = "reels-catcher"


def _safe_dirname(name: str) -> str:
    """파일시스템/Obsidian에서 사용 가능한 폴더명으로 정규화."""
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    return name or "Unknown"


def _resolve_game_folder(root: Path, game_title: str, uploader: str | None) -> Path:
    """
    game_title 기반 폴더를 결정한다.
    같은 sanitized 폴더명이 이미 존재하고 다른 uploader가 쓰고 있으면
    '{game_title} ({uploader})' 형태로 분리한다.
    """
    candidate = _safe_dirname(game_title)
    folder = root / candidate

    # 폴더가 없으면 바로 사용
    if not folder.exists():
        return folder

    # 폴더 안에 .owner 파일로 uploader 기록 (최초 생성 시)
    owner_file = folder / ".owner"
    if not owner_file.exists():
        # 기존 폴더에 owner 기록 없음 → 그냥 사용 (레거시 호환)
        return folder

    existing_uploader = owner_file.read_text(encoding="utf-8").strip()
    if not uploader or existing_uploader == uploader:
        return folder

    # 충돌: 다른 uploader가 같은 sanitized 이름 사용 중 → 구분자 추가
    fallback = _safe_dirname(f"{game_title} ({uploader})")
    return root / fallback


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def _yaml_list(key: str, values: list[str]) -> list[str]:
    if not values:
        return [f"{key}: []"]
    lines = [f"{key}:"]
    for value in values:
        lines.append(f"  - {_yaml_scalar(value)}")
    return lines


def write_note(metadata: dict, output_dir: str) -> Path:
    root = Path(output_dir).expanduser().resolve()
    shortcode = metadata["ad_id"]
    tags = metadata.get("tags") or {}
    game_title = tags.get("game_title")
    developer = tags.get("developer")
    genre = tags.get("genre") or []
    art_style = tags.get("art_style") or []
    ad_hook_type = tags.get("ad_hook_type")
    target_audience = tags.get("target_audience")
    visual_quality = tags.get("visual_quality")
    ai_notes = tags.get("ai_notes")
    classified_by = tags.get("classified_by")
    collected_at = (metadata.get("collected_at") or "")[:10]

    uploader = metadata.get("uploader")

    # 폴더 구조: {game_title}/{shortcode}/ 또는 {shortcode}/
    if game_title:
        game_folder = _resolve_game_folder(root, game_title, uploader)
        note_dir = game_folder / shortcode
        vault_rel = f"{VAULT_FOLDER}/{game_folder.name}/{shortcode}"
    else:
        note_dir = root / shortcode
        vault_rel = f"{VAULT_FOLDER}/{shortcode}"

    note_dir.mkdir(parents=True, exist_ok=True)

    # 최초 생성 시 uploader를 .owner 파일에 기록
    if game_title and uploader:
        owner_file = game_folder / ".owner"
        if not owner_file.exists():
            owner_file.write_text(uploader, encoding="utf-8")

    # 미디어 파일을 shortcode 폴더에서 새 위치로 이동 (파일이 root/shortcode에 있는 경우)
    old_dir = root / shortcode
    if old_dir.exists() and old_dir != note_dir:
        for f in ["video.mp4", "thumbnail.jpg", "meta.json", "metadata.json", "metadata_raw.json"]:
            src = old_dir / f
            if src.exists():
                src.rename(note_dir / f)
        try:
            old_dir.rmdir()  # 비어있으면 삭제
        except OSError:
            pass

    thumbnail_path = f"{vault_rel}/thumbnail.jpg"
    video_path = f"{vault_rel}/video.mp4"

    frontmatter = [
        "---",
        f'ad_id: "{shortcode}"',
        f'source_url: "{metadata.get("source_url")}"',
        "platform: instagram",
        f'collected_at: "{collected_at}"',
        f"game_title: {_yaml_scalar(game_title)}",
        f"developer: {_yaml_scalar(developer)}",
    ]
    frontmatter.extend(_yaml_list("genre", genre))
    frontmatter.extend(_yaml_list("art_style", art_style))
    frontmatter.extend(
        [
            f"ad_hook_type: {_yaml_scalar(ad_hook_type)}",
            f"target_audience: {_yaml_scalar(target_audience)}",
            f"visual_quality: {_yaml_scalar(visual_quality)}",
            f"duration_sec: {_yaml_scalar(round(metadata.get('duration_sec') or 0, 1))}",
            f"view_count: {_yaml_scalar(metadata.get('view_count'))}",
            f"like_count: {_yaml_scalar(metadata.get('like_count'))}",
        ]
    )
    frontmatter.extend(_yaml_list("hashtags", metadata.get("hashtags") or []))
    frontmatter.extend(
        [
            f"is_paid_partnership: {_yaml_scalar(metadata.get('is_paid_partnership'))}",
            f"uploader: {_yaml_scalar(metadata.get('uploader'))}",
            f"classified_by: {_yaml_scalar(classified_by)}",
            f"thumbnail_path: {_yaml_scalar(thumbnail_path)}",
            "---",
        ]
    )

    if game_title is None:
        title_line = f"# [Unknown Game] — {shortcode}"
    else:
        title_line = f"# {game_title} — {ad_hook_type or 'unknown'} 광고"

    content = "\n".join(
        frontmatter
        + [
            "",
            title_line,
            "",
            f"![[{thumbnail_path}]]",
            "",
            "## 메타데이터",
            "- **플랫폼**: Instagram Reels",
            f"- **게임**: {game_title or 'Unknown Game'} ({developer or 'Unknown Developer'})",
            f"- **장르**: {', '.join(genre) if genre else 'None'}",
            f"- **아트 스타일**: {', '.join(art_style) if art_style else 'None'}",
            f"- **광고 훅**: {ad_hook_type or 'None'}",
            f"- **영상 길이**: {round(metadata.get('duration_sec') or 0, 1)}초",
            f"- **수집일**: {collected_at}",
            "",
            "## 캡션",
            metadata.get("description") or "",
            "",
            "## AI 분석 메모",
            ai_notes or "",
            "",
            "## 영상",
            f"[[{video_path}]]",
            "",
        ]
    )

    note_path = note_dir / "note.md"
    note_path.write_text(content, encoding="utf-8")
    return note_path
