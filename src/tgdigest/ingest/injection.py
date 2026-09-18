"""Cheap first-line detector for instructions addressed to the model (SPEC §8.4, defense 2).

Heuristic v0 — a classifier replaces it on D17; the flag only gates what reaches the agent's
context, it never blocks indexing.
"""

from __future__ import annotations

import re

# One hit is enough: the phrase makes no sense unless it addresses a model or its tools.
_STRONG = [
    r"ignore (all )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (the )?(previous|prior|above|system) (instructions|prompt)",
    r"(reveal|print|show|output|leak) (the |your )?(system prompt|instructions)",
    r"\bsystem prompt\b",
    r"\bjailbreak\b",
    r"\bupdate_profile\b",
    r"call (the )?(tool|function) ",
    r"игнорируй (все )?(предыдущие|прошлые|системные) (инструкции|указания|промпт)",
    r"забудь (все )?(предыдущие|прошлые) (инструкции|указания)",
    r"(выведи|покажи|напечатай|раскрой) (свой |системный |свои )?(промпт|инструкции)",
    r"системн(ый|ого|ому) промпт",
    r"(поставь|добавь|включи|помести) (этот пост|это|этот канал|канал)"
    r"( первым)?( в (дайджест|подписки|выдачу|топ))?",
    r"вызови (инструмент|функцию)",
]
# Need two of these: suggestive on their own, common in ordinary posts.
_WEAK = [
    r"you are (now )?(an?|the) (assistant|ai|model|llm|language model)",
    r"as an ai( language model)?",
    r"ты (теперь )?(—|-|–)? ?(ассистент|ии|модель|нейросеть|языковая модель)",
    r"^(ассистент|assistant|система|system)[,:]",
    r"\b(assistant|system):",
    r"(instructions?|инструкци[яи])\b",
]
_STRONG_RX = re.compile("|".join(f"(?:{p})" for p in _STRONG), re.IGNORECASE | re.MULTILINE)
_WEAK_RX = re.compile("|".join(f"(?:{p})" for p in _WEAK), re.IGNORECASE | re.MULTILINE)


def injection_score(text: str) -> tuple[bool, list[str]]:
    """(flag, matched fragments): one strong phrase or two weak ones flag the text."""
    text = text or ""
    strong = [m.group(0) for m in _STRONG_RX.finditer(text)]
    weak = [m.group(0) for m in _WEAK_RX.finditer(text)]
    return (bool(strong) or len(weak) >= 2), strong + weak
