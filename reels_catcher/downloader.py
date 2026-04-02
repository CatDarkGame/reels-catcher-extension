"""
yt-dlp 래퍼. Instagram Reel URL 다운로드.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


SHORTCODE_PATTERN = re.compile(r"instagram\.com/(?:reels?|p)/([A-Za-z0-9_-]+)")


def _extract_shortcode(url: str) -> str:
    match = SHORTCODE_PATTERN.search(url)
    if not match:
        raise ValueError("Invalid Instagram reel URL")
    return match.group(1)


def _move_file(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    shutil.move(str(source), str(destination))


def _find_first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _find_video_candidate(shortcode_dir: Path) -> Path | None:
    candidates = [
        shortcode_dir / "mp4",
        shortcode_dir / "mkv",
        shortcode_dir / "webm",
        shortcode_dir / "mov",
    ]
    existing = _find_first_existing(candidates)
    if existing is not None:
        return existing

    for path in sorted(shortcode_dir.iterdir()):
        if path.name in {"video.mp4", "thumbnail.jpg", "metadata_raw.json"}:
            continue
        if path.name in {"jpg", "jpeg", "png", "webp", "info.json"}:
            continue
        if path.suffix in {".json", ".jpg", ".jpeg", ".png", ".webp", ".part"}:
            continue
        if path.is_file():
            return path
    return None


def _find_thumbnail_candidate(shortcode_dir: Path) -> Path | None:
    # -o "video.%(ext)s" 사용 시 썸네일은 video.jpg
    return _find_first_existing(
        [
            shortcode_dir / "video.jpg",
            shortcode_dir / "video.jpeg",
            shortcode_dir / "video.png",
            shortcode_dir / "video.webp",
            shortcode_dir / "jpg",
            shortcode_dir / "jpeg",
            shortcode_dir / "png",
            shortcode_dir / "webp",
            shortcode_dir / "mp4.jpg",  # 이전 방식 fallback
        ]
    )


def _find_info_candidate(shortcode_dir: Path) -> Path | None:
    # -o "video.%(ext)s" 사용 시 info.json은 video.info.json
    direct = _find_first_existing(
        [
            shortcode_dir / "video.info.json",
            shortcode_dir / "info.json",
            shortcode_dir / "json",
        ]
    )
    if direct is not None:
        return direct

    for path in sorted(shortcode_dir.glob("*.info.json")):
        if path.is_file():
            return path
    for path in sorted(shortcode_dir.glob("*.json")):
        if path.is_file() and path.name not in {"metadata_raw.json"}:
            return path
    return None


def download(url: str, output_dir: str) -> dict:
    """
    Parameters:
        url: Instagram reel URL
        output_dir: 데이터셋 루트 경로 (~/Workspace/reels-catcher_output)
    Returns:
        {
            "shortcode": str,
            "video_path": str,
            "thumbnail_path": str,
            "info_json_path": str,
            "success": bool,
            "error": str | None
        }
    """
    try:
        shortcode = _extract_shortcode(url)
    except ValueError as exc:
        return {
            "shortcode": "",
            "video_path": "",
            "thumbnail_path": "",
            "info_json_path": "",
            "success": False,
            "error": str(exc),
        }

    root = Path(output_dir).expanduser().resolve()
    shortcode_dir = root / shortcode
    shortcode_dir.mkdir(parents=True, exist_ok=True)

    video_path = shortcode_dir / "video.mp4"
    thumbnail_path = shortcode_dir / "thumbnail.jpg"
    info_json_path = shortcode_dir / "metadata_raw.json"

    if video_path.exists() and thumbnail_path.exists() and info_json_path.exists():
        return {
            "shortcode": shortcode,
            "video_path": str(video_path),
            "thumbnail_path": str(thumbnail_path),
            "info_json_path": str(info_json_path),
            "success": True,
            "error": None,
        }

    errors: list[str] = []
    last_result: subprocess.CompletedProcess[str] | None = None

    # 쿠키 소스 결정: cookies.txt 파일 우선, 없으면 브라우저 DB fallback
    cookies_file = root / "cookies.txt"
    preferred_browser = os.getenv("COOKIE_BROWSER", "chrome").strip() or "chrome"

    if cookies_file.exists():
        cookie_args = ["--cookies", str(cookies_file)]
        cookie_sources = [("cookies_file", cookie_args)]
    else:
        browsers = [preferred_browser]
        if preferred_browser != "firefox":
            browsers.append("firefox")
        cookie_sources = [(b, ["--cookies-from-browser", b]) for b in browsers]

    for source_name, cookie_opts in cookie_sources:
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            *cookie_opts,
            "--write-info-json",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "--no-playlist",
            "--recode-video",
            "mp4",
            "-o",
            str(shortcode_dir / "video.%(ext)s"),
            url,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except Exception as exc:
            errors.append(f"{source_name}: {exc}")
            continue

        last_result = result
        if result.returncode == 0:
            break

        stderr = result.stderr.strip() or result.stdout.strip() or "yt-dlp failed"
        errors.append(f"{source_name}: {stderr}")

    if last_result is None or last_result.returncode != 0:
        return {
            "shortcode": shortcode,
            "video_path": str(video_path),
            "thumbnail_path": str(thumbnail_path),
            "info_json_path": str(info_json_path),
            "success": False,
            "error": " | ".join(errors) if errors else "yt-dlp execution failed",
        }

    downloaded_info = _find_info_candidate(shortcode_dir)
    downloaded_thumbnail = _find_thumbnail_candidate(shortcode_dir)
    downloaded_video = _find_video_candidate(shortcode_dir)

    if downloaded_info is not None and downloaded_info != info_json_path:
        _move_file(downloaded_info, info_json_path)
    if downloaded_thumbnail is not None and downloaded_thumbnail != thumbnail_path:
        _move_file(downloaded_thumbnail, thumbnail_path)
    if downloaded_video is not None and downloaded_video != video_path:
        _move_file(downloaded_video, video_path)

    missing = []
    if not video_path.exists():
        missing.append("video")
    if not thumbnail_path.exists():
        missing.append("thumbnail")
    if not info_json_path.exists():
        missing.append("info_json")

    if missing:
        return {
            "shortcode": shortcode,
            "video_path": str(video_path),
            "thumbnail_path": str(thumbnail_path),
            "info_json_path": str(info_json_path),
            "success": False,
            "error": f"Missing downloaded files: {', '.join(missing)}",
        }

    return {
        "shortcode": shortcode,
        "video_path": str(video_path),
        "thumbnail_path": str(thumbnail_path),
        "info_json_path": str(info_json_path),
        "success": True,
        "error": None,
    }
