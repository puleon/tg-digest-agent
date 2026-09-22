"""Query rewriting (SPEC §6.4): conversational requests → post-like search queries, logged both."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from pydantic import BaseModel, Field

from tgdigest.llm.client import LLMClient, LLMOutputError
from tgdigest.prompts import load_prompt, prompt_id

log = structlog.get_logger(__name__)

REWRITE_PROMPT = ("rewrite_query", 2)
"""v2 asks for ``keywords`` explicitly: v1 left them empty on most requests, so the research
mode's Wikipedia lookup fell back to the whole conversational request and came up empty."""


class RewrittenQuery(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=3)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    """Distinctive words the posts would contain (titles, names) — for the lexical side."""


@dataclass
class Rewrite:
    original: str
    queries: list[str]
    keywords: list[str]
    prompt: str
    degraded: bool = False
    """True when the model failed and the original query is used as is."""


async def rewrite_query(llm: LLMClient, query: str) -> Rewrite:
    messages = [
        {"role": "system", "content": load_prompt(*REWRITE_PROMPT)},
        {"role": "user", "content": query.strip()},
    ]
    try:
        out, _ = await llm.structured(messages, RewrittenQuery, tier="fast", max_tokens=200)  # type: ignore[arg-type]
    except LLMOutputError as exc:
        log.warning("rewrite_failed", query=query, error=str(exc)[:120])
        return Rewrite(query, [query], [], prompt_id(*REWRITE_PROMPT), degraded=True)
    queries = [q.strip() for q in out.queries if q.strip()] or [query]
    log.info("query_rewritten", original=query, queries=queries, keywords=out.keywords)
    return Rewrite(
        query, queries, [k.strip() for k in out.keywords if k.strip()], prompt_id(*REWRITE_PROMPT)
    )
