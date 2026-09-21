"""Build duplicate clusters from signatures and explicit links (SPEC §6.3).

Edges (cheap → expensive): identical normalized text, one text a truncated copy of the
other, identical file bytes, perceptual-hash distance ≤ ``phash_threshold``, near-identical
embeddings inside a 7-day window (when provided), and forwards: two posts forwarded from the
same source message, or a forward and the original post when the original is in the corpus.
Union-find merges edges into clusters; the representative is the earliest post (ties: higher
quality).

Two vetoes, tuned on the D6 labels (``scripts/dedup_labels.py``): a media match is an
*illustration*, not a repost, when both posts carry different texts and are more than a week
apart, or come from different channels with article-length texts (a press photo under two
stories); a short identical caption over different media weeks apart is a *rubric template*
("«Title» (year) (by Artist) #PosterPorn"), not a repost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from tgdigest.dedup.signatures import Signature, hamming, text_overlap

WINDOW_DAYS = 7
SAME_TEXT_OVERLAP = 0.5  # word-set Jaccard at or above which two captions count as one text
ARTICLE_CHARS = 300  # a normalized text this long is a story, not a caption of the media
CAPTION_CHARS = 100  # a text this short over different media is a rubric, not content
TRUNCATION_MIN_CHARS = 100  # a text that is a prefix of another one, both at least this long
OCR_MIN_WORDS = 4  # OCR shorter than this says nothing about the joke
OCR_SAME_OVERLAP = 0.3  # word-set Jaccard below which two OCR texts are different jokes


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
    """Union-find with cannot-link constraints: ``forbid(a, b)`` keeps the two components apart
    whatever chain of other edges arrives later (greedy — earlier, cheaper edges win)."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.cannot: dict[int, set[int]] = defaultdict(set)  # root -> roots it must not join

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def forbid(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.cannot[ra].add(rb)
            self.cannot[rb].add(ra)

    def union(self, a: int, b: int) -> bool:
        """Merge the two components; False (and no change) when they are forbidden to join."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if rb in self.cannot[ra]:
            return False
        root, gone = min(ra, rb), max(ra, rb)
        self.parent[gone] = root
        blocked = self.cannot.pop(gone, set()) | self.cannot.get(root, set())
        for other in blocked:
            self.cannot[other].discard(gone)
            self.cannot[other].add(root)
        if blocked:
            self.cannot[root] = blocked
        return True


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
    ocr_of: dict[int, str] | None = None,
) -> ClusterResult:
    """``max_signature_frequency``: a hash shared by more posts than this is a placeholder
    (a generic video thumbnail, a weekly rubric caption), not evidence of a repost.
    ``ocr_of``: text read from each message's image (from the VLM pass) — a pHash match whose
    OCR texts differ is a meme *template* with two jokes, not a repost."""
    by_id = {r.id: r for r in rows}
    ocr_of = ocr_of or {}
    post_of = {r.id: r.post_id for r in rows}
    edges: list[Edge] = []

    def placeholders(groups: dict[Any, list[int]]) -> set[Any]:
        return {k for k, members in groups.items() if len(members) > max_signature_frequency}

    # per post: caption (lives on the first member), files and hashes of all members
    text_of: dict[int, str] = {}
    files_of: dict[int, set[str]] = defaultdict(set)
    hashes_of: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        sig = signatures.get(r.id)
        if sig is None:
            continue
        if sig.text_key and r.id == r.post_id:
            text_of[r.id] = sig.text_key
        if sig.file_sha256:
            files_of[r.post_id].add(sig.file_sha256)
        if sig.phash is not None:
            hashes_of[r.post_id].append(sig.phash)

    def days_apart(pa: int, pb: int) -> int:
        return abs((by_id[pa].posted_at - by_id[pb].posted_at).days)

    def media_match(pa: int, pb: int) -> bool:
        if files_of[pa] & files_of[pb]:
            return True
        return any(hamming(x, y) <= phash_threshold for x in hashes_of[pa] for y in hashes_of[pb])

    def illustration(pa: int, pb: int) -> bool:
        """A media match between posts that tell different stories."""
        ta, tb = text_of.get(pa), text_of.get(pb)
        if not ta or not tb or text_overlap(ta, tb) >= SAME_TEXT_OVERLAP:
            return False
        if days_apart(pa, pb) > WINDOW_DAYS:
            return True
        return (
            by_id[pa].channel_id != by_id[pb].channel_id and min(len(ta), len(tb)) >= ARTICLE_CHARS
        )

    def template(e: Edge) -> bool:
        """Same picture by pHash, different words on it: a meme template reused."""
        oa, ob = ocr_of.get(e.a, ""), ocr_of.get(e.b, "")
        if len(oa.split()) < OCR_MIN_WORDS or len(ob.split()) < OCR_MIN_WORDS:
            return False
        return text_overlap(oa.lower(), ob.lower()) < OCR_SAME_OVERLAP

    def rubric(pa: int, pb: int) -> bool:
        """A short caption reused over different media weeks apart."""
        if len(text_of.get(pa, "")) >= CAPTION_CHARS or days_apart(pa, pb) <= WINDOW_DAYS:
            return False
        has_media = (files_of[pa] or hashes_of[pa]) and (files_of[pb] or hashes_of[pb])
        return bool(has_media) and not media_match(pa, pb)

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

    # 1b. one text is a truncated copy of the other (a repost cut at Telegram's caption limit)
    by_prefix: dict[str, list[int]] = defaultdict(list)
    for pid, key in text_of.items():
        if len(key) >= TRUNCATION_MIN_CHARS:
            by_prefix[key[:TRUNCATION_MIN_CHARS]].append(pid)
    for members in by_prefix.values():
        if len(members) < 2 or len(members) > max_signature_frequency:
            continue
        ordered = sorted(members, key=lambda pid: len(text_of[pid]))
        for i, shorter in enumerate(ordered):
            for longer in ordered[i + 1 :]:
                if text_of[longer] != text_of[shorter] and text_of[longer].startswith(
                    text_of[shorter]
                ):
                    edges.append(Edge(shorter, longer, "text_prefix", 1.0))

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
    kept: list[Edge] = []
    for e in edges:  # vetoes first: an illustration pair must stay apart whatever bridges it
        pa, pb = post_of.get(e.a), post_of.get(e.b)
        if pa is None or pb is None or pa == pb:
            continue
        if e.stage in ("file", "phash") and illustration(pa, pb):
            stage_counts["vetoed_illustration"] += 1
            uf.forbid(pa, pb)
        elif e.stage == "phash" and template(e):
            stage_counts["vetoed_template"] += 1
            uf.forbid(pa, pb)
        elif e.stage == "text" and rubric(pa, pb):
            stage_counts["vetoed_rubric"] += 1
        else:
            kept.append(e)
    edges = []
    for e in kept:
        pa, pb = post_of[e.a], post_of[e.b]
        if uf.find(pa) == uf.find(pb):
            edges.append(e)
        elif uf.union(pa, pb):
            stage_counts[e.stage] += 1
            edges.append(e)
        else:
            stage_counts["blocked_by_veto"] += 1

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
