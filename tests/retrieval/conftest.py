from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import numpy as np
import pytest
from qdrant_client import QdrantClient

from tgdigest.retrieval.embeddings import EmbeddingBatch
from tgdigest.retrieval.index import PostIndex

_TOKEN = re.compile(r"\w+")


class FakeEmbedder:
    """Deterministic stand-in for BGE-M3: hashed bag of words, so shared words → cosine > 0."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(
        self, texts: Sequence[str], *, batch_size: int = 16, sparse: bool = True
    ) -> EmbeddingBatch:
        self.calls.append(list(texts))
        dense = np.zeros((len(texts), 1024), dtype=np.float32)
        sparse_vecs: list[dict[int, float]] = []
        for i, text in enumerate(texts):
            weights: dict[int, float] = {}
            for tok in _TOKEN.findall(text.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)  # noqa: S324
                dense[i, h % 1024] += 1.0
                weights[h % 30000] = weights.get(h % 30000, 0.0) + 1.0
            if not dense[i].any():
                dense[i, 0] = 1.0
            sparse_vecs.append(weights if sparse else {})
        dense /= np.linalg.norm(dense, axis=1, keepdims=True)
        return EmbeddingBatch(dense=dense, sparse=sparse_vecs)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def index(embedder: FakeEmbedder) -> PostIndex:
    idx = PostIndex(QdrantClient(":memory:"), embedder)
    idx.ensure_collection()
    return idx
