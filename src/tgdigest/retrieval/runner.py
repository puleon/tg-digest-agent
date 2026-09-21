"""Build the post index from Postgres: posts + enrichment + clusters → documents → Qdrant."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgdigest.db.models import Channel, Cluster, Enrichment, Post, PostCluster
from tgdigest.retrieval.documents import IndexDocument, MemberEnrichment, PostFacts, build_document
from tgdigest.retrieval.index import PostIndex

log = structlog.get_logger(__name__)


@dataclass
class BuildStats:
    posts: int = 0
    indexed: int = 0
    payload_updated: int = 0
    unchanged: int = 0
    embedded_texts: int = 0
    with_enrichment: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


async def load_facts(
    session: AsyncSession,
    *,
    topic: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[PostFacts]:
    """One :class:`PostFacts` per post (album = one post, first member carries the caption)."""
    stmt = (
        select(Post, Channel.username, Channel.topic)
        .join(Channel, Channel.id == Post.channel_id)
        .order_by(Post.channel_id, Post.id)
    )
    if topic:
        stmt = stmt.where(Channel.topic == topic)
    if since is not None:
        stmt = stmt.where(Post.posted_at >= since)
    rows = (await session.execute(stmt)).all()
    enrichment = {e.post_id: e for e in (await session.execute(select(Enrichment))).scalars().all()}
    cluster_of = {
        pc.post_id: pc.cluster_id
        for pc in (await session.execute(select(PostCluster))).scalars().all()
    }
    representative = {
        c.id: c.representative_post_id
        for c in (await session.execute(select(Cluster))).scalars().all()
    }

    albums: dict[tuple[int, int], list[Post]] = defaultdict(list)
    singles: list[Post] = []
    meta: dict[int, tuple[str, str]] = {}
    for post, username, ch_topic in rows:
        meta[post.id] = (username, ch_topic)
        if post.grouped_id is not None:
            albums[(post.channel_id, post.grouped_id)].append(post)
        else:
            singles.append(post)
    groups = [[p] for p in singles] + [sorted(a, key=lambda p: p.id) for a in albums.values()]
    groups.sort(key=lambda g: g[0].id)

    facts: list[PostFacts] = []
    for members in groups:
        first = members[0]
        username, ch_topic = meta[first.id]
        rows_e = [enrichment.get(m.id) for m in members]
        head = next((e for e in rows_e if e is not None), None)
        text = "\n".join(m.text for m in members if m.text)
        cluster_id = cluster_of.get(first.id)
        facts.append(
            PostFacts(
                post_id=first.id,
                channel_id=first.channel_id,
                channel=username,
                topic=ch_topic,
                posted_at=first.posted_at,
                text=text,
                media_type=next((m.media_type for m in members if m.media_type), None),
                media_path=next((m.media_path for m in members if m.media_path), None),
                members=tuple(
                    MemberEnrichment(e.ocr_text, e.vlm_caption, e.link_summary)
                    for e in rows_e
                    if e is not None
                ),
                label=(head.topic_labels[0] if head and head.topic_labels else None),
                is_ad=head.is_ad if head else None,
                is_spoiler=head.is_spoiler if head else None,
                quality=head.quality_score if head else None,
                injection_flag=head.injection_flag if head else None,
                model_version=head.model_version if head else None,
                cluster_id=cluster_id,
                is_representative=cluster_id is None or representative.get(cluster_id) == first.id,
            )
        )
        if limit and len(facts) >= limit:
            break
    return facts


async def build_index(
    factory: async_sessionmaker[AsyncSession],
    index: PostIndex,
    *,
    topic: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    batch: int = 256,
    embed_batch: int = 16,
) -> BuildStats:
    async with factory() as session:
        facts = await load_facts(session, topic=topic, since=since, limit=limit)
    index.ensure_collection()
    stats = BuildStats(posts=len(facts), with_enrichment=sum(1 for f in facts if f.members))
    docs: list[IndexDocument] = [build_document(f) for f in facts]
    for start in range(0, len(docs), batch):
        chunk = docs[start : start + batch]
        s = index.upsert(chunk, batch_size=embed_batch)
        stats.indexed += s.indexed
        stats.payload_updated += s.payload_updated
        stats.unchanged += s.unchanged
        stats.embedded_texts += s.embedded_texts
        for k, v in s.by_source.items():
            stats.by_source[k] = stats.by_source.get(k, 0) + v
        log.info(
            "index_progress",
            done=min(start + batch, len(docs)),
            of=len(docs),
            indexed=stats.indexed,
            unchanged=stats.unchanged,
        )
    return stats
