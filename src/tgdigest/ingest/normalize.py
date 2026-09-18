"""Text normalization shared by classifiers, exact-hash dedup and indexing."""

from __future__ import annotations

import re
import unicodedata

_INVISIBLE = re.compile(r"[​‌‍⁠﻿­]")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]\"']+", re.IGNORECASE)
_TG_LINK = re.compile(r"(?<![\w/])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
_WS = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_EMOJI_ONLY_LINE = re.compile(r"^[\W_]+$", re.UNICODE)
_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‘": "'"})


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for m in _URL.finditer(text):
        url = m.group(0).rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    return seen


def extract_mentions(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _TG_LINK.finditer(text)})


def normalize_text(text: str) -> str:
    """NFC, no invisible chars, unified quotes, collapsed whitespace, decorative lines dropped."""
    t = unicodedata.normalize("NFC", text or "")
    t = _INVISIBLE.sub("", t).translate(_QUOTES)
    lines = []
    for line in t.replace("\r\n", "\n").split("\n"):
        line = _WS.sub(" ", line).strip()
        if line and not _EMOJI_ONLY_LINE.match(line):
            lines.append(line)
    return _BLANK_LINES.sub("\n", "\n".join(lines)).strip()


def dedup_key(text: str) -> str:
    """Aggressive form for exact-duplicate detection: lowercase, no URLs/mentions/punctuation."""
    t = normalize_text(text).lower()
    t = _URL.sub(" ", t)
    t = _TG_LINK.sub(" ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()
