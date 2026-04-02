"""
텍스트 기반 광고 분류기 전략 패턴 구현.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - 런타임 환경 의존
    OpenAI = None

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - 런타임 환경 의존
    Anthropic = None


GENRE_RULES = {
    "Strategy": ["strategy", "base", "building", "rts", "왕국", "전략"],
    "RPG": ["rpg", "role", "quest", "fantasy", "hero", "영웅"],
    "Puzzle": ["puzzle", "match", "brain", "퍼즐", "match3"],
    "Casual": ["casual", "hyper", "fun", "mini"],
    "Action": ["action", "fight", "battle", "combat", "pvp", "전투"],
    "Idle": ["idle", "clicker", "afk", "방치"],
    "MOBA": ["moba", "arena", "league"],
    "Battle Royale": ["royale", "survival", "배틀로얄"],
    "Sports": ["sports", "football", "soccer", "basketball"],
    "Simulation": ["sim", "simulation", "farm", "city", "tycoon"],
    "Horror": ["horror", "scary", "survive", "zombie"],
}

HOOK_RULES = {
    "gameplay": ["gameplay", "play", "level", "stage", "mission"],
    "story": ["story", "lore", "legend", "tale", "narrative"],
    "character": ["hero", "character", "skin", "champion"],
    "challenge": ["challenge", "can you", "dare", "try"],
    "social_proof": ["million", "players", "#1", "top", "best", "rated"],
    "ugc_style": ["real player", "recorded", "my game"],
    "reward": ["free", "gift", "reward", "bonus", "earn"],
}

PUBLISHER_GAME_MAP = {
    "supercell": "Clash of Clans / Supercell",
    "clashofclans": "Clash of Clans",
    "mobilegames": None,
}

GAME_HASHTAGS = {
    "clashofclans": "Clash of Clans",
    "clashofclansgame": "Clash of Clans",
    "pubgmobile": "PUBG Mobile",
    "freefire": "Free Fire",
    "brawlstars": "Brawl Stars",
    "royalematch": "Royal Match",
    "candycrush": "Candy Crush",
}

LLM_PROMPT = """
다음은 인스타그램 게임 광고의 텍스트 정보다.
이미지 없이 텍스트만으로 아래 JSON을 추론하여 반환하라.
확실하지 않은 필드는 null로 반환한다.

입력:
{text_input}

