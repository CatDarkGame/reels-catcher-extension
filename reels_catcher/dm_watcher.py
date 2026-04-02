"""
DM 와처 — instagrapi 기반 폴링.
봇 계정으로 DM 공유된 릴스를 감지해 자동으로 add 파이프라인에 넘김.
"""

from __future__ import annotations

import json
import time
import random
import logging
from pathlib import Path

CONFIG_DIR = Path.home() / ".local" / "share" / "reels-catcher"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSION_FILE = CONFIG_DIR / "session.json"
DM_SEEN_FILE = CONFIG_DIR / "dm_seen.json"
LOG_FILE = CONFIG_DIR / "logs" / "watcher.log"

DM_SEEN_MAX = 2000   # dm_seen.json 최대 보관 개수
LOG_MAX_BYTES = 2 * 1024 * 1024   # 2MB
LOG_BACKUP_COUNT = 3              # watcher.log.1 ~ .3

log = logging.getLogger("reels-catcher.dm_watcher")

def _setup_logging() -> None:
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    # 콘솔 출력
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)

    # 파일 로테이션
    try:
        from logging.handlers import RotatingFileHandler
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except Exception:
        pass

    log.setLevel(logging.INFO)

_setup_logging()


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"설정 파일이 없습니다: {CONFIG_FILE}\n"
        "먼저 'reels-catcher setup'을 실행하세요."
    )


