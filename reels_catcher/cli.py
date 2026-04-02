"""
reels-catcher CLI 진입점.
Entry point: reels-catcher = "reels_catcher.cli:cli"
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import builtins
from collections import Counter
from pathlib import Path

import click

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

from reels_catcher.downloader import download
from reels_catcher.classifier import get_classifier
from reels_catcher.metadata import parse_info_json, save_metadata
from reels_catcher.obsidian_writer import write_note

# ── 기본 경로 ──────────────────────────────────────────────────
DEFAULT_DATASET_ROOT = Path.home() / "Workspace" / "reels-catcher_output"
DEFAULT_OBSIDIAN_VAULT = Path.home() / "Obsidian" / "obsidian"
CONFIG_DIR = Path.home() / ".local" / "share" / "reels-catcher"
CONFIG_FILE = CONFIG_DIR / "config.json"

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

INDEX_COLUMNS = [
    "ad_id", "source_url", "collected_at", "game_title", "developer",
    "genre", "art_style", "ad_hook_type", "target_audience", "visual_quality",
    "duration_sec", "view_count", "is_paid_partnership", "uploader",
]

load_dotenv()


# ── 설정 로드 헬퍼 ──────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def _resolved_dataset_root() -> Path:
    cfg = load_config()
    val = cfg.get("dataset_root") or os.getenv("DATASET_ROOT", str(DEFAULT_DATASET_ROOT))
    return Path(val).expanduser().resolve()


def _resolved_classifier_mode(mode: str | None) -> str:
    return (mode or os.getenv("CLASSIFIER") or "auto").strip().lower()


# ── 파일 경로 ──────────────────────────────────────────────────
def _ensure_dataset_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_index").mkdir(parents=True, exist_ok=True)


def _index_path(root: Path) -> Path:
    return root / "_index" / "index.csv"


def _metadata_json_path(root: Path, shortcode: str) -> Path:
    return root / shortcode / "metadata.json"


def _gallery_path(root: Path) -> Path:
    return root / "_index" / "gallery.md"


def _write_gallery_file(root: Path) -> Path:
    path = _gallery_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GALLERY_TEMPLATE, encoding="utf-8")
    return path


def _ensure_obsidian_symlink(target: Path, obsidian_vault: Path) -> Path:
    link_path = obsidian_vault / "reels-catcher"
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        current_target = link_path.resolve(strict=False)
        if current_target == target:
            return link_path
        link_path.unlink()
    elif link_path.exists():
        raise click.ClickException(f"Cannot replace existing path: {link_path}")
    link_path.symlink_to(target, target_is_directory=True)
    return link_path


def _load_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _index_row_from_metadata(metadata: dict) -> dict:
    tags = metadata.get("tags") or {}
    return {
        "ad_id": metadata.get("ad_id"),
        "source_url": metadata.get("source_url"),
        "collected_at": metadata.get("collected_at"),
        "game_title": tags.get("game_title"),
        "developer": tags.get("developer"),
        "genre": ";".join(tags.get("genre") or []),
        "art_style": ";".join(tags.get("art_style") or []),
        "ad_hook_type": tags.get("ad_hook_type"),
        "target_audience": tags.get("target_audience"),
        "visual_quality": tags.get("visual_quality"),
        "duration_sec": metadata.get("duration_sec"),
        "view_count": metadata.get("view_count"),
        "is_paid_partnership": metadata.get("is_paid_partnership"),
        "uploader": metadata.get("uploader"),
    }


def _load_index_rows(root: Path) -> list[dict]:
    path = _index_path(root)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return builtins.list(csv.DictReader(handle))


def _write_index_rows(root: Path, rows: list[dict]) -> None:
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _update_index(root: Path, metadata: dict) -> None:
    new_row = _index_row_from_metadata(metadata)
    rows = _load_index_rows(root)
    updated = False
    for index, row in enumerate(rows):
        if row.get("ad_id") == new_row["ad_id"]:
            rows[index] = new_row
            updated = True
            break
    if not updated:
        rows.append(new_row)
    _write_index_rows(root, rows)


def _filter_rows(rows: list[dict], game: str | None, genre: str | None, hook: str | None) -> list[dict]:
    filtered = rows
    if game:
        needle = game.lower()
        filtered = [row for row in filtered if needle in (row.get("game_title") or "").lower()]
    if genre:
        needle = genre.lower()
        filtered = [row for row in filtered if needle in (row.get("genre") or "").lower()]
    if hook:
        needle = hook.lower()
        filtered = [row for row in filtered if needle in (row.get("ad_hook_type") or "").lower()]
    return filtered


def _echo_table(rows: list[dict]) -> None:
    columns = ["ad_id", "game_title", "genre", "ad_hook_type", "uploader", "collected_at"]
    display_rows = [{column: row.get(column) or "" for column in columns} for row in rows]
    widths = {
        column: max(len(column), *(len(str(row[column])) for row in display_rows))
        for column in columns
    }
    header = " | ".join(column.ljust(widths[column]) for column in columns)
    separator = "-+-".join("-" * widths[column] for column in columns)
    click.echo(header)
    click.echo(separator)
    for row in display_rows:
        click.echo(" | ".join(str(row[column]).ljust(widths[column]) for column in columns))


def _echo_csv(rows: list[dict]) -> None:
    writer = csv.DictWriter(sys.stdout, fieldnames=INDEX_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)


# ── CLI ────────────────────────────────────────────────────────
@click.group()
def cli():
    """게임 광고 릴스 수집 & 분류 데이터셋 도구"""
    pass


# ── setup ─────────────────────────────────────────────────────
@cli.command()
def setup():
    """최초 세팅: 계정정보 + 경로 설정 + Obsidian 연결"""
    from reels_catcher.setup_wizard import run_setup
    run_setup()


# ── auth ──────────────────────────────────────────────────────
@cli.command()
def auth():
    """Instagram 봇 계정 로그인 재시도 (2FA 지원)"""
    from reels_catcher.setup_wizard import login_and_save_session
    cfg = load_config()
    if not cfg.get("bot_username"):
        click.echo("설정이 없습니다. 먼저 reels-catcher setup을 실행하세요.")
        raise SystemExit(1)
    login_and_save_session(cfg["bot_username"], cfg["bot_password"])


# ── start ─────────────────────────────────────────────────────
@cli.command()
def start():
    """macOS Terminal.app 새 창에서 DM 와처를 시작"""
    script = 'tell application "Terminal" to do script "reels-catcher watch"'
    subprocess.run(["osascript", "-e", script])
    subprocess.run(["osascript", "-e", 'tell application "Terminal" to activate'])
    click.echo("DM 와처를 Terminal 창에서 시작했습니다.")
    click.echo("터미널 창을 닫으면 프로세스가 종료됩니다.")


# ── watch ─────────────────────────────────────────────────────
@cli.command()
def watch():
    """DM 와처를 포어그라운드에서 실행 (Ctrl+C로 종료)"""
    from reels_catcher.dm_watcher import start_watching
    cfg = load_config()
    interval = cfg.get("poll_interval_seconds", 30)
    click.echo(f"DM 와처 시작 — {interval}초 간격 폴링 — Ctrl+C로 종료")
    try:
        start_watching(interval)
    except KeyboardInterrupt:
        click.echo("\n종료됨.")


# ── add ───────────────────────────────────────────────────────
@cli.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--no-tag", is_flag=True, help="분류 스킵")
@click.option("--no-note", is_flag=True, help="Obsidian 노트 생성 스킵")
@click.option(
    "--classifier", "classifier_mode",
    type=click.Choice(["auto", "rule", "llm", "web"]),
    default=None,
    show_default="env CLASSIFIER or auto",
)
def add(urls, no_tag, no_note, classifier_mode):
    """인스타 릴스 URL 추가 (다운로드 + 분류 + 노트)"""
    root = _resolved_dataset_root()
    _ensure_dataset_dirs(root)
    selected_mode = _resolved_classifier_mode(classifier_mode)

    for url in urls:
        try:
            result = download(url, str(root))
            if not result["success"]:
                click.echo(f"ERROR {url}: {result['error']}")
                continue

            shortcode = result["shortcode"]
            metadata = parse_info_json(result["info_json_path"], shortcode, str(root))
            metadata_path = _metadata_json_path(root, shortcode)
            save_metadata(metadata, str(metadata_path))

            if not no_tag:
                classifier = get_classifier(selected_mode)
                metadata["tags"] = classifier.classify(metadata)
                save_metadata(metadata, str(metadata_path))

            output_path = metadata_path
            if not no_note:
                output_path = write_note(metadata, str(root))

            _update_index(root, metadata)
            game_title = (metadata.get("tags") or {}).get("game_title") or "Unknown Game"
            relative_output = output_path.relative_to(root)
            click.echo(f"✅ {game_title} saved → {relative_output}")
        except Exception as exc:
            click.echo(f"ERROR {url}: {exc}")


# ── list ──────────────────────────────────────────────────────
@cli.command(name="list")
@click.option("--game", default=None)
@click.option("--genre", default=None)
@click.option("--hook", default=None)
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "csv", "json"]))
def list_records(game, genre, hook, fmt):
    """수집된 광고 목록 출력"""
    root = _resolved_dataset_root()
    rows = _filter_rows(_load_index_rows(root), game, genre, hook)
    if not rows:
        click.echo("No records found.")
        return
    if fmt == "json":
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
    elif fmt == "csv":
        _echo_csv(rows)
    else:
        _echo_table(rows)


# ── stats ─────────────────────────────────────────────────────
@cli.command()
def stats():
    """데이터셋 통계"""
    root = _resolved_dataset_root()
    rows = _load_index_rows(root)
    if not rows:
        click.echo("No records found.")
        return

    genre_counter: Counter[str] = Counter()
    hook_counter: Counter[str] = Counter()
    for row in rows:
        genres = [item.strip() for item in (row.get("genre") or "").split(";") if item.strip()]
        for genre_name in genres:
            genre_counter[genre_name] += 1
        hook_counter[row.get("ad_hook_type") or "unknown"] += 1

    click.echo(f"Total ads: {len(rows)}")
    click.echo("\nGenre distribution:")
    for name, count in genre_counter.most_common():
        click.echo(f"  {name}: {count}")
    click.echo("\nHook distribution:")
    for name, count in hook_counter.most_common():
        click.echo(f"  {name}: {count}")


# ── retag ─────────────────────────────────────────────────────
@cli.command()
@click.argument("shortcode")
@click.option("--classifier", "classifier_mode",
              type=click.Choice(["auto", "rule", "llm", "web"]), default=None)
def retag(shortcode, classifier_mode):
    """특정 광고만 분류 재실행"""
    root = _resolved_dataset_root()
    metadata_path = _metadata_json_path(root, shortcode)
    if not metadata_path.exists():
        click.echo(f"Metadata not found: {metadata_path}")
        return
    try:
        metadata = _load_metadata(metadata_path)
        classifier = get_classifier(_resolved_classifier_mode(classifier_mode))
        metadata["tags"] = classifier.classify(metadata)
        save_metadata(metadata, str(metadata_path))
        note_path = write_note(metadata, str(root))
        _update_index(root, metadata)
        click.echo(f"Retagged {shortcode} → {note_path.relative_to(root)}")
    except Exception as exc:
        click.echo(f"ERROR {shortcode}: {exc}")


if __name__ == "__main__":
    cli()
