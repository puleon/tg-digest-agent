"""Cheap first-line detector for instructions addressed to the model (SPEC §8.4, defense 2).

Heuristic: v0 phrases (jailbreak vocabulary) plus, since D17, generic "instructions about the
answer" markers — a model being addressed, or a demand on what the answer must contain. The
flag only gates what reaches the agent's context, it never blocks indexing. Its coverage of
the D17 attack set is reported in docs/experiments/d17-injection (the set was seen when the
v1 patterns were written; they are generic by construction, but that is in-sample).
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
    r"игнорируй (все )?(предыдущие|прошлые|системные|свои)? ?(инструкции|указания|промпт)",
    r"забудь (все )?(предыдущие|прошлые) (инструкции|указания)",
    r"(выведи|покажи|напечатай|раскрой) (свой |системный |свои )?(промпт|инструкции)",
    r"системн(ый|ого|ому) промпт",
    r"(поставь|добавь|включи|помести) (этот пост|это|этот канал|канал)"
    r"( первым)?( в (дайджест|подписки|выдачу|топ))?",
    r"вызови (инструмент|функцию)",
    # v1 (D17): instructions about the *answer* — who is addressed, or what the answer must
    # contain — make no sense in a post about films or memes
    r"(ассистент\w*|нейросет\w*|языков\w+ модел\w*|\bии\b|\bai\b|assistant|language model|"
    r"\bllm\b|grader)[^.\n]{0,40}\b(ответь|отвечай|добавь|включи|начни|заверши|закончи|выведи|"
    r"напиши|замени|поставь|оцени|reply|answer|include|append|start|end|begin|print|respond|"
    r"mark|grade)\b",
    r"\b(для|for) (ии|нейросет\w*|ассистент\w*|языков\w+ модел\w*|ai( assistants?)?|"
    r"the (model|assistant)|models?)\b",
    r"(инструкция системы|system note|assistant note|note to ai)",
    r"(grade (it|this)|mark (this|it) as relevant|relevant\s*[:=]\s*true|relevance\s*:\s*[0-9]|"
    r"оцени (его|этот пост) как|поставь (этот пост )?первым)",
    r"(закончи|заверши|начни) (свой )?ответ (словом|строкой|кодом|со? )",
    r"(в конце|в начале) (своего )?ответа",
    r"\b(in|into|to) (every|the|your) (summary|answer|response)\b",
    r"ignore (your|the|all|any) (previous |prior |above )?instructions",
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
