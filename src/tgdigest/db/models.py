"""Relational schema — SPEC §5. One row per Telegram message; albums share ``grouped_id``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tgdigest.db.base import Base
from tgdigest.db.types import BigIntPK, JSONType

TOPICS = ("scifi", "humor", "cinema")
FEEDBACK_SIGNALS = ("like", "dislike", "open", "skip", "save")
FEEDBACK_CONTEXTS = ("onboarding", "digest", "search")


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    """Telegram channel id (stable, unique) — not a surrogate key."""
    username: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    topic: Mapped[str] = mapped_column(String(16), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(32), default="manual")
    """How the channel got into the corpus: manual | forward | mention (SPEC §12)."""
    quality_score: Mapped[float | None] = mapped_column(Float)
    subscribers: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # collector state (SPEC §6.1: incremental sync keeps last_message_id)
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    posts: Mapped[list[Post]] = relationship(back_populates="channel")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("channel_id", "tg_message_id", name="uq_posts_channel_message"),
        Index("ix_posts_posted_at", "posted_at"),
        Index("ix_posts_grouped_id", "grouped_id"),
        Index("ix_posts_forward_from", "forward_from_channel", "forward_from_msg_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    tg_message_id: Mapped[int] = mapped_column(BigInteger)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text, default="")
    media_type: Mapped[str | None] = mapped_column(String(16))
    """photo | video | animation | document | sticker | audio | voice | webpage | None."""
    media_path: Mapped[str | None] = mapped_column(String(512))
    """Relative to ``data/media``; only photos and image documents are downloaded."""
    media_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    """Telegram photo/document id — identical bytes share it, so it doubles as a dedup key."""
    views: Mapped[int | None] = mapped_column(Integer)
    forwards: Mapped[int | None] = mapped_column(Integer)
    reactions_count: Mapped[int | None] = mapped_column(Integer)
    forward_from_channel: Mapped[int | None] = mapped_column(BigInteger)
    forward_from_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    grouped_id: Mapped[int | None] = mapped_column(BigInteger)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="posts")
    enrichment: Mapped[Enrichment | None] = relationship(back_populates="post", uselist=False)


class Enrichment(Base):
    __tablename__ = "enrichment"

    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    )
    ocr_text: Mapped[str | None] = mapped_column(Text)
    vlm_caption: Mapped[str | None] = mapped_column(Text)
    topic_labels: Mapped[list[str] | None] = mapped_column(JSONType)
    is_ad: Mapped[bool | None] = mapped_column(Boolean)
    is_spoiler: Mapped[bool | None] = mapped_column(Boolean)
    quality_score: Mapped[float | None] = mapped_column(Float)
    entities_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    """``{version, films: [...], books: [...], people: [...]}`` — see ``ingest/entities.py``."""
    external_links: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    """``{version, items: [{url, status, title, error|summary}]}`` — see ``ingest/links.py``."""
    link_summary: Mapped[str | None] = mapped_column(Text)
    injection_flag: Mapped[bool | None] = mapped_column(Boolean)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_version: Mapped[str] = mapped_column(String(128))
    """Mandatory: two pipeline runs are only comparable through it (SPEC §5)."""

    post: Mapped[Post] = relationship(back_populates="enrichment")


class PostSignature(Base):
    """Dedup signatures per message (SPEC §6.3 stages 1–3), computed once."""

    __tablename__ = "post_signatures"
    __table_args__ = (
        Index("ix_post_signatures_text_hash", "text_hash"),
        Index("ix_post_signatures_file_sha256", "file_sha256"),
    )

    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    )
    text_key: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String(40))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    phash: Mapped[int | None] = mapped_column(BigInteger)
    """64-bit perceptual hash stored as a signed int64 (two's complement)."""


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    topic: Mapped[str] = mapped_column(String(16), index=True)
    representative_post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size: Mapped[int] = mapped_column(Integer, default=1)


class PostCluster(Base):
    __tablename__ = "post_clusters"

    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    )
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True
    )
    similarity: Mapped[float | None] = mapped_column(Float)
    stage: Mapped[str | None] = mapped_column(String(16))
    """Which cascade stage linked this member to the cluster (text | file | phash | …)."""


class UserProfile(Base):
    __tablename__ = "user_profile"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    topic_weights_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    channel_affinity_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    interest_centroids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    negative_prefs: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (Index("ix_feedback_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    signal: Mapped[str] = mapped_column(String(16))
    context: Mapped[str] = mapped_column(String(16))
    query: Mapped[str | None] = mapped_column(Text)
    digest_id: Mapped[int | None] = mapped_column(ForeignKey("digests.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntityCache(Base):
    """Memoized external lookups (Wikidata, FantLab, Open Library) — deterministic and slow."""

    __tablename__ = "entity_cache"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    query: Mapped[str] = mapped_column(String(512), primary_key=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    items_json: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    critic_iterations: Mapped[int] = mapped_column(Integer, default=0)
    """Maturity metric of the digest crew (SPEC §6.7)."""
