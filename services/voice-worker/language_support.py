"""Detect the user's spoken language and map it to Chatterbox TTS codes."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Chatterbox Multilingual ISO codes (subset commonly used; unknown → en).
CHATTERBOX_LANGUAGES = frozenset(
    {
        "ar",
        "da",
        "de",
        "el",
        "en",
        "es",
        "fi",
        "fr",
        "he",
        "hi",
        "it",
        "ja",
        "ko",
        "ms",
        "nl",
        "no",
        "pl",
        "pt",
        "ru",
        "sv",
        "sw",
        "tr",
        "zh",
    }
)

_SCRIPT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\u0900-\u097F]"), "hi"),  # Devanagari
    (re.compile(r"[\u0400-\u04FF]"), "ru"),  # Cyrillic
    (re.compile(r"[\u0600-\u06FF]"), "ar"),  # Arabic
    (re.compile(r"[\u0590-\u05FF]"), "he"),  # Hebrew
    (re.compile(r"[\u3040-\u30FF]"), "ja"),  # Hiragana/Katakana
    (re.compile(r"[\uAC00-\uD7AF]"), "ko"),  # Hangul
    (re.compile(r"[\u4E00-\u9FFF]"), "zh"),  # CJK
)


@dataclass
class ConversationLanguage:
    """Session language inferred from the user's latest utterances."""

    code: str = "en"

    def observe_user_text(self, text: str) -> str:
        detected = detect_language_code(text)
        if detected:
            self.code = detected
        return self.code

    @property
    def tts_language(self) -> str:
        if self.code in CHATTERBOX_LANGUAGES:
            return self.code
        return "en"


def detect_language_code(text: str) -> str | None:
    """Best-effort language id for reply + TTS selection."""
    sample = (text or "").strip()
    if len(sample) < 2:
        return None

    for pattern, code in _SCRIPT_HINTS:
        if pattern.search(sample):
            return code

    try:
        from langdetect import DetectorFactory, detect
        from langdetect.lang_detect_exception import LangDetectException
    except ImportError:
        return None

    DetectorFactory.seed = 0
    try:
        code = detect(sample)
    except LangDetectException:
        return None

    aliases = {
        "zh-cn": "zh",
        "zh-tw": "zh",
        "pt-br": "pt",
        "pt-pt": "pt",
    }
    code = aliases.get(code, code)
    if code in CHATTERBOX_LANGUAGES:
        return code
    return None
