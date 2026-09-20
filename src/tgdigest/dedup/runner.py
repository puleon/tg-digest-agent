"""Compute signatures once, rebuild clusters wholesale (idempotent), report stage counts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from tgdigest.db.models import Channel, Cluster, Enrichment, Post, PostCluster, PostSignature
from tgdigest.db.upsert import insert_ignore
from tgdigest.dedup.cluster import PostRow, build_clusters
from tgdigest.dedup.signatures import Signature, compute_signature

log = structlog.get_logger(__name__)


def _to_signed(value: int | None) -> int | None:
    return None if value is None else (value - (1 << 64) if value >= 1 << 63 else value)


def _to_unsigned(value: int | None) -> int | None:
    return None if value is None else (value + (1 << 64) if value < 0 else value)


@dataclass
class SignatureStats:
    selected: int = 0
    computed: int = 0
    with_text: int = 0
    with_file: int = 0
    with_phash: int = 0


async def compute_signatures(
    factory: async_sessionmaker[AsyncSession], media_root: Path, *, batch: int = 500
) -> SignatureStats:
    stats = SignatureStats()
    async with factory() as session:
        done = select(PostSignature.post_id)
        stmt = (
            select(Post.id, Post.text, Post.media_path)
            .where(Post.id.not_in(done))
            .order_by(Post.id)
        )
        pending = (await session.execute(stmt)).all()
    stats.selected = len(pending)
    for start in range(0, len(pending), batch):
        chunk = pending[start : start + batch]
        sigs = await asyncio.gather(
            *(
                asyncio.to_thread(
                    compute_signature, pid, text, media_root / media_path if media_path else None
                )
                for pid, text, media_path in chunk
            )
        )
        rows = [
            {
                "post_id": s.post_id,
                "text_key": s.text_key,
                "text_hash": s.text_hash,
                "file_sha256": s.file_sha256,
                "phash": _to_signed(s.phash),
            }
            for s in sigs
        ]
        async with factory() as session:
            await session.execute(insert_ignore(session, PostSignature.__table__, rows))  # type: ignore[arg-type]
            await session.commit()
        stats.computed += len(rows)
        stats.with_text += sum(1 for s in sigs if s.text_hash)
        stats.with_file += sum(1 for s in sigs if s.file_sha256)
        stats.with_phash += sum(1 for s in sigs if s.phash is not None)
        log.info("signatures", done=stats.computed, of=stats.selected)
    return stats


@dataclass
class DedupStats:
    posts: int = 0
    clusters: int = 0
    clustered_posts: int = 0
    largest: int = 0
    stage_counts: dict[str, int] = field(default_factory=dict)


async def load_rows(session: AsyncSession) -> tuple[list[PostRow], dict[int, Signature]]:
    stmt = (
        select(Post, Channel.topic, Enrichment.quality_score)
        .join(Channel, Channel.id == Post.channel_id)
        .outerjoin(Enrichment, Enrichment.post_id == Post.id)
        .options(selectinload(Post.channel))
        .order_by(Post.channel_id, Post.id)
    )
    rows: list[PostRow] = []
    first_of_album: dict[tuple[int, int], int] = {}
    for post, topic, quality in (await session.execute(stmt)).all():
        if post.grouped_id is not None:
            key = (post.channel_id, post.grouped_id)
            first_of_album.setdefault(key, post.id)
            post_id = first_of_album[key]
        else:
            post_id = post.id
        rows.append(
            PostRow(
                id=post.id,
                post_id=post_id,
                channel_id=post.channel_id,
                topic=topic,
                posted_at=post.posted_at,
                tg_message_id=post.tg_message_id,
                forward_from_channel=post.forward_from_channel,
                forward_from_msg_id=post.forward_from_msg_id,
                quality=quality,
            )
        )
    sig_rows = (await session.execute(select(PostSignature))).scalars().all()
    signatures = {
        s.post_id: Signature(
            s.post_id, s.text_key, s.text_hash, s.file_sha256, _to_unsigned(s.phash)
        )
        for s in sig_rows
    }
    return rows, signatures


async def rebuild_clusters(
    factory: async_sessionmaker[AsyncSession],
    *,
    phash_threshold: int = 6,
    max_signature_frequency: int = 12,
    embedding_pairs: list[tuple[int, int, float]] | None = None,
) -> DedupStats:
    async with factory() as session:
        rows, signatures = await load_rows(session)
    result = build_clusters(
        rows,
        signatures,
        phash_threshold=phash_threshold,
        max_signature_frequency=max_signature_frequency,
        embedding_pairs=embedding_pairs,
    )
    post_of = {r.id: r.post_id for r in rows}
    stage_of: dict[int, str] = {}
    for e in result.edges:  # first (cheapest) stage that touched a post wins the label
        stage_of.setdefault(post_of.get(e.a, e.a), e.stage)
        stage_of.setdefault(post_of.get(e.b, e.b), e.stage)
    async with factory() as session:
        await session.execute(delete(PostCluster))
        await session.execute(delete(Cluster))
        for c in result.clusters:
            cluster = Cluster(
                topic=c.topic,
                representative_post_id=c.representative,
                first_seen_at=c.first_seen_at,
                size=c.size,
            )
            session.add(cluster)
            await session.flush()
            session.add_all(
                PostCluster(post_id=m, cluster_id=cluster.id, stage=stage_of.get(m))
                for m in c.members
            )
        await session.commit()
    stats = DedupStats(
        posts=len({r.post_id for r in rows}),
        clusters=len(result.clusters),
        clustered_posts=sum(c.size for c in result.clusters),
        largest=max((c.size for c in result.clusters), default=0),
        stage_counts=result.stage_counts,
    )
    log.info("clusters_rebuilt", **vars(stats))
    return stats


async def cluster_stats(session: AsyncSession) -> list[tuple[str, int, int]]:
    stmt = (
        select(Cluster.topic, func.count(Cluster.id), func.sum(Cluster.size))
        .group_by(Cluster.topic)
        .order_by(Cluster.topic)
    )
    return [(t, int(n), int(s)) for t, n, s in (await session.execute(stmt)).all()]
