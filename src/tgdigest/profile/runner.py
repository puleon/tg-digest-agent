"""Profile plumbing: onboarding sample, feedback rows, profile build and candidate ranking."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from qdrant_client import models
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgdigest.db.models import Channel, Enrichment, Feedback, Post, UserProfile
from tgdigest.profile.model import (
    TOPICS,
    Candidate,
    Profile,
    Vote,
    Weights,
    baseline_rank,
    build_profile,
    rank,
)
from tgdigest.retrieval.index import PostIndex

log = structlog.get_logger(__name__)

SIGNALS = ("like", "dislike", "save", "skip")


@dataclass
class OnboardingPost:
    post_id: int
    channel: str
    topic: str
    posted_at: datetime
    text: str
    media_path: str | None


async def onboarding_sample(
    session: AsyncSession,
    *,
    per_topic: int = 12,
    max_per_channel: int = 2,
    since: datetime | None = None,
    seed: int = 0,
) -> list[OnboardingPost]:
    """Stratified cold-start sample (SPEC §6.6): per topic, spread over channels; posts
    labelled as ads are excluded (unlabelled ones stay: enrichment may still be running)."""
    since = since or datetime.now(UTC) - timedelta(days=90)
    stmt = (
        select(Post, Channel.username, Channel.topic)
        .join(Channel, Channel.id == Post.channel_id)
        .outerjoin(Enrichment, Enrichment.post_id == Post.id)
        .where(Post.posted_at >= since, Enrichment.is_ad.is_not(True))
        .where((Post.grouped_id.is_(None)) | (Post.text != ""))
    )
    rows = list((await session.execute(stmt)).all())
    rng = random.Random(seed)  # noqa: S311 - a reproducible sample, not a secret
    rng.shuffle(rows)
    picked: list[OnboardingPost] = []
    per_channel: dict[int, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    for post, username, topic in rows:
        if count[topic] >= per_topic or per_channel[post.channel_id] >= max_per_channel:
            continue
        if not post.text and not post.media_path:
            continue
        picked.append(
            OnboardingPost(post.id, username, topic, post.posted_at, post.text, post.media_path)
        )
        count[topic] += 1
        per_channel[post.channel_id] += 1
        if all(count[t] >= per_topic for t in TOPICS):
            break
    picked.sort(key=lambda p: (p.topic, p.post_id))
    return picked


async def record_feedback(
    factory: async_sessionmaker[AsyncSession],
    user_id: int,
    votes: list[tuple[int, str]],
    *,
    context: str = "onboarding",
    query: str | None = None,
) -> int:
    """Append feedback rows; a later vote on the same post supersedes earlier ones at read time."""
    rows = [
        Feedback(user_id=user_id, post_id=pid, signal=sig, context=context, query=query)
        for pid, sig in votes
        if sig in SIGNALS
    ]
    async with factory() as session:
        session.add_all(rows)
        await session.commit()
    return len(rows)


async def load_votes(session: AsyncSession, user_id: int) -> list[Vote]:
    stmt = (
        select(
            Feedback.post_id, Feedback.signal, Channel.topic, Post.channel_id, Feedback.created_at
        )
        .join(Post, Post.id == Feedback.post_id)
        .join(Channel, Channel.id == Post.channel_id)
        .where(Feedback.user_id == user_id)
        .order_by(Feedback.created_at, Feedback.id)
    )
    latest: dict[int, Vote] = {}
    for pid, sig, topic, ch, _ in (await session.execute(stmt)).all():
        latest[int(pid)] = Vote(int(pid), sig, topic, int(ch))  # last vote wins
    return list(latest.values())


def embeddings_for(index: PostIndex, post_ids: list[int]) -> dict[int, list[float]]:
    if not post_ids:
        return {}
    points = index.client.retrieve(
        index.collection, ids=post_ids, with_payload=False, with_vectors=["full"]
    )
    out: dict[int, list[float]] = {}
    for p in points:
        vec: Any = p.vector.get("full") if isinstance(p.vector, dict) else None
        if isinstance(vec, list) and vec and not isinstance(vec[0], list):
            out[int(p.id)] = [float(x) for x in vec]
    return out


async def build_and_store_profile(
    factory: async_sessionmaker[AsyncSession], index: PostIndex, user_id: int
) -> Profile:
    async with factory() as session:
        votes = await load_votes(session, user_id)
        stored = await session.get(UserProfile, user_id)
        negative = list(stored.negative_prefs or []) if stored else []
    embeddings = embeddings_for(index, [v.post_id for v in votes if v.signal in ("like", "save")])
    profile = build_profile(votes, embeddings, negative_prefs=negative)
    async with factory() as session:
        row = await session.get(UserProfile, user_id)
        if row is None:
            row = UserProfile(user_id=user_id)
            session.add(row)
        row.topic_weights_json = profile.topic_weights
        row.channel_affinity_json = {str(k): v for k, v in profile.channel_affinity.items()}
        row.interest_centroids = profile.interest_centroids
        row.negative_prefs = profile.negative_prefs
        row.stats_json = {"like_rate": profile.like_rate, "votes": profile.votes}
        await session.commit()
    log.info(
        "profile_built",
        user_id=user_id,
        votes=profile.votes,
        like_rate=profile.like_rate,
        centroids=len(profile.interest_centroids),
    )
    return profile


async def load_profile(session: AsyncSession, user_id: int) -> Profile | None:
    row = await session.get(UserProfile, user_id)
    if row is None:
        return None
    stats = row.stats_json or {}
    return Profile.from_json(
        {
            "topic_weights": row.topic_weights_json,
            "channel_affinity": row.channel_affinity_json,
            "interest_centroids": row.interest_centroids,
            "negative_prefs": row.negative_prefs,
            "like_rate": stats.get("like_rate", 0.5),
            "votes": stats.get("votes", 0),
        }
    )


async def channel_median_views(session: AsyncSession, since: datetime) -> dict[int, float]:
    """Median views per channel in the window (mean on SQLite, which has no percentile)."""
    bind = session.get_bind()
    typical: Any = (
        func.percentile_cont(0.5).within_group(Post.views)
        if bind.dialect.name == "postgresql"
        else func.avg(Post.views)
    )
    stmt = (
        select(Post.channel_id, typical)
        .where(Post.posted_at >= since, Post.views.is_not(None))
        .group_by(Post.channel_id)
    )
    rows = (await session.execute(stmt)).all()
    return {int(ch): float(m) for ch, m in rows if m is not None}


async def rank_candidates(
    factory: async_sessionmaker[AsyncSession],
    index: PostIndex,
    user_id: int,
    *,
    days: int = 7,
    per_centroid: int = 60,
    limit: int = 20,
    weights: Weights | None = None,
    baseline: bool = False,
) -> list[dict[str, Any]]:
    """Candidates = recent posts near any interest centroid ∪ recent top-viewed posts, minus
    anything the user already voted on; ranked by the v1 score (or by views when
    ``baseline``)."""
    since = datetime.now(UTC) - timedelta(days=days)
    async with factory() as session:
        profile = await load_profile(session, user_id) or Profile(
            {t: 1 / 3 for t in TOPICS}, {}, [], [], 0.5
        )
        seen = {v.post_id for v in await load_votes(session, user_id)}
        medians = await channel_median_views(session, since)
    window = models.Filter(
        must=[
            models.FieldCondition(key="posted_at", range=models.Range(gte=int(since.timestamp())))
        ],
        must_not=[models.FieldCondition(key="is_ad", match=models.MatchValue(value=True))],
    )
    ids: set[int] = set()
    for centroid in profile.interest_centroids:
        res = index.client.query_points(
            index.collection,
            query=centroid,
            using="full",
            query_filter=window,
            limit=per_centroid,
            with_payload=False,
        )
        ids.update(int(p.id) for p in res.points)
    async with factory() as session:
        stmt = (  # the popularity branch, with the same ad filter the vector branch has
            select(Post.id)
            .outerjoin(Enrichment, Enrichment.post_id == Post.id)
            .where(Post.posted_at >= since, Enrichment.is_ad.is_not(True))
            .order_by(Post.views.desc().nulls_last())
            .limit(per_centroid)
        )
        ids.update(int(i) for (i,) in (await session.execute(stmt)).all())
        ids -= seen
        rows = (
            await session.execute(
                select(Post, Channel.username, Channel.topic, Enrichment)
                .join(Channel, Channel.id == Post.channel_id)
                .outerjoin(Enrichment, Enrichment.post_id == Post.id)
                .where(Post.id.in_(ids))
            )
        ).all()
    embeddings = embeddings_for(index, [int(p.id) for p, *_ in rows])
    candidates = [
        Candidate(
            post_id=post.id,
            topic=topic,
            channel_id=post.channel_id,
            channel=username,
            text=post.text or "",
            views=post.views,
            channel_median_views=medians.get(post.channel_id),
            is_ad=e.is_ad if e else None,
            quality=e.quality_score if e else None,
            embedding=embeddings.get(post.id),
            label=(e.topic_labels or [None])[0] if e and e.topic_labels else None,
        )
        for post, username, topic, e in rows
    ]
    if baseline:
        return [
            {
                "post_id": c.post_id,
                "channel": c.channel,
                "topic": c.topic,
                "views": c.views,
                "text": c.text[:120],
                "score": {"total": float(c.views or 0)},
            }
            for c in baseline_rank(candidates)[:limit]
        ]
    ranked = rank(candidates, profile, weights or Weights())
    return [
        {
            "post_id": c.post_id,
            "channel": c.channel,
            "topic": c.topic,
            "views": c.views,
            "text": c.text[:120],
            "score": s,
        }
        for c, s in ranked[:limit]
    ]