def _load_seen() -> set[str]:
    if DM_SEEN_FILE.exists():
        data = json.loads(DM_SEEN_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen_message_ids", []))
    return set()


def _save_seen(seen: set[str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # 최대 개수 초과 시 오래된 것부터 정리 (정렬하면 대략 시간순)
    ids = sorted(seen)
    if len(ids) > DM_SEEN_MAX:
        ids = ids[-DM_SEEN_MAX:]
        log.info(f"dm_seen.json 정리: {len(seen)} → {len(ids)}개")
    DM_SEEN_FILE.write_text(
        json.dumps({"seen_message_ids": ids}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_client(cfg: dict):
    """
    instagrapi Client 로드.
    우선순위: 1) session.json  2) cookies.txt sessionid  3) username/password
    """
    try:
        from instagrapi import Client
        from instagrapi.exceptions import LoginRequired, ChallengeRequired, TwoFactorRequired
    except ImportError:
        raise ImportError("instagrapi가 설치되지 않았습니다. pip install instagrapi")

    from urllib.parse import unquote as _unquote

    def _make_client() -> "Client":
        c = Client()
        c.delay_range = [2, 5]
        if cfg.get("proxy"):
            c.set_proxy(cfg["proxy"])
            log.info(f"프록시 사용: {cfg['proxy']}")
        return c

    def _try_sessionid(session_id: str) -> "Client | None":
        try:
            c = _make_client()
            c.login_by_sessionid(session_id)
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            c.dump_settings(str(SESSION_FILE))
            SESSION_FILE.chmod(0o600)
            log.info(f"sessionid 로그인 성공: {c.username}")
            return c
        except Exception as e:
            log.warning(f"sessionid 로그인 실패: {e}")
            return None

    # ── 1) session.json 재사용 ────────────────────────────────
    if SESSION_FILE.exists():
        try:
            client = _make_client()
            client.load_settings(str(SESSION_FILE))
            client.login(cfg["bot_username"], cfg["bot_password"])
            log.info("세션에서 로그인 성공")
            return client
        except Exception as e:
            log.warning(f"세션 로그인 실패: {e}")

    # ── 2) config.json bot_session_id 사용 ───────────────────
    if cfg.get("bot_session_id"):
        sid = _unquote(cfg["bot_session_id"])
        result = _try_sessionid(sid)
        if result:
            return result

    # ── 3) cookies.txt sessionid 사용 ─────────────────────────
    cookies_path = _find_cookies_file(cfg)
    if cookies_path:
        session_id = _extract_sessionid(cookies_path)
        if session_id:
            result = _try_sessionid(session_id)
            if result:
                return result

    # ── 4) username/password 로그인 ───────────────────────────
    log.info("username/password 로그인 시도...")
    client = _make_client()
    try:
        client.login(cfg["bot_username"], cfg["bot_password"])
    except ChallengeRequired:
        return _login_with_challenge(client, cfg)
    except TwoFactorRequired:
        code = input("2FA 코드 입력: ").strip()
        client.login(cfg["bot_username"], cfg["bot_password"], verification_code=code)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    client.dump_settings(str(SESSION_FILE))
    SESSION_FILE.chmod(0o600)
    log.info("로그인 성공. 세션 저장.")
    return client


def _extract_reel_url(msg) -> "str | None":
    """DM 메시지에서 릴스 URL 추출. xma_clip / xma_share / media_share 모두 처리."""
    import re
    REEL_PATTERN = re.compile(r"instagram\.com/(?:reel|reels|p)/([A-Za-z0-9_-]+)")

    # ── xma_share (신형 DM 공유 방식) ─────────────────────────
    xma = getattr(msg, "xma_share", None)
    if xma:
        video_url = getattr(xma, "video_url", None)
        if video_url:
            m = REEL_PATTERN.search(str(video_url))
            if m:
                return f"https://www.instagram.com/reel/{m.group(1)}/"

    # ── media_share (구형) ────────────────────────────────────
    media = getattr(msg, "media_share", None)
    if media:
        code = getattr(media, "code", None)
        if code:
            return f"https://www.instagram.com/p/{code}/"

    # ── clip (일부 버전) ──────────────────────────────────────
    clip = getattr(msg, "clip", None)
    if clip:
        media = getattr(clip, "clip", None) or clip
        code = getattr(media, "code", None)
        if code:
            return f"https://www.instagram.com/reel/{code}/"

    return None


def _find_cookies_file(cfg: dict) -> "Path | None":
    """config 또는 기본 위치에서 cookies.txt 탐색."""
    candidates = []
    if cfg.get("cookies_path"):
        candidates.append(Path(cfg["cookies_path"]))
    if cfg.get("dataset_root"):
        candidates.append(Path(cfg["dataset_root"]) / "cookies.txt")
    candidates.append(Path.home() / "Workspace" / "reels-catcher_output" / "cookies.txt")

    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _extract_sessionid(cookies_path: Path) -> "str | None":
    """Netscape cookies.txt 에서 instagram sessionid 추출."""
    try:
        for line in cookies_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7 and "instagram.com" in parts[0] and parts[5] == "sessionid":
                from urllib.parse import unquote
                return unquote(parts[6])
    except Exception as e:
        log.warning(f"cookies.txt 파싱 실패: {e}")
    return None


def _login_with_challenge(client, cfg: dict):
    """ChallengeRequired 처리 — step_name에 따라 분기."""
    last_json = client.last_json
    step_name = last_json.get("step_name", "")
    step_data = last_json.get("step_data", {})

    print("\n" + "=" * 50)
    print("  Instagram 보안 인증 필요")
    print(f"  step: {step_name}")
    print("=" * 50)

    # ── 1. '본인이었나요?' 확인 요청 ─────────────────────────
    if step_name == "delta_login_review":
        print("  Instagram 앱에서 '본인이 맞습니다' 를 눌러주세요.")
        print("  앱에서 승인 후 Enter를 누르세요.")
        input("  [Enter] ")
        try:
            client.challenge_resolve(last_json, choice=0)  # 0 = it was me
            return _save_and_return(client)
        except Exception as e:
            log.error(f"앱 승인 처리 실패: {e}")
            raise

    # ── 2. 인증 방법 선택 ─────────────────────────────────────
    if step_name == "select_verify_method":
        phone = step_data.get("phone_number", "")
        email = step_data.get("email", "")
        print(f"  인증 방법 선택:")
        if phone:
            print(f"  0 → SMS  ({phone})")
        if email:
            print(f"  1 → 이메일 ({email})")

        choice = input("  선택 (0 또는 1): ").strip()
        try:
            if choice == "0" and phone:
                client.challenge_send_phone_number(
                    last_json.get("challenge", {}).get("api_path", "")
                )
            else:
                client.challenge_send_email(
                    last_json.get("challenge", {}).get("api_path", "")
                )
            log.info("인증 코드 발송 완료")
        except Exception as e:
            log.warning(f"코드 발송 요청 실패: {e} — 코드 입력창으로 이동")

    # ── 3. 코드 입력 (이메일/SMS) ─────────────────────────────
    else:
        # 기본: 코드 발송 먼저 시도
        try:
            client.challenge_resolve(last_json)
            log.info("인증 코드 발송 요청 완료")
        except Exception as e:
            log.warning(f"코드 발송 요청 실패: {e}")

    print("\n  이메일 또는 SMS로 받은 6자리 코드를 입력하세요.")
    print("  코드가 오지 않으면 Instagram 앱 > 알림 확인 후 Enter.")
    code = input("  코드: ").strip()

    if not code:
        raise RuntimeError("코드 미입력 — 로그인 취소")

    client.challenge_resolve(last_json, security_code=code)
    return _save_and_return(client)


def _save_and_return(client):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    client.dump_settings(str(SESSION_FILE))
    SESSION_FILE.chmod(0o600)
    log.info("챌린지 인증 성공. 세션 저장.")
    return client


def _process_threads(client, seen: set[str], cfg: dict) -> int:
    """
    DM 스레드를 검색해 새 릴스 공유를 처리.
    반환값: 처리한 릴스 수.
    """
    from reels_catcher.downloader import download
    from reels_catcher.classifier import get_classifier
    from reels_catcher.metadata import parse_info_json, save_metadata
    from reels_catcher.obsidian_writer import write_note

    dataset_root = Path(cfg.get("dataset_root", str(Path.home() / "Workspace" / "reels-catcher_output")))
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "_index").mkdir(parents=True, exist_ok=True)

    processed = 0

    try:
        threads = client.direct_threads(amount=20)
    except Exception as e:
        log.error(f"DM 스레드 조회 실패: {e}")
        return 0

    for thread in threads:
        try:
            messages = client.direct_messages(thread.id, amount=20)
        except Exception as e:
            log.warning(f"스레드 {thread.id} 메시지 조회 실패: {e}")
            continue

        for msg in messages:
            msg_id = str(msg.id)
            if msg_id in seen:
                continue

            # 릴스 URL 추출 (xma_clip / xma_share 또는 media_share)
            url = _extract_reel_url(msg)
            if not url:
                seen.add(msg_id)
                continue

            # URL에서 shortcode 추출
            import re as _re
            _m = _re.search(r"instagram\.com/(?:reel|reels|p)/([A-Za-z0-9_-]+)", url)
            shortcode = _m.group(1) if _m else None
            if not shortcode:
                log.warning(f"shortcode 추출 실패: {url}")
                seen.add(msg_id)
                continue

            log.info(f"새 릴스 감지: {url} (shortcode={shortcode})")

            try:
                result = download(url, str(dataset_root))
                if not result["success"]:
                    log.error(f"다운로드 실패: {result['error']}")
                    seen.add(msg_id)
                    continue

                metadata = parse_info_json(
                    result["info_json_path"], shortcode, str(dataset_root)
                )
                metadata_path = dataset_root / shortcode / "metadata.json"
                save_metadata(metadata, str(metadata_path))

                classifier = get_classifier("auto")
                metadata["tags"] = classifier.classify(metadata)
                save_metadata(metadata, str(metadata_path))

                note_path = write_note(metadata, str(dataset_root))
                game_title = (metadata.get("tags") or {}).get("game_title") or "Unknown Game"
                log.info(f"✅ {game_title} 저장 → {note_path.relative_to(dataset_root)}")
                processed += 1

            except Exception as e:
                log.error(f"처리 실패 {url}: {e}")

            seen.add(msg_id)
            time.sleep(random.uniform(1, 3))

    _save_seen(seen)
    return processed


def start_watching(poll_interval_seconds: int = 30) -> None:
    """
    포어그라운드에서 무한 루프로 DM을 폴링.
    터미널 종료 또는 Ctrl+C로 종료.
    """
    cfg = _load_config()
    seen = _load_seen()

    try:
        client = _get_client(cfg)
    except Exception as e:
        log.error(f"로그인 실패: {e}")
        log.error("'reels-catcher auth'를 실행해 세션을 갱신하세요.")
        return

    log.info(f"DM 폴링 시작 (간격: {poll_interval_seconds}초)")

    while True:
        try:
            count = _process_threads(client, seen, cfg)
            if count:
                log.info(f"이번 폴링에서 {count}개 릴스 처리 완료")
            else:
                log.info("새 릴스 없음")
        except Exception as e:
            log.error(f"폴링 오류: {e}")
            # 세션 만료 처리
            if "login" in str(e).lower() or "unauthorized" in str(e).lower():
                log.error("세션이 만료되었습니다. 'reels-catcher auth'를 실행하세요.")
                break

        # 폴링 간격 대기 (jitter ±5초)
        sleep_sec = poll_interval_seconds + random.uniform(-5, 5)
        sleep_sec = max(sleep_sec, 10)
        log.info(f"다음 폴링까지 {sleep_sec:.0f}초 대기...")
        time.sleep(sleep_sec)