출력 JSON:
{{
  "game_title": "게임명 또는 null",
  "developer": "개발사/퍼블리셔 또는 null",
  "genre": ["장르1"],
  "art_style": [],
  "ad_hook_type": "훅유형 또는 null",
  "target_audience": "타겟 또는 null",
  "ai_notes": "추가 관찰 또는 null"
}}
""".strip()


def _now_kst_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat()


def _empty_tags() -> dict:
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


def _normalize_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _extract_json_text(raw_text: str) -> str:
    stripped = (raw_text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return stripped[start : end + 1]
    return stripped


def _join_text_parts(values: list[str]) -> str:
    return " ".join(value for value in values if value).strip()


def _lower_tokens(metadata: dict) -> str:
    description = metadata.get("description") or ""
    hashtags = metadata.get("hashtags") or []
    hashtag_text = " ".join(str(tag) for tag in hashtags)
    return _join_text_parts([description, hashtag_text]).lower()


def _match_keywords(text: str, rules: dict[str, list[str]]) -> list[str]:
    matches = []
    for label, keywords in rules.items():
        if any(keyword.lower() in text for keyword in keywords):
            matches.append(label)
    return matches


def _extract_game_title(metadata: dict) -> str | None:
    uploader = (metadata.get("uploader") or "").strip().lower()
    if uploader in PUBLISHER_GAME_MAP:
        return PUBLISHER_GAME_MAP[uploader]

    uploader_id = (metadata.get("uploader_id") or "").strip().lower()
    if uploader_id in PUBLISHER_GAME_MAP:
        return PUBLISHER_GAME_MAP[uploader_id]

    hashtags = [str(tag).strip().lower() for tag in metadata.get("hashtags") or []]
    for hashtag in hashtags:
        if hashtag in GAME_HASHTAGS:
            return GAME_HASHTAGS[hashtag]

    description = metadata.get("description") or ""
    match = re.search(r'"([^"]+)"', description)
    if match:
        return match.group(1).strip() or None

    # 맵에 없으면 uploader 값을 그대로 game_title로 사용
    if uploader:
        return metadata.get("uploader", "").strip() or None
    return None


class BaseClassifier:
    def classify(self, metadata: dict) -> dict:
        raise NotImplementedError


class RuleBasedClassifier(BaseClassifier):
    def classify(self, metadata: dict) -> dict:
        text = _lower_tokens(metadata)
        genre = _match_keywords(text, GENRE_RULES)
        hook_matches = _match_keywords(text, HOOK_RULES)

        tags = _empty_tags()
        tags.update(
            {
                "game_title": _extract_game_title(metadata),
                "genre": genre,
                "ad_hook_type": hook_matches[0] if hook_matches else None,
                "classified_at": _now_kst_iso(),
                "classified_by": "rule_based",
            }
        )
        return tags


class LLMTextClassifier(BaseClassifier):
    def __init__(self) -> None:
        self._fallback = RuleBasedClassifier()

    def _text_input(self, metadata: dict) -> str:
        hashtags = ", ".join(str(tag) for tag in metadata.get("hashtags") or [])
        return "\n".join(
            [
                f"uploader: {metadata.get('uploader') or ''}",
                f"caption: {metadata.get('description') or ''}",
                f"hashtags: {hashtags}",
            ]
        )

    def _classify_with_openai(self, text_input: str) -> dict:
        if OpenAI is None:
            raise RuntimeError("openai package not installed")

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.responses.create(
            model="gpt-4o-mini",
            input=LLM_PROMPT.format(text_input=text_input),
        )
        return json.loads(_extract_json_text(response.output_text))

    def _classify_with_anthropic(self, text_input: str) -> dict:
        if Anthropic is None:
            raise RuntimeError("anthropic package not installed")

        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=500,
            messages=[{"role": "user", "content": LLM_PROMPT.format(text_input=text_input)}],
        )
        text = "".join(
            block.text for block in getattr(response, "content", []) if getattr(block, "text", None)
        ).strip()
        return json.loads(_extract_json_text(text))

    def classify(self, metadata: dict) -> dict:
        text_input = self._text_input(metadata)

        try:
            if os.getenv("OPENAI_API_KEY"):
                parsed = self._classify_with_openai(text_input)
            elif os.getenv("ANTHROPIC_API_KEY"):
                parsed = self._classify_with_anthropic(text_input)
            else:
                raise RuntimeError("No LLM API key configured")
        except Exception:
            return self._fallback.classify(metadata)

        tags = _empty_tags()
        tags.update(
            {
                "game_title": parsed.get("game_title"),
                "developer": parsed.get("developer"),
                "genre": _normalize_list(parsed.get("genre")),
                "art_style": _normalize_list(parsed.get("art_style")),
                "ad_hook_type": parsed.get("ad_hook_type"),
                "target_audience": parsed.get("target_audience"),
                "ai_notes": parsed.get("ai_notes"),
                "classified_at": _now_kst_iso(),
                "classified_by": "llm_text",
            }
        )
        return tags


class WebResearchClassifier(BaseClassifier):
    def classify(self, metadata: dict) -> dict:
        raise NotImplementedError("향후 구현 예정 (B안)")


def get_classifier(mode: str = "auto") -> BaseClassifier:
    normalized_mode = (mode or "auto").strip().lower()

    if normalized_mode == "auto":
        if os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"):
            return LLMTextClassifier()
        return RuleBasedClassifier()
    if normalized_mode == "rule":
        return RuleBasedClassifier()
    if normalized_mode == "llm":
        return LLMTextClassifier()
    if normalized_mode == "web":
        return WebResearchClassifier()
    raise ValueError(f"Unsupported classifier mode: {mode}")
