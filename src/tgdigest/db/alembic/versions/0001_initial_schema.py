"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-18 20:50:39.384899
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "channels",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=16), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("subscribers", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_message_id", sa.BigInteger(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_channels_topic"), "channels", ["topic"], unique=False)
    op.create_table(
        "digests",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "plan_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "items_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("critic_iterations", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_digests_user_id"), "digests", ["user_id"], unique=False)
    op.create_table(
        "user_profile",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column(
            "topic_weights_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "channel_affinity_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "interest_centroids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "negative_prefs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "posts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=True),
        sa.Column("media_path", sa.String(length=512), nullable=True),
        sa.Column("media_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("forwards", sa.Integer(), nullable=True),
        sa.Column("reactions_count", sa.Integer(), nullable=True),
        sa.Column("forward_from_channel", sa.BigInteger(), nullable=True),
        sa.Column("forward_from_msg_id", sa.BigInteger(), nullable=True),
        sa.Column("grouped_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "raw_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "tg_message_id", name="uq_posts_channel_message"),
    )
    op.create_index(
        "ix_posts_forward_from",
        "posts",
        ["forward_from_channel", "forward_from_msg_id"],
        unique=False,
    )
    op.create_index("ix_posts_grouped_id", "posts", ["grouped_id"], unique=False)
    op.create_index("ix_posts_posted_at", "posts", ["posted_at"], unique=False)
    op.create_table(
        "clusters",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("topic", sa.String(length=16), nullable=False),
        sa.Column(
            "representative_post_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["representative_post_id"], ["posts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clusters_topic"), "clusters", ["topic"], unique=False)
    op.create_table(
        "enrichment",
        sa.Column("post_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("vlm_caption", sa.Text(), nullable=True),
        sa.Column(
            "topic_labels",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("is_ad", sa.Boolean(), nullable=True),
        sa.Column("is_spoiler", sa.Boolean(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column(
            "entities_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "external_links",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("link_summary", sa.Text(), nullable=True),
        sa.Column("injection_flag", sa.Boolean(), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("post_id"),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("post_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("signal", sa.String(length=16), nullable=False),
        sa.Column("context", sa.String(length=16), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("digest_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["digest_id"], ["digests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_user_created", "feedback", ["user_id", "created_at"], unique=False)
    op.create_table(
        "post_clusters",
        sa.Column("post_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "cluster_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("post_id", "cluster_id"),
    )


def downgrade() -> None:

    op.drop_table("post_clusters")
    op.drop_index("ix_feedback_user_created", table_name="feedback")
    op.drop_table("feedback")
    op.drop_table("enrichment")
    op.drop_index(op.f("ix_clusters_topic"), table_name="clusters")
    op.drop_table("clusters")
    op.drop_index("ix_posts_posted_at", table_name="posts")
    op.drop_index("ix_posts_grouped_id", table_name="posts")
    op.drop_index("ix_posts_forward_from", table_name="posts")
    op.drop_table("posts")
    op.drop_table("user_profile")
    op.drop_index(op.f("ix_digests_user_id"), table_name="digests")
    op.drop_table("digests")
    op.drop_index(op.f("ix_channels_topic"), table_name="channels")
    op.drop_table("channels")
