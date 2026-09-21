"""Qdrant collection of posts: named dense vectors per variant, BGE-M3 sparse vectors, payload
filters, and hybrid (dense + sparse, RRF) search (SPEC §6.4).

Collection layout (``posts``):

- dense vectors ``text``, ``text_ocr``, ``text_ocr_caption``, ``full`` — 1 024-d cosine, one per
  indexing variant (see :mod:`tgdigest.retrieval.documents`);
- sparse vectors ``sparse_<variant>`` — BGE-M3 lexical weights of the same text;
- payload: post/channel/topic/label, ``posted_at`` (unix seconds) for range filters,
  ``is_ad`` / ``is_spoiler`` / ``quality``, ``cluster_id`` and ``is_representative`` (dedup at
  query time), a text snippet, and ``content_hash`` for idempotent re-indexing.

Point ids are post ids, so an upsert is a replace and a rebuild converges.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import numpy as np
from qdrant_client import QdrantClient, models

from tgdigest.retrieval.documents import VARIANTS, IndexDocument

COLLECTION = "posts"
DENSE_DIM = 1024
Mode = Literal["hybrid", "dense", "sparse"]


class Embedder(Protocol):
    def encode(self, texts: Sequence[str], *, batch_size: int = 16, sparse: bool = True) -> Any:
        """Returns an object with ``dense`` (n × 1024 float32) and ``sparse`` (list of
        ``{token_id: weight}``)."""


@dataclass
class SearchFilters:
    topic: str | None = None
    channels: Sequence[int] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    exclude_ads: bool = True
    exclude_spoilers: bool = False
    representatives_only: bool = False
    """Collapse duplicate clusters to their representative (SPEC §6.3 → §6.4)."""

    def to_qdrant(self) -> models.Filter | None:
        must: list[models.Condition] = []
        if self.topic:
            must.append(
                models.FieldCondition(key="topic", match=models.MatchValue(value=self.topic))
            )
        if self.channels:
            must.append(
                models.FieldCondition(
                    key="channel_id", match=models.MatchAny(any=list(self.channels))
                )
            )
        if self.date_from or self.date_to:
            must.append(
                models.FieldCondition(
                    key="posted_at",
                    range=models.Range(
                        gte=int(self.date_from.timestamp()) if self.date_from else None,
                        lte=int(self.date_to.timestamp()) if self.date_to else None,
                    ),
                )
            )
        if self.representatives_only:
            must.append(
                models.FieldCondition(key="is_representative", match=models.MatchValue(value=True))
            )
        must_not: list[models.Condition] = []  # unlabelled posts stay: only positive labels drop
        if self.exclude_ads:
            must_not.append(models.FieldCondition(key="is_ad", match=models.MatchValue(value=True)))
        if self.exclude_spoilers:
            must_not.append(
                models.FieldCondition(key="is_spoiler", match=models.MatchValue(value=True))
            )
        if not must and not must_not:
            return None
        return models.Filter(must=must or None, must_not=must_not or None)


@dataclass
class Hit:
    post_id: int
    score: float
    payload: dict[str, Any]
    rank: int


@dataclass
class IndexStats:
    seen: int = 0
    indexed: int = 0
    unchanged: int = 0
    embedded_texts: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


def _sparse(vec: dict[int, float]) -> models.SparseVector:
    items = sorted(vec.items())
    return models.SparseVector(indices=[k for k, _ in items], values=[v for _, v in items])


class PostIndex:
    def __init__(self, client: QdrantClient, embedder: Embedder, collection: str = COLLECTION):
        self.client = client
        self.embedder = embedder
        self.collection = collection

    # --- schema ------------------------------------------------------------------------------
    def ensure_collection(self, *, recreate: bool = False) -> bool:
        """Create the collection and payload indexes; True if it was (re)created."""
        if recreate and self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        if self.client.collection_exists(self.collection):
            return False
        self.client.create_collection(
            self.collection,
            vectors_config={
                v: models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)
                for v in VARIANTS
            },
            sparse_vectors_config={f"sparse_{v}": models.SparseVectorParams() for v in VARIANTS},
        )
        for key, schema in {
            "topic": models.PayloadSchemaType.KEYWORD,
            "label": models.PayloadSchemaType.KEYWORD,
            "channel_id": models.PayloadSchemaType.INTEGER,
            "posted_at": models.PayloadSchemaType.INTEGER,
            "is_ad": models.PayloadSchemaType.BOOL,
            "is_spoiler": models.PayloadSchemaType.BOOL,
            "is_representative": models.PayloadSchemaType.BOOL,
            "cluster_id": models.PayloadSchemaType.INTEGER,
            "content_hash": models.PayloadSchemaType.KEYWORD,
        }.items():
            self.client.create_payload_index(self.collection, key, schema)
        return True

    # --- indexing ----------------------------------------------------------------------------
    def existing_hashes(self, post_ids: Iterable[int]) -> dict[int, str]:
        ids = list(post_ids)
        if not ids:
            return {}
        points = self.client.retrieve(
            self.collection, ids=ids, with_payload=["content_hash"], with_vectors=False
        )
        return {int(p.id): str((p.payload or {}).get("content_hash")) for p in points}

    def upsert(self, docs: Sequence[IndexDocument], *, batch_size: int = 16) -> IndexStats:
        """Embed and upsert documents whose content changed; identical variant texts of one
        post are embedded once."""
        stats = IndexStats(seen=len(docs))
        current = self.existing_hashes(d.post_id for d in docs)
        todo = [d for d in docs if current.get(d.post_id) != d.content_hash]
        stats.unchanged = len(docs) - len(todo)
        if not todo:
            return stats
        # unique texts across posts and variants -> one embedding each
        unique: dict[str, int] = {}
        for d in todo:
            for v in VARIANTS:
                unique.setdefault(d.variants[v], len(unique))
        texts = list(unique)
        batch = self.embedder.encode(texts, batch_size=batch_size, sparse=True)
        dense = np.asarray(batch.dense, dtype=np.float32)
        stats.embedded_texts = len(texts)
        points = []
        for d in todo:
            vectors: dict[str, Any] = {}
            for v in VARIANTS:
                k = unique[d.variants[v]]
                vectors[v] = dense[k].tolist()
                vectors[f"sparse_{v}"] = _sparse(batch.sparse[k])
            points.append(models.PointStruct(id=d.post_id, vector=vectors, payload=d.payload))
            for src, present in d.sources.items():
                if present:
                    stats.by_source[src] = stats.by_source.get(src, 0) + 1
        self.client.upsert(self.collection, points=points, wait=True)
        stats.indexed = len(points)
        return stats

    def count(self) -> int:
        return int(self.client.count(self.collection, exact=True).count)

    # --- search ------------------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        variant: str = "full",
        mode: Mode = "hybrid",
        filters: SearchFilters | None = None,
        limit: int = 20,
        candidates: int = 100,
    ) -> list[Hit]:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}; one of {VARIANTS}")
        q = self.embedder.encode([query], sparse=mode != "dense")
        dense = np.asarray(q.dense, dtype=np.float32)[0].tolist()
        qfilter = filters.to_qdrant() if filters else None
        if mode == "dense":
            res = self.client.query_points(
                self.collection,
                query=dense,
                using=variant,
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            )
        elif mode == "sparse":
            res = self.client.query_points(
                self.collection,
                query=_sparse(q.sparse[0]),
                using=f"sparse_{variant}",
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            )
        else:
            res = self.client.query_points(
                self.collection,
                prefetch=[
                    models.Prefetch(query=dense, using=variant, filter=qfilter, limit=candidates),
                    models.Prefetch(
                        query=_sparse(q.sparse[0]),
                        using=f"sparse_{variant}",
                        filter=qfilter,
                        limit=candidates,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            )
        return [
            Hit(post_id=int(p.id), score=float(p.score), payload=dict(p.payload or {}), rank=i + 1)
            for i, p in enumerate(res.points)
        ]
