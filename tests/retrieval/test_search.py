from __future__ import annotations

from tests.ingest.conftest import FakeLLM
from tgdigest.retrieval.index import Hit
from tgdigest.retrieval.rewrite import RewrittenQuery, rewrite_query
from tgdigest.retrieval.search import rrf_fuse


def _hits(*ids: int) -> list[Hit]:
    return [Hit(post_id=p, score=1.0 / (i + 1), payload={}, rank=i + 1) for i, p in enumerate(ids)]


def test_rrf_prefers_posts_found_by_several_phrasings() -> None:
    fused = rrf_fuse([_hits(1, 2, 3), _hits(3, 4), _hits(2, 5)])
    assert [h.post_id for h in fused] == [2, 3, 1, 4, 5]
    assert [h.rank for h in fused] == [1, 2, 3, 4, 5]
    assert rrf_fuse([_hits(7, 8)], limit=1)[0].post_id == 7
    assert rrf_fuse([]) == []


async def test_rewrite_uses_the_versioned_prompt_and_degrades_to_the_original() -> None:
    llm = FakeLLM()
    llm.labels = RewrittenQuery(queries=["мем про дедлайн", "дедлайн горит"], keywords=["дедлайн"])  # type: ignore[assignment]
    rw = await rewrite_query(llm, "что-нибудь смешное про дедлайны")  # type: ignore[arg-type]
    assert rw.queries == ["мем про дедлайн", "дедлайн горит"] and rw.keywords == ["дедлайн"]
    assert rw.prompt == "rewrite_query.v1" and not rw.degraded
    assert "search queries" in llm.calls[-1]["messages"][0]["content"]

    from tgdigest.llm.client import LLMOutputError

    class Broken(FakeLLM):
        async def structured(self, *a: object, **kw: object) -> tuple[object, object]:  # type: ignore[override]
            raise LLMOutputError("bad", "raw")

    rw = await rewrite_query(Broken(), "запрос")  # type: ignore[arg-type]
    assert rw.queries == ["запрос"] and rw.degraded
