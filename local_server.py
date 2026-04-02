#!/usr/bin/env python3
"""
reels-catcher Extension 연동 서버
Chrome Extension → POST /api/reels → 다운로드 → 메타데이터 → 분류 → Obsidian → Notion

실행:
    cd <reels-catcher 경로>
    source .venv/bin/activate
    python3 <reels-catcher-extension 경로>/local_server.py
"""

import argparse
import json
import logging
import re
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ── 로깅 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reels-server")

# ── 설정 로드 ─────────────────────────────────────────────────────────────────
_CONFIG_PATH = Path.home() / ".local" / "share" / "reels-catcher-extension" / "config.json"

def _load_config() -> dict:
    """config.json 로드. 누락 키는 기본값으로 채운다."""
    cfg: dict = {}
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning(f"config.json 없음: {_CONFIG_PATH}  기본값으로 실행합니다.")
    except Exception as e:
        log.warning(f"config.json 로드 실패: {e}  기본값으로 실행합니다.")
    return cfg

_CFG = _load_config()

# ── 경로 설정 (config.json 우선, 없으면 기본값) ───────────────────────────────
REELS_CATCHER_SRC = Path(_CFG.get("reels_catcher_src", "")) if _CFG.get("reels_catcher_src") else None
DATASET_ROOT = Path(_CFG.get("dataset_root", Path.home() / "reels-catcher_output"))
SEEN_FILE = Path.home() / ".local" / "share" / "reels-catcher-extension" / "ext_seen.json"

# 서버 시작 시각 (이 시각 이후 수신된 DM만 처리)
SERVER_START_TIME = datetime.now(timezone.utc)


def _parse_timestamp(ts) -> "datetime | None":
    """Unix epoch(초/밀리초) 또는 ISO 문자열 → timezone-aware datetime"""
    if ts is None:
        return None
    try:
        # 숫자형 or 숫자 문자열 → Unix epoch
        val = float(ts)
        # 밀리초 범위(13자리)이면 초로 변환
        if val > 1e12:
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)
    except (ValueError, TypeError):
        pass
    try:
        # ISO 문자열
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

# ── reels-catcher 파이프라인 import ───────────────────────────────────────────
if REELS_CATCHER_SRC:
    sys.path.insert(0, str(REELS_CATCHER_SRC))
try:
    from reels_catcher.downloader import download
    from reels_catcher.classifier import get_classifier
    from reels_catcher.metadata import parse_info_json, save_metadata
    from reels_catcher.obsidian_writer import write_note
    PIPELINE_AVAILABLE = True
    log.info("✅ reels-catcher 파이프라인 로드 성공")
except ImportError as e:
    PIPELINE_AVAILABLE = False
    log.error(f"❌ 파이프라인 import 실패: {e}")
    if REELS_CATCHER_SRC:
        log.error(f"   경로 확인: {REELS_CATCHER_SRC}")
    else:
        log.error("   config.json에 reels_catcher_src 키가 없습니다.")

# ── Notion writer import ──────────────────────────────────────────────────────
_server_dir = Path(__file__).parent
sys.path.insert(0, str(_server_dir))
try:
    from notion_writer import sync_to_notion
    NOTION_AVAILABLE = True
    log.info("✅ Notion writer 로드 성공")
except ImportError as e:
    NOTION_AVAILABLE = False
    log.warning(f"⚠️  Notion writer 로드 실패 (선택 기능): {e}")

# ── seen 관리 ─────────────────────────────────────────────────────────────────
_seen_lock = threading.Lock()

def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            return set(data.get("shortcodes", []))
        except Exception:
            pass
    return set()

