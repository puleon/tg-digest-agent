"""Digest plumbing: candidates from the interest ranking → crew → ``digests`` row."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgdigest.agent.tracing import Tracer, current_run
from tgdigest.db.models import Channel, Digest, Enrichment, Post, PostCluster
from tgdigest.digest.crew import (
    FRESH_DAYS,
    DigestResult,
    Pick,
    Plan,
    compose,
    coverage_gaps,
    curate,
    make_plan,
)
from tgdigest.llm.client import LLMClient
from tgdigest.profile.model import TOPICS
from tgdigest.profile.runner import load_profile, rank_candidates
from tgdigest.retrieval.index import PostIndex

log = structlog.get_logger(__name__)


async def shown_before(session: AsyncSession, user_id: int, *, issues: int = 10) -> set[int]:
    """Posts that appeared in the user's last ``issues`` digests (SPEC §6.7: never repeat)."""
    stmt = (
        select(Digest.items_json)
        .where(Digest.user_id == user_id)
        .order_by(Digest.created_at.desc())
        .limit(issues)
    )
    seen: set[int] = set()
    for (items,) in (await session.execute(stmt)).all():
        seen.update(int(it["post_id"]) for it in (items or []) if "post_id" in it)
    return seen


async def picks_for(
    factory: async_sessionmaker[AsyncSession],
    index: PostIndex,
    user_id: int,
    plan: Plan,
    *,
    pool: int = 120,
) -> list[Pick]:
    ranked = await rank_candidates(
        factory, index, user_id, days=plan.window_days, limit=pool, per_centroid=pool
    )
    ids = [r["post_id"] for r in ranked]
    if not ids:
        return []
    fresh_since = datetime.now(UTC) - timedelta(days=FRESH_DAYS)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Post, Channel.username, Channel.topic, Enrichment, PostCluster.cluster_id)
                .join(Channel, Channel.id == Post.channel_id)
                .outerjoin(Enrichment, Enrichment.post_id == Post.id)
                .outerjoin(PostCluster, PostCluster.post_id == Post.id)
                .where(Post.id.in_(ids))
            )
        ).all()
        exclude = await shown_before(session, user_id)
    score = {r["post_id"]: dict(r["score"]) for r in ranked}
    picks = [
        Pick(
            post_id=post.id,
            topic=topic,
            channel=username,
            channel_id=post.channel_id,
            score=float(score[post.id]["total"]),
            score_parts={k: v for k, v in score[post.id].items() if k != "total"},
            fresh=post.posted_at.replace(tzinfo=post.posted_at.tzinfo or UTC) >= fresh_since,
            cluster_id=cluster_id,
            text=post.text or "",
            ocr=(e.ocr_text or "") if e else "",
            caption=(e.vlm_caption or "") if e else "",
            url=f"https://t.me/{username}/{post.tg_message_id}",
            date=post.posted_at.strftime("%Y-%m-%d"),
        )
        for post, username, topic, e, cluster_id in rows
    ]
    return curate(picks, plan, exclude=frozenset(exclude))


async def make_digest(
    factory: async_sessionmaker[AsyncSession],
    index: PostIndex,
    llm: LLMClient,
    user_id: int,
    *,
    minutes: int = 10,
    window_days: int = 7,
    tier: str = "fast",
    store: bool = True,
    tracer: Tracer | None = None,
) -> tuple[DigestResult, int | None]:
    async with factory() as session:
        profile = await load_profile(session, user_id)
    weights = profile.topic_weights if profile else {t: 1 / 3 for t in TOPICS}
    plan = make_plan(weights, minutes=minutes, window_days=window_days)
    run = (
        tracer.start_run(
            "digest", input={"user_id": user_id, "plan": plan.to_json()}, user_id=str(user_id)
        )
        if tracer
        else None
    )
    token = current_run.set(run)
    try:
        step = run.step("curate", kind="retriever", input=plan.to_json()) if run else None
        picks = await picks_for(factory, index, user_id, plan)
        if step is not None:
            step.end(
                output={"picks": [p.post_id for p in picks], "gaps": coverage_gaps(picks, plan)}
            )
        result = await compose(llm, picks, plan, tier=tier)
    finally:
        current_run.reset(token)
    trace_id = run.trace_id if run else None
    if run is not None:
        run.end(
            output={"items": [it["post_id"] for it in result.items], "intro": result.intro},
            metadata={
                "critic_iterations": result.critic_iterations,
                "problems": result.critic_problems,
                "gaps": result.gaps,
                "tokens": result.usage.total,
                "degraded": result.degraded,
            },
        )
    result.trace_id = trace_id
    digest_id: int | None = None
    if store and result.items:
        async with factory() as session:
            row = Digest(
                user_id=user_id,
                plan_json=plan.to_json(),
                items_json=result.items,
                critic_iterations=result.critic_iterations,
                trace_id=trace_id,
            )
            session.add(row)
            await session.commit()
            digest_id = int(row.id)
    log.info(
        "digest_made",
        user_id=user_id,
        digest_id=digest_id,
        items=len(result.items),
        critic_iterations=result.critic_iterations,
        gaps=result.gaps,
        tokens=result.usage.total,
        seconds=result.seconds,
        degraded=result.degraded,
    )
    return result, digest_id


def render_text(result: DigestResult) -> str:
    """Plain-text issue for the terminal (the bot renders its own)."""
    lines = [result.intro] if result.intro else []
    for i, it in enumerate(result.items, 1):
        lines.append(f"\n{i}. {it['title']}  [{it['topic']} · @{it['channel']} · {it['date']}]")
        if it["why"]:
            lines.append(f"   {it['why']}")
        lines.append(f"   {it['url']}")
    if result.gaps:
        lines.append(f"\n(не хватило кандидатов: {result.gaps})")
    return "\n".join(lines)


def as_json(result: DigestResult, digest_id: int | None) -> dict[str, Any]:
    return {
        "digest_id": digest_id,
        "plan": result.plan.to_json(),
        "intro": result.intro,
        "items": result.items,
        "critic_iterations": result.critic_iterations,
        "critic_problems": result.critic_problems,
        "gaps": result.gaps,
        "usage": {
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
        },
        "seconds": result.seconds,
        "degraded": result.degraded,
        "trace_id": result.trace_id,
    }
