#!/usr/bin/env python3
"""
기존 수집 데이터 전체를 Notion 데이터베이스에 백필.

사전 요구사항:
  - ~/.local/share/reels-catcher-extension/config.json 설정 완료
  - notion-client 설치 (pip install notion-client)

사용법:
  # Extension 디렉토리의 venv를 활성화한 뒤 실행:
  source <venv>/bin/activate
  python3 <reels-catcher-extension 경로>/scripts/backfill_notion.py

옵션:
    --dry-run    실제 업로드 없이 항목만 나열
    --no-video   메타데이터만 (동영상 제외)
    --video-only 동영상 첨부만 (메타데이터 skip, 이미 페이지 존재 전제)
    --delay N    항목 간 대기 시간(초), 기본 1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
# 스크립트 위치(scripts/) 기준으로 extension 디렉토리 탐색
EXT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path.home() / ".local" / "share" / "reels-catcher-extension" / "config.json"

# config.json에서 경로 로드
def _load_paths() -> tuple[Path, Path | None]:
    """(dataset_root, reels_catcher_src) 반환."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[ERROR] config.json 없음: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] config.json 로드 실패: {e}", file=sys.stderr)
        sys.exit(1)

    dataset_root = Path(cfg.get("dataset_root", Path.home() / "reels-catcher_output"))
    rcs = cfg.get("reels_catcher_src")
    reels_catcher_src = Path(rcs) if rcs else None
    return dataset_root, reels_catcher_src

DATASET_ROOT, REELS_CATCHER_SRC = _load_paths()

# Python 경로 설정
sys.path.insert(0, str(EXT_DIR))
if REELS_CATCHER_SRC:
    sys.path.insert(0, str(REELS_CATCHER_SRC))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill")


def find_all_metadata(root: Path) -> list[Path]:
    return sorted(root.rglob("metadata.json"))


def main():
    parser = argparse.ArgumentParser(description="reels-catcher → Notion 백필")
    parser.add_argument("--dry-run", action="store_true", help="항목 나열만, 실제 업로드 없음")
    parser.add_argument("--no-video", action="store_true", help="메타데이터만 (동영상 제외)")
    parser.add_argument("--video-only", action="store_true", help="동영상 첨부만 (메타데이터 skip, 이미 페이지 존재 전제)")
    parser.add_argument("--delay", type=float, default=1.0, help="항목 간 대기 시간(초), 기본 1.0")
    args = parser.parse_args()

    from notion_writer import (
        sync_to_notion, _load_config, _get_client, _get_ds_id,
        _find_existing_page, _upload_video, _attach_video_block,
    )

    meta_files = find_all_metadata(DATASET_ROOT)
    log.info(f"dataset_root: {DATASET_ROOT}")
    log.info(f"총 {len(meta_files)}개 항목 발견")

    if args.dry_run:
        for p in meta_files:
            meta = json.loads(p.read_text())
            ad_id = meta.get("ad_id", "?")
            game = (meta.get("tags") or {}).get("game_title") or "(없음)"
            hits = list(DATASET_ROOT.rglob(f"{ad_id}/video.*"))
            video_size = f"{hits[0].stat().st_size // 1024}KB" if hits else "없음"
            log.info(f"  [{game}] {ad_id}  video={video_size}")
        return

    if args.video_only:
        cfg = _load_config()
        if not cfg:
            log.error("config 로드 실패")
            return
        api_key, db_id = cfg
        notion = _get_client(api_key)
        ds_id = _get_ds_id(notion, db_id)

        ok = 0
        fail = 0
        for i, meta_path in enumerate(meta_files, 1):
            meta = json.loads(meta_path.read_text())
            ad_id = meta.get("ad_id", "?")
            game = (meta.get("tags") or {}).get("game_title") or "(없음)"
            log.info(f"[{i}/{len(meta_files)}] {game} / {ad_id}")

            try:
                page_id = _find_existing_page(notion, ds_id, ad_id)
                if not page_id:
                    log.warning(f"  페이지 없음: {ad_id}, skip")
                    continue

                hits = list(DATASET_ROOT.rglob(f"{ad_id}/video.*"))
                if not hits:
                    log.warning(f"  동영상 파일 없음: {ad_id}")
                    continue

                upload_id = _upload_video(notion, hits[0])
                if upload_id:
                    _attach_video_block(notion, page_id, upload_id)
                    log.info("  ✅ 동영상 첨부 완료")
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                log.error(f"  ❌ 실패: {e}")
                fail += 1

            if i < len(meta_files):
                time.sleep(args.delay)

        log.info(f"\n완료: 동영상 첨부 성공 {ok}개 / 실패 {fail}개")
        return

    ok = 0
    fail = 0
    for i, meta_path in enumerate(meta_files, 1):
        meta = json.loads(meta_path.read_text())
        ad_id = meta.get("ad_id", "?")
        game = (meta.get("tags") or {}).get("game_title") or "(없음)"
        log.info(f"[{i}/{len(meta_files)}] {game} / {ad_id}")

        try:
            ds_root = str(DATASET_ROOT) if not args.no_video else None
            sync_to_notion(meta, dataset_root=ds_root)
            ok += 1
        except Exception as e:
            log.error(f"  ❌ 실패: {e}")
            fail += 1

        if i < len(meta_files):
            time.sleep(args.delay)

    log.info(f"\n완료: 성공 {ok}개 / 실패 {fail}개 / 전체 {len(meta_files)}개")


if __name__ == "__main__":
    main()
