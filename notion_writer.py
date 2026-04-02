"""
reels-catcher-extension → Notion 동기화 모듈 (Notion API v3 호환).
ad_id 기준 upsert (이미 존재하면 update, 없으면 create).
실패 시 예외를 억제하고 로깅만 한다 (non-blocking).

Notion API v3 변경사항:
- 데이터/프로퍼티는 database 내부의 data_source에 있음
- databases.query → data_sources.query
- schema 업데이트 → data_sources.update
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("reels-server")

# ── 경로 상수 ─────────────────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / ".local" / "share" / "reels-catcher-extension" / "config.json"

def _get_dataset_root() -> Path:
    """config.json의 dataset_root 반환. 없으면 홈 디렉토리 하위 기본값."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        v = cfg.get("dataset_root")
        if v:
            return Path(v)
    except Exception:
        pass
    return Path.home() / "reels-catcher_output"

DATASET_ROOT = _get_dataset_root()

# 멀티파트 업로드 청크 크기 (20MB)
CHUNK_SIZE = 20 * 1024 * 1024

# ── DB 스키마 정의 ─────────────────────────────────────────────────────────────
DB_SCHEMA = {
    "Ad ID":          {"rich_text": {}},
    "Uploader":       {"rich_text": {}},
    "Description":    {"rich_text": {}},
    "AI Notes":       {"rich_text": {}},
    "Source URL":     {"url": {}},
    "Collected At":   {"date": {}},
    "Duration (sec)": {"number": {"format": "number"}},
    "Like Count":     {"number": {"format": "number"}},
    "Platform":       {"select": {}},
    "Game Title":     {"select": {}},
    "Ad Hook Type":   {"select": {}},
    "Genre":          {"multi_select": {}},
    "Art Style":      {"multi_select": {}},
}


