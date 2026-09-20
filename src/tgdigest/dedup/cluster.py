"""Build duplicate clusters from signatures and explicit links (SPEC §6.3).

Edges (cheap → expensive): identical normalized text, identical file bytes, perceptual-hash
distance ≤ ``phash_threshold``, near-identical embeddings inside a 7-day window (when
provided), and forwards: two posts forwarded from the same source message, or a forward and
the original post when the original is in the corpus. Union-find merges edges into clusters;
the representative is the earliest post (ties: higher quality).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from tgdigest.dedup.signatures import Signature

WINDOW_DAYS = 7


@dataclass
class PostRow:
    id: int
    post_id: int
    """Post the message belongs to: first member of the album, itself for a plain message."""
    channel_id: int
    topic: str
    posted_at: datetime
    tg_message_id: int
    forward_from_channel: int | None = None
    forward_from_msg_id: int | None = None
    quality: float | None = None


@dataclass
class Edge:
    a: int
    b: int
    stage: str
    score: float


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


@dataclass
class Cluster:
    members: list[int]
    """Post ids (first member of each album)."""
    representative: int
    topic: str
    first_seen_at: datetime
    size: int


@dataclass
class ClusterResult:
    clusters: list[Cluster]
    edges: list[Edge]
    stage_counts: dict[str, int] = field(default_factory=dict)


def _phash_edges(rows: list[tuple[int, int]], threshold: int) -> list[Edge]:
    """rows = (post_id, phash). Pairwise Hamming with numpy popcount; posts ids are unique."""
    if len(rows) < 2:
        return []
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    hashes = np.array([r[1] for r in rows], dtype=np.uint64)
    edges: list[Edge] = []
    chunk = 2048
    for start in range(0, len(hashes), chunk):
        block = hashes[start : start + chunk]
        xor = block[:, None] ^ hashes[None, :]
        dist = _popcount(xor)
        ii, jj = np.nonzero(dist <= threshold)
        for i, j in zip(ii, jj, strict=True):
            gi = start + int(i)
            if gi < int(j) and ids[gi] != ids[j]:
                edges.append(Edge(int(ids[gi]), int(ids[j]), "phash", float(dist[i, j])))
    return edges


_POP8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def _popcount(x: np.ndarray) -> np.ndarray:
    out = np.zeros(x.shape, dtype=np.uint8)
    v = x.copy()
    for _ in range(8):
        out += _POP8[(v & np.uint64(0xFF)).astype(np.uint8)]
        v >>= np.uint64(8)
    return out


def build_clusters(
    rows: list[PostRow],
    signatures: dict[int, Signature],
    *,
    phash_threshold: int = 6,
    max_signature_frequency: int = 12,
    embedding_pairs: list[tuple[int, int, float]] | None = None,
) -> ClusterResult:
    """``max_signature_frequency``: a hash shared by more posts than this is a placeholder
    (a generic video thumbnail, a weekly rubric caption), not evidence of a repost."""
    by_id = {r.id: r for r in rows}
    post_of = {r.id: r.post_id for r in rows}
    edges: list[Edge] = []

    def placeholders(groups: dict[Any, list[int]]) -> set[Any]:
        return {k for k, members in groups.items() if len(members) > max_signature_frequency}

    # 1. identical normalized text
    by_text: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        sig = signatures.get(r.id)
        if sig and sig.text_hash and r.id == r.post_id:  # captions live on the first member
            by_text[sig.text_hash].append(r.id)
    skip_text = placeholders(by_text)
    for key, members in by_text.items():
        if key in skip_text:
            continue
        for other in members[1:]:
            edges.append(Edge(members[0], other, "text", 1.0))

    # 2. identical file bytes
    by_file: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        sig = signatures.get(r.id)
        if sig and sig.file_sha256:
            by_file[sig.file_sha256].append(r.id)
    skip_file = placeholders(by_file)
    for key, members in by_file.items():
        if key in skip_file:
            continue
        for other in members[1:]:
            edges.append(Edge(members[0], other, "file", 1.0))

    # 3. perceptual hash: exact placeholder values excluded, then near matches
    by_phash: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        sig = signatures.get(r.id)
        if sig and sig.phash is not None:
            by_phash[sig.phash].append(r.id)
    skip_phash = placeholders(by_phash)
    with_hash = [(i, h) for h, members in by_phash.items() if h not in skip_phash for i in members]
    edges.extend(_phash_edges(with_hash, phash_threshold))

    # 4. embeddings within the window (pairs are computed by the caller: needs the index)
    for a, b, score in embedding_pairs or []:
        ra, rb = by_id.get(a), by_id.get(b)
        if ra and rb and abs((ra.posted_at - rb.posted_at).days) <= WINDOW_DAYS:
            edges.append(Edge(a, b, "embedding", score))

    # 5. forwards
    by_source: dict[tuple[int, int], list[int]] = defaultdict(list)
    originals = {(r.channel_id, r.tg_message_id): r.id for r in rows}
    for r in rows:
        if r.forward_from_channel is not None and r.forward_from_msg_id is not None:
            source = (r.forward_from_channel, r.forward_from_msg_id)
            by_source[source].append(r.id)
            if source in originals and originals[source] != r.id:
                edges.append(Edge(originals[source], r.id, "forward", 1.0))
    for members in by_source.values():
        for other in members[1:]:
            edges.append(Edge(members[0], other, "forward", 1.0))

    uf = UnionFind()
    stage_counts: dict[str, int] = defaultdict(int)  # merges each stage actually caused
    for e in edges:
        pa, pb = post_of.get(e.a), post_of.get(e.b)
        if pa is None or pb is None or pa == pb or uf.find(pa) == uf.find(pb):
            continue
        stage_counts[e.stage] += 1
        uf.union(pa, pb)

    members_of: dict[int, list[int]] = defaultdict(list)
    for post_id in set(post_of.values()):
        members_of[uf.find(post_id)].append(post_id)
    clusters: list[Cluster] = []
    for members in members_of.values():
        if len(members) < 2:
            continue
        posts = [by_id[m] for m in members]
        posts.sort(key=lambda p: (p.posted_at, -(p.quality or 0.0), p.id))
        rep = posts[0]
        clusters.append(
            Cluster(
                members=sorted(members),
                representative=rep.id,
                topic=rep.topic,
                first_seen_at=rep.posted_at,
                size=len(members),
            )
        )
    clusters.sort(key=lambda c: (-c.size, c.representative))
    return ClusterResult(clusters=clusters, edges=edges, stage_counts=dict(stage_counts))
