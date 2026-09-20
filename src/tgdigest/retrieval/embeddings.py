"""BGE-M3 embeddings on CPU: dense (1024-d, normalized) and sparse lexical weights in one pass.

Loaded lazily and once per process; the model (~2.3 GB fp32) comes from the Hugging Face cache.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger(__name__)

MODEL_NAME = "BAAI/bge-m3"


@dataclass
class EmbeddingBatch:
    dense: np.ndarray
    """float32, shape (n, 1024), L2-normalized."""
    sparse: list[dict[int, float]]
    """Per text: token id → weight (BGE-M3 lexical weights), for the sparse index."""


class BGEM3Embedder:
    def __init__(
        self,
        model_name: str = MODEL_NAME,
        *,
        max_length: int = 1024,
        threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self._threads = threads
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            import torch
            from FlagEmbedding import BGEM3FlagModel

            if self._threads:
                torch.set_num_threads(self._threads)
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            log.info("loading_embedder", model=self.model_name, threads=torch.get_num_threads())
            self._model = BGEM3FlagModel(self.model_name, use_fp16=False, devices="cpu")
        return self._model

    def encode(
        self, texts: Sequence[str], *, batch_size: int = 16, sparse: bool = True
    ) -> EmbeddingBatch:
        model = self._load()
        out = model.encode(
            list(texts),
            batch_size=batch_size,
            max_length=self.max_length,
            return_dense=True,
            return_sparse=sparse,
            return_colbert_vecs=False,
        )
        dense = np.asarray(out["dense_vecs"], dtype=np.float32)
        norms = np.linalg.norm(dense, axis=1, keepdims=True)
        dense = dense / np.clip(norms, 1e-12, None)
        lexical = out.get("lexical_weights") if sparse else None
        sparse_vecs = [
            {int(k): float(v) for k, v in (w or {}).items()} for w in (lexical or [{}] * len(texts))
        ]
        return EmbeddingBatch(dense=dense, sparse=sparse_vecs)