def _load_config() -> tuple[str, str] | None:
    """(api_key, db_id) 반환. 설정 누락 시 None."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        key = cfg.get("notion_api_key", "")
        db_id = cfg.get("notion_db_id", "")
        if key and db_id:
            return key, db_id
    except Exception as e:
        log.warning(f"[notion] config 로드 실패: {e}")
    return None


def _get_client(api_key: str):
    try:
        from notion_client import Client
        return Client(auth=api_key)
    except ImportError:
        log.error("[notion] notion-client 미설치. `pip install notion-client` 실행 필요.")
        return None


# ── data_source_id 조회 (캐시) ────────────────────────────────────────────────
_ds_cache: dict[str, str] = {}  # db_id → ds_id

def _get_ds_id(notion, db_id: str) -> str | None:
    """database_id에 대응하는 data_source_id 반환."""
    if db_id in _ds_cache:
        return _ds_cache[db_id]
    try:
        db = notion.databases.retrieve(database_id=db_id)
        sources = db.get("data_sources", [])
        if sources:
            ds_id = sources[0]["id"]
            _ds_cache[db_id] = ds_id
            return ds_id
    except Exception as e:
        log.warning(f"[notion] data_source_id 조회 실패: {e}")
    return None


# ── DB 스키마 초기화 ───────────────────────────────────────────────────────────
_schema_initialized: set[str] = set()

def ensure_schema(notion, ds_id: str) -> None:
    """data_source에 필요한 프로퍼티가 없으면 추가한다."""
    if ds_id in _schema_initialized:
        return
    try:
        ds = notion.data_sources.retrieve(data_source_id=ds_id)
        existing = set(ds.get("properties", {}).keys())
        missing = {k: v for k, v in DB_SCHEMA.items() if k not in existing}
        if missing:
            notion.data_sources.update(data_source_id=ds_id, properties=missing)
            log.info(f"[notion] 스키마 추가: {list(missing.keys())}")
        _schema_initialized.add(ds_id)
    except Exception as e:
        log.warning(f"[notion] 스키마 초기화 실패: {e}")


# ── 값 변환 헬퍼 ──────────────────────────────────────────────────────────────

def _title(text: str) -> dict:
    return {"title": [{"text": {"content": str(text)[:2000]}}]}

def _rich_text(text) -> dict:
    return {"rich_text": [{"text": {"content": str(text or "")[:2000]}}]}

def _select(value) -> dict | None:
    if not value:
        return None
    return {"select": {"name": str(value)[:100]}}

def _multi_select(values: list) -> dict:
    return {"multi_select": [{"name": str(v)[:100]} for v in (values or []) if v]}

def _number(value) -> dict | None:
    if value is None:
        return None
    try:
        return {"number": float(value)}
    except (TypeError, ValueError):
        return None

def _url(value) -> dict | None:
    if not value:
        return None
    return {"url": str(value)}

def _date(value) -> dict | None:
    if not value:
        return None
    return {"date": {"start": str(value)[:50]}}


# ── 메타데이터 → Notion properties 변환 ──────────────────────────────────────

def _build_properties(meta: dict, title_key: str = "이름") -> dict:
    tags = meta.get("tags") or {}
    uploader = meta.get("uploader") or ""
    ad_id = meta.get("ad_id") or ""

    props: dict = {}

    # Title (Notion 기본 타이틀 컬럼: 생성 시 '이름')
    props[title_key] = _title(f"{uploader} — {ad_id}" if uploader else ad_id)

    # Text fields
    props["Ad ID"] = _rich_text(ad_id)
    props["Uploader"] = _rich_text(uploader)
    props["Description"] = _rich_text(meta.get("description") or "")
    props["AI Notes"] = _rich_text(tags.get("ai_notes") or "")

    # URL / Date / Number
    if v := _url(meta.get("source_url")):
        props["Source URL"] = v
    if v := _date(meta.get("collected_at")):
        props["Collected At"] = v
    if v := _number(meta.get("duration_sec")):
        props["Duration (sec)"] = v
    if v := _number(meta.get("like_count")):
        props["Like Count"] = v

    # Select
    if v := _select(meta.get("platform")):
        props["Platform"] = v
    if v := _select(tags.get("game_title")):
        props["Game Title"] = v
    if v := _select(tags.get("ad_hook_type")):
        props["Ad Hook Type"] = v

    # Multi-select
    props["Genre"] = _multi_select(tags.get("genre") or [])
    props["Art Style"] = _multi_select(tags.get("art_style") or [])

    return props


# ── 중복 체크 ─────────────────────────────────────────────────────────────────

def _find_existing_page(notion, ds_id: str, ad_id: str) -> str | None:
    """ad_id 기준으로 기존 페이지 ID 반환. 없으면 None."""
    try:
        resp = notion.data_sources.query(
            data_source_id=ds_id,
            filter={
                "property": "Ad ID",
                "rich_text": {"equals": ad_id}
            }
        )
        results = resp.get("results", [])
        if results:
            return results[0]["id"]
    except Exception as e:
        log.warning(f"[notion] 중복 체크 실패: {e}")
    return None


# ── 타이틀 컬럼명 조회 ────────────────────────────────────────────────────────
_title_key_cache: dict[str, str] = {}

def _get_title_key(notion, ds_id: str) -> str:
    """data_source의 title 타입 프로퍼티 이름 반환. 기본값 '이름'."""
    if ds_id in _title_key_cache:
        return _title_key_cache[ds_id]
    try:
        ds = notion.data_sources.retrieve(data_source_id=ds_id)
        for name, prop in ds.get("properties", {}).items():
            if prop.get("type") == "title":
                _title_key_cache[ds_id] = name
                return name
    except Exception:
        pass
    return "이름"


# ── 동영상 업로드 ─────────────────────────────────────────────────────────────

def _upload_video(notion, video_path: Path) -> str | None:
    """로컬 동영상 파일을 Notion File Uploads로 업로드. 성공 시 file_upload_id 반환."""
    if not video_path.exists():
        log.warning(f"[notion] 동영상 파일 없음: {video_path}")
        return None

    file_size = video_path.stat().st_size
    content_type = "video/mp4"
    filename = video_path.name

    # 청크 수 계산 (20MB 단위)
    num_parts = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)
    log.info(f"[notion] 동영상 업로드 시작: {filename} ({file_size // 1024}KB, {num_parts}파트)")

    try:
        upload = notion.file_uploads.create(
            mode="multi_part",
            filename=filename,
            content_type=content_type,
            number_of_parts=num_parts,
        )
        upload_id = upload["id"]

        with open(video_path, "rb") as f:
            for part_number in range(1, num_parts + 1):
                chunk = f.read(CHUNK_SIZE)
                notion.file_uploads.send(
                    file_upload_id=upload_id,
                    file=(filename, chunk, content_type),
                    part_number=part_number,
                )

        notion.file_uploads.complete(file_upload_id=upload_id)
        log.info(f"[notion] 동영상 업로드 완료: {upload_id}")
        return upload_id

    except Exception as e:
        log.error(f"[notion] 동영상 업로드 실패: {e}")
        return None


def _attach_video_block(notion, page_id: str, upload_id: str) -> None:
    """페이지에 video 블록 추가."""
    try:
        notion.blocks.children.append(
            block_id=page_id,
            children=[{
                "type": "video",
                "video": {
                    "type": "file_upload",
                    "file_upload": {"id": upload_id}
                }
            }]
        )
    except Exception as e:
        log.error(f"[notion] 동영상 블록 추가 실패 ({page_id}): {e}")


# ── 메인 함수 ─────────────────────────────────────────────────────────────────

def sync_to_notion(metadata: dict, dataset_root: str | None = None) -> None:
    """
    metadata dict를 Notion 데이터베이스에 upsert.
    실패 시 예외 억제 (non-blocking).
    """
    cfg = _load_config()
    if not cfg:
        log.warning("[notion] API key 또는 DB ID 미설정. 동기화 스킵.")
        return

    api_key, db_id = cfg
    notion = _get_client(api_key)
    if not notion:
        return

    ad_id = metadata.get("ad_id") or ""
    if not ad_id:
        log.warning("[notion] ad_id 없음. 동기화 스킵.")
        return

    try:
        ds_id = _get_ds_id(notion, db_id)
        if not ds_id:
            log.error("[notion] data_source_id 조회 실패. 동기화 스킵.")
            return

        # 스키마 초기화 (최초 1회)
        ensure_schema(notion, ds_id)

        # 타이틀 컬럼명 조회
        title_key = _get_title_key(notion, ds_id)

        props = _build_properties(metadata, title_key=title_key)
        existing_id = _find_existing_page(notion, ds_id, ad_id)

        if existing_id:
            notion.pages.update(page_id=existing_id, properties=props)
            page_id = existing_id
            log.info(f"[notion] ✏️  업데이트: {ad_id}")
        else:
            page = notion.pages.create(
                parent={"database_id": db_id},
                properties=props,
            )
            page_id = page["id"]
            log.info(f"[notion] ✅ 생성: {ad_id}")

        # ── 동영상 첨부 ────────────────────────────────────────────────────
        if dataset_root:
            # metadata의 video_path 필드 우선, 없으면 rglob으로 탐색
            video_path = None
            raw_vp = metadata.get("video_path")
            if raw_vp:
                candidate = Path(dataset_root) / raw_vp
                if candidate.exists():
                    video_path = candidate

            if not video_path:
                # game_title/shortcode/video.* 구조 탐색
                hits = list(Path(dataset_root).rglob(f"{ad_id}/video.*"))
                if hits:
                    video_path = hits[0]

            if video_path and video_path.exists():
                upload_id = _upload_video(notion, video_path)
                if upload_id:
                    _attach_video_block(notion, page_id, upload_id)
            else:
                log.warning(f"[notion] 동영상 파일 없음: {ad_id}")

    except Exception as e:
        log.error(f"[notion] 동기화 실패 ({ad_id}): {e}")
