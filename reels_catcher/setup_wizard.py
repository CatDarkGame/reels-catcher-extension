"""
인터랙티브 세팅 위저드.
- 경로, 계정정보, 폴링 간격 입력
- ~/.local/share/reels-catcher/config.json 저장
- instagrapi 로그인 테스트 + session 저장
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

CONFIG_DIR = Path.home() / ".local" / "share" / "reels-catcher"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSION_FILE = CONFIG_DIR / "session.json"

DEFAULT_DATASET_ROOT = str(Path.home() / "Workspace" / "reels-catcher_output")
DEFAULT_OBSIDIAN_VAULT = str(Path.home() / "Obsidian" / "obsidian")
DEFAULT_POLL_INTERVAL = 30  # seconds

GALLERY_TEMPLATE = """---
cssclasses:
  - wide-page
---

# 릴스 광고 갤러리

```datacards
TABLE thumbnail_path AS thumbnail, game_title, genre, ad_hook_type, view_count, collected_at
FROM "reels-catcher"
WHERE file.folder != "reels-catcher/_index"
SORT collected_at DESC

// Settings
preset: portrait
columns: 4
imageProperty: thumbnail
imageHeight: 180
```
"""


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    CONFIG_FILE.chmod(0o600)


def _setup_output_dir(dataset_root: str) -> None:
    root = Path(dataset_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "_index").mkdir(parents=True, exist_ok=True)
    gallery_path = root / "_index" / "gallery.md"
    if not gallery_path.exists():
        gallery_path.write_text(GALLERY_TEMPLATE, encoding="utf-8")
    click.echo(f"  output 디렉토리: {root}")


def _setup_obsidian_symlink(dataset_root: str, obsidian_vault: str) -> None:
    target = Path(dataset_root).expanduser().resolve()
    vault = Path(obsidian_vault).expanduser().resolve()
    link_path = vault / "reels-catcher"

    if not vault.exists():
        click.echo(f"  [경고] Obsidian vault 경로가 없습니다: {vault}")
        click.echo("  나중에 직접 symlink를 만들거나 경로를 수정하세요.")
        return

    if link_path.is_symlink():
        link_path.unlink()
    elif link_path.exists():
        click.echo(f"  [경고] {link_path} 이 이미 존재합니다. symlink 생성을 스킵합니다.")
        return

    link_path.symlink_to(target, target_is_directory=True)
    click.echo(f"  Obsidian 링크: {link_path} → {target}")


def login_and_save_session(username: str, password: str) -> bool:
    """instagrapi 로그인 후 session.json 저장. 성공 여부 반환."""
    try:
        from instagrapi import Client
        from instagrapi.exceptions import TwoFactorRequired, ChallengeRequired
    except ImportError:
        click.echo("[오류] instagrapi가 설치되지 않았습니다. pip install instagrapi")
        return False

    client = Client()
    client.delay_range = [2, 5]

    def _save(c: "Client") -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        c.dump_settings(str(SESSION_FILE))
        SESSION_FILE.chmod(0o600)
        click.echo(f"  로그인 성공. 세션 저장: {SESSION_FILE}")

    try:
        click.echo(f"  {username} 로그인 시도 중...")
        client.login(username, password)
        _save(client)
        return True

    except TwoFactorRequired:
        click.echo("  2FA 인증이 필요합니다.")
        code = click.prompt("  Authenticator 앱 코드 입력")
        try:
            client.login(username, password, verification_code=code)
            _save(client)
            return True
        except Exception as e:
            click.echo(f"  [오류] 2FA 로그인 실패: {e}")
            return False

    except ChallengeRequired:
        click.echo("  Instagram 보안 인증이 필요합니다.")
        return _handle_challenge(client, username, password)

    except Exception as e:
        click.echo(f"  [오류] 로그인 실패: {e}")
        click.echo("  계정 정보를 확인하거나 나중에 'reels-catcher auth'를 실행하세요.")
        return False


def _handle_challenge(client, username: str, password: str) -> bool:
    """Instagram challenge_required 처리 (이메일/SMS 코드 입력)."""
    try:
        from instagrapi.exceptions import ChallengeRequired
    except ImportError:
        return False

    try:
        # 챌린지 방법 선택 요청 (0=SMS, 1=이메일)
        challenge = client.last_json.get("challenge", {})
        api_path = challenge.get("api_path", "")
        click.echo(f"  챌린지 유형: {challenge.get('challengeType', 'unknown')}")

        # 코드 발송 요청
        try:
            # 이메일로 코드 발송 시도
            client.challenge_resolve(client.last_json)
            click.echo("  인증 코드를 이메일 또는 SMS로 발송했습니다.")
        except Exception:
            pass

        code = click.prompt("  받은 인증 코드 입력 (6자리)")
        code = code.strip()

        client.challenge_resolve(client.last_json, security_code=code)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        client.dump_settings(str(SESSION_FILE))
        SESSION_FILE.chmod(0o600)
        click.echo(f"  인증 성공. 세션 저장: {SESSION_FILE}")
        return True

    except Exception as e:
        click.echo(f"  [오류] 챌린지 인증 실패: {e}")
        click.echo("")
        click.echo("  수동 해결 방법:")
        click.echo("  1. Instagram 앱 또는 웹에서 해당 계정으로 직접 로그인")
        click.echo("  2. 보안 인증을 완료한 뒤")
        click.echo("  3. 다시 'reels-catcher auth' 실행")
        return False


def run_setup() -> None:
    """인터랙티브 세팅 위저드 실행"""
    click.echo("=" * 50)
    click.echo("  reels-catcher 초기 설정")
    click.echo("=" * 50)

    existing = _load_config()

    # 1. 출력 경로
    click.echo("\n[1/5] 출력 경로 (릴스 영상·노트 저장 위치)")
    dataset_root = click.prompt(
        "  경로",
        default=existing.get("dataset_root", DEFAULT_DATASET_ROOT),
    )

    # 2. Obsidian vault 경로
    click.echo("\n[2/5] Obsidian vault 경로")
    obsidian_vault = click.prompt(
        "  Obsidian vault 경로",
        default=existing.get("obsidian_vault", DEFAULT_OBSIDIAN_VAULT),
    )

    # 3. Instagram 봇 계정
    click.echo("\n[3/5] Instagram 봇 계정 (전용 계정 권장)")
    bot_username = click.prompt(
        "  봇 계정 username",
        default=existing.get("bot_username", ""),
    )
    bot_password = click.prompt(
        "  봇 계정 password",
        default=existing.get("bot_password", ""),
        hide_input=True,
        confirmation_prompt=False,
    )

    # 4. 폴링 간격
    click.echo("\n[4/5] DM 폴링 간격 (초 단위)")
    poll_interval = click.prompt(
        "  폴링 간격 (초)",
        default=existing.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL),
        type=int,
    )

    # 5. 저장
    cfg = {
        "dataset_root": str(Path(dataset_root).expanduser().resolve()),
        "obsidian_vault": str(Path(obsidian_vault).expanduser().resolve()),
        "bot_username": bot_username,
        "bot_password": bot_password,
        "poll_interval_seconds": poll_interval,
    }

    click.echo("\n[5/5] 환경 구성 중...")
    _setup_output_dir(cfg["dataset_root"])
    _setup_obsidian_symlink(cfg["dataset_root"], cfg["obsidian_vault"])
    _save_config(cfg)
    click.echo(f"  설정 저장: {CONFIG_FILE}")

    # 로그인 테스트
    click.echo("\n Instagram 로그인 테스트...")
    login_and_save_session(bot_username, bot_password)

    click.echo("\n" + "=" * 50)
    click.echo("  설정 완료!")
    click.echo("  시작: reels-catcher start")
    click.echo("  직접 URL 추가: reels-catcher add <URL>")
    click.echo("=" * 50)
