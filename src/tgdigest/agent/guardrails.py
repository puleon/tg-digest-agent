"""Defenses against indirect prompt injection (SPEC §8.4); measured by scripts/injection_eval.py.

1. Structural separation — post text reaches the model only inside ``<<<POSTS … POSTS>>>``
   markers with the instruction that it is data (in every prompt file); nothing here.
2. Quarantine — a retrieved post whose text, OCR or caption contains instructions addressed
   to a model (``injection_flag`` from ingest, or the same heuristic at retrieval time for
   posts ingest has not reached) is dropped before grading and synthesis; the run records
   how many.
3. Writes — ``update_profile`` is never called from content: the graph has no LLM-driven tool
   selection at all, and the tool requires the user's confirmation (``tools.py``).
4. Output checks — an answer that quotes a prompt file at length is redacted; a URL that no
   source post or verified page contains is stripped (exfiltration through the answer).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from tgdigest.ingest.injection import injection_score
from tgdigest.prompts import PROMPTS_DIR

_URL = re.compile(r"https?://[^\s<>()\[\]\"']+", re.IGNORECASE)
LEAK_SPAN = 60
"""Chars of a prompt file appearing verbatim in an answer that count as a leak."""


@dataclass
class QuarantineResult:
    kept: list[dict[str, Any]]
    dropped: list[dict[str, Any]] = field(default_factory=list)


def suspicious(hit: dict[str, Any]) -> tuple[bool, list[str]]:
    """Ingest's flag when present, otherwise the same heuristic on what the model would see."""
    if hit.get("injection_flag") is True:
        return True, ["injection_flag"]
    text = "\n".join(str(hit.get(k) or "") for k in ("text", "ocr", "caption"))
    return injection_score(text)


def quarantine(hits: Iterable[dict[str, Any]]) -> QuarantineResult:
    out = QuarantineResult(kept=[])
    for h in hits:
        flagged, why = suspicious(h)
        if flagged:
            out.dropped.append({**h, "why_dropped": why[:3]})
        else:
            out.kept.append(h)
    return out


@cache
def _prompt_texts() -> tuple[str, ...]:
    return tuple(
        " ".join(p.read_text(encoding="utf-8").split()) for p in sorted(PROMPTS_DIR.glob("*.md"))
    )


def leaked_prompt_span(answer: str, *, span: int = LEAK_SPAN) -> str | None:
    """The first ``span``-char window of a prompt file found verbatim in the answer, if any."""
    flat = " ".join(answer.split())
    if len(flat) < span:
        return None
    for prompt in _prompt_texts():
        for start in range(0, max(1, len(prompt) - span + 1), span // 3):
            window = prompt[start : start + span]
            if len(window) == span and window in flat:
                return window
    return None


def foreign_urls(answer: str, allowed_sources: Sequence[str]) -> list[str]:
    """URLs in the answer that appear in none of the texts the answer was written from."""
    corpus = "\n".join(allowed_sources)
    found = [u.rstrip(".,;:!?)") for u in _URL.findall(answer)]
    return [u for u in found if u not in corpus]


def check_output(answer: str, allowed_sources: Sequence[str]) -> tuple[str, list[str]]:
    """Redact leaks and strip foreign URLs; returns the cleaned answer and what was done."""
    notes: list[str] = []
    cleaned = answer
    window = leaked_prompt_span(cleaned)
    if window is not None:
        cleaned = "Ответ скрыт: он воспроизводил служебные инструкции системы."
        notes.append("output:prompt_leak_redacted")
        return cleaned, notes
    bad = foreign_urls(cleaned, allowed_sources)
    for u in bad:
        cleaned = cleaned.replace(u, "[ссылка удалена]")
    if bad:
        notes.append(f"output:urls_stripped:{len(bad)}")
    return cleaned, notes