def save_seen(seen: set) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(
        json.dumps({"shortcodes": sorted(seen)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

_seen: set = load_seen()

def is_already_processed(shortcode: str) -> bool:
    with _seen_lock:
        return shortcode in _seen

def mark_processed(shortcode: str) -> None:
    with _seen_lock:
        _seen.add(shortcode)
        save_seen(_seen)

# ── 파이프라인 실행 ───────────────────────────────────────────────────────────
def run_pipeline(url: str, shortcode: str) -> dict:
    if not PIPELINE_AVAILABLE:
        return {"success": False, "error": "파이프라인 미로드"}

    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    (DATASET_ROOT / "_index").mkdir(parents=True, exist_ok=True)

    try:
        log.info(f"⬇️  다운로드 시작: {url}")
        result = download(url, str(DATASET_ROOT))
        if not result["success"]:
            return {"success": False, "error": f"다운로드 실패: {result['error']}"}

        metadata = parse_info_json(result["info_json_path"], shortcode, str(DATASET_ROOT))
        metadata_path = DATASET_ROOT / shortcode / "metadata.json"
        save_metadata(metadata, str(metadata_path))

        classifier = get_classifier("auto")
        metadata["tags"] = classifier.classify(metadata)
        save_metadata(metadata, str(metadata_path))

        note_path = write_note(metadata, str(DATASET_ROOT))
        game_title = (metadata.get("tags") or {}).get("game_title") or "Unknown Game"
        log.info(f"✅ 처리 완료: {game_title} → {note_path}")

        # ── Notion 동기화 (non-blocking) ──────────────────────────────────
        if NOTION_AVAILABLE:
            sync_to_notion(metadata, dataset_root=str(DATASET_ROOT))

        return {"success": True, "game_title": game_title, "note_path": str(note_path)}

    except Exception as e:
        log.error(f"파이프라인 오류 {url}: {e}")
        return {"success": False, "error": str(e)}


# ── HTTP 핸들러 ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/reels":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        url = data.get("url", "")
        shortcode = data.get("shortcode", "")
        timestamp_raw = data.get("timestamp")

        # ── 필수 필드 확인 ─────────────────────────────────────────────────
        if not url or not shortcode:
            self._respond(400, {"error": "url, shortcode 필수"})
            return

        # ── 서버 시작 이전 DM 무시 ──────────────────────────────────────────
        if timestamp_raw:
            msg_time = _parse_timestamp(timestamp_raw)
            if msg_time and msg_time < SERVER_START_TIME:
                log.info(f"⏭️  서버 시작 전 DM 무시: {shortcode} ({msg_time.strftime('%H:%M:%S')})")
                self._respond(200, {"status": "skipped", "reason": "before_server_start"})
                return

        # ── 중복 체크 ────────────────────────────────────────────────────────
        if is_already_processed(shortcode):
            log.info(f"⏭️  중복 스킵: {shortcode}")
            self._respond(200, {"status": "skipped", "reason": "duplicate"})
            return

        # ── 파이프라인 실행 (별도 스레드) ─────────────────────────────────
        log.info(f"🎬 릴스 수신: {url} (shortcode={shortcode})")
        mark_processed(shortcode)  # 선점 등록 (중복 방지)

        def run():
            result = run_pipeline(url, shortcode)
            if not result["success"]:
                # 실패 시 seen에서 제거해 재시도 가능하게
                with _seen_lock:
                    _seen.discard(shortcode)
                    save_seen(_seen)

        threading.Thread(target=run, daemon=True).start()
        self._respond(200, {"status": "accepted", "shortcode": shortcode})

    def _respond(self, code: int, body: dict):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass  # 기본 HTTP 로그 억제


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    log.info(f"🚀 reels-catcher 서버 시작: http://localhost:{args.port}/api/reels")
    log.info(f"   서버 시작 시각: {SERVER_START_TIME.strftime('%H:%M:%S')} (이후 수신 DM만 처리)")
    log.info(f"   파이프라인: {'✅ 활성' if PIPELINE_AVAILABLE else '❌ 비활성 (debug_server.py로 동작)'}")
    log.info(f"   seen 파일: {SEEN_FILE}")
    log.info("   종료: Ctrl+C\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("서버 종료")
