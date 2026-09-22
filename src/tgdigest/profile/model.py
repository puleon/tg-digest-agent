"""Profile from feedback and the v1 interest score (SPEC §6.6).

Profile fields:
- ``topic_weights`` — smoothed share of likes per topic (Laplace prior toward uniform);
- ``channel_affinity`` — Bayesian-smoothed like rate per channel (prior = the user's overall
  like rate, strength ``PRIOR_STRENGTH`` pseudo-votes), so a channel with one like is not a
  favourite yet;
- ``interest_centroids`` — k-means centres of the liked posts' dense embeddings (k grows with
  the number of likes, never one centre for a heterogeneous taste);
- ``negative_prefs`` — explicit exclusions (phrases, channels), kept as given.

Score v1 (linear, weights in ``Weights``): nearest-centroid cosine, topic weight, channel
affinity, engagement normalised by the channel's typical views, novelty (not shown before),
minus penalties for ads and low quality. The baseline it must beat is chronological top by
views (§6.6) — evaluated in ``scripts/profile_eval.py``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

TOPICS = ("scifi", "humor", "cinema")
PRIOR_STRENGTH = 4.0
LIKE = "like"
DISLIKE = "dislike"


@dataclass(frozen=True)
class Vote:
    post_id: int
    signal: str  # like | dislike | save | skip
    topic: str
    channel_id: int


@dataclass
class Profile:
    topic_weights: dict[str, float]
    channel_affinity: dict[int, float]
    interest_centroids: list[list[float]]
    negative_prefs: list[str] = field(default_factory=list)
    like_rate: float = 0.0
    votes: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "topic_weights": self.topic_weights,
            "channel_affinity": {str(k): v for k, v in self.channel_affinity.items()},
            "interest_centroids": self.interest_centroids,
            "negative_prefs": self.negative_prefs,
            "like_rate": self.like_rate,
            "votes": self.votes,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Profile:
        return cls(
            topic_weights=dict(data.get("topic_weights") or {}),
            channel_affinity={
                int(k): float(v) for k, v in (data.get("channel_affinity") or {}).items()
            },
            interest_centroids=[
                list(map(float, c)) for c in (data.get("interest_centroids") or [])
            ],
            negative_prefs=list(data.get("negative_prefs") or []),
            like_rate=float(data.get("like_rate") or 0.0),
            votes=int(data.get("votes") or 0),
        )


def _positive(signal: str) -> bool:
    return signal in (LIKE, "save")


def kmeans(vectors: np.ndarray, k: int, *, iters: int = 25, seed: int = 0) -> np.ndarray:
    """Plain k-means on L2-normalised rows (cosine geometry); deterministic given ``seed``."""
    rng = np.random.default_rng(seed)
    x = vectors / np.clip(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12, None)
    k = max(1, min(k, len(x)))
    centres = x[rng.choice(len(x), size=k, replace=False)].copy()
    for _ in range(iters):
        sims = x @ centres.T
        assign = sims.argmax(axis=1)
        moved = False
        for j in range(k):
            members = x[assign == j]
            if len(members) == 0:
                continue
            c = members.mean(axis=0)
            c /= max(float(np.linalg.norm(c)), 1e-12)
            if not np.allclose(c, centres[j], atol=1e-6):
                centres[j] = c
                moved = True
        if not moved:
            break
    return np.asarray(centres, dtype=np.float32)


def centroid_count(n_likes: int) -> int:
    """Two centres from 6 likes, one more per 8 likes, at most 5 — interests are plural (§6.6)."""
    if n_likes < 6:
        return 1 if n_likes else 0
    return min(5, 2 + (n_likes - 6) // 8)


def build_profile(
    votes: Sequence[Vote],
    embeddings: Mapping[int, Sequence[float]],
    *,
    negative_prefs: Iterable[str] = (),
) -> Profile:
    liked = [v for v in votes if _positive(v.signal)]
    judged = [v for v in votes if v.signal in (LIKE, DISLIKE, "save")]
    like_rate = len(liked) / len(judged) if judged else 0.0

    topic_likes = {t: 1.0 for t in TOPICS}  # Laplace prior toward uniform
    for v in liked:
        topic_likes[v.topic] = topic_likes.get(v.topic, 1.0) + 1.0
    total = sum(topic_likes.values())
    topic_weights = {t: round(n / total, 4) for t, n in topic_likes.items()}

    per_channel: dict[int, list[int]] = {}
    for v in judged:
        per_channel.setdefault(v.channel_id, []).append(int(_positive(v.signal)))
    channel_affinity = {
        ch: round((sum(xs) + PRIOR_STRENGTH * like_rate) / (len(xs) + PRIOR_STRENGTH), 4)
        for ch, xs in per_channel.items()
    }

    vecs = [embeddings[v.post_id] for v in liked if v.post_id in embeddings]
    centroids: list[list[float]] = []
    if vecs:
        centres = kmeans(np.asarray(vecs, dtype=np.float32), centroid_count(len(vecs)))
        centroids = [[round(float(x), 6) for x in c] for c in centres]

    return Profile(
        topic_weights=topic_weights,
        channel_affinity=channel_affinity,
        interest_centroids=centroids,
        negative_prefs=[p for p in negative_prefs if p],
        like_rate=round(like_rate, 4),
        votes=len(judged),
    )


@dataclass(frozen=True)
class Candidate:
    post_id: int
    topic: str
    channel_id: int
    channel: str
    text: str
    views: int | None
    channel_median_views: float | None
    is_ad: bool | None
    quality: float | None
    embedding: Sequence[float] | None
    seen_before: bool = False
    label: str | None = None


@dataclass(frozen=True)
class Weights:
    similarity: float = 0.45
    topic: float = 0.15
    channel: float = 0.15
    engagement: float = 0.15
    novelty: float = 0.10
    ad_penalty: float = 0.5
    low_quality_penalty: float = 0.3
    quality_floor: float = 2.5  # quality is 1–5 from the classifier


DEFAULT_WEIGHTS = Weights()


def engagement_score(views: int | None, channel_median: float | None) -> float:
    """Views relative to the channel's own median, squashed to [0, 1] — a small channel's hit
    counts as much as a big channel's."""
    if not views or not channel_median:
        return 0.5
    ratio = views / max(channel_median, 1.0)
    return float(1 / (1 + math.exp(-math.log(max(ratio, 1e-6)))))  # logistic of log-ratio


def _blocked(candidate: Candidate, negative_prefs: Sequence[str]) -> bool:
    text = candidate.text.lower()
    for pref in negative_prefs:
        p = pref.lower().lstrip("@")
        if p == candidate.channel.lower() or (p and re.search(re.escape(p), text)):
            return True
    return False


def score(
    candidate: Candidate, profile: Profile, weights: Weights = DEFAULT_WEIGHTS
) -> dict[str, float]:
    """Component scores and the total; ``blocked`` posts get total −1."""
    if _blocked(candidate, profile.negative_prefs):
        return {"total": -1.0, "blocked": 1.0}
    sim = 0.0
    if candidate.embedding is not None and profile.interest_centroids:
        v = np.asarray(candidate.embedding, dtype=np.float32)
        v /= max(float(np.linalg.norm(v)), 1e-12)
        c = np.asarray(profile.interest_centroids, dtype=np.float32)
        sim = float(np.clip((c @ v).max(), 0.0, 1.0))
    topic = profile.topic_weights.get(candidate.topic, 1 / len(TOPICS))
    topic_score = min(1.0, topic * len(TOPICS) / 2)  # uniform share → 0.5
    channel = profile.channel_affinity.get(candidate.channel_id, profile.like_rate or 0.5)
    engagement = engagement_score(candidate.views, candidate.channel_median_views)
    novelty = 0.0 if candidate.seen_before else 1.0
    total = (
        weights.similarity * sim
        + weights.topic * topic_score
        + weights.channel * channel
        + weights.engagement * engagement
        + weights.novelty * novelty
    )
    if candidate.is_ad:
        total -= weights.ad_penalty
    if candidate.quality is not None and candidate.quality < weights.quality_floor:
        total -= weights.low_quality_penalty
    return {
        "total": round(total, 4),
        "similarity": round(sim, 4),
        "topic": round(topic_score, 4),
        "channel": round(channel, 4),
        "engagement": round(engagement, 4),
        "novelty": novelty,
    }


def rank(
    candidates: Sequence[Candidate], profile: Profile, weights: Weights = DEFAULT_WEIGHTS
) -> list[tuple[Candidate, dict[str, float]]]:
    scored = [(c, score(c, profile, weights)) for c in candidates]
    scored.sort(key=lambda cs: (-cs[1]["total"], cs[0].post_id))
    return scored


def baseline_rank(candidates: Sequence[Candidate]) -> list[Candidate]:
    """SPEC §6.6 baseline: top by views, taken topic by topic (ads and blocked posts are not
    filtered — it is naive).

    Views are ranked within each topic and the topics are interleaved, so the Curator's topic
    slots can be filled. A global ranking starved cinema: the humor channels' view counts are an
    order of magnitude higher, the top of the list had no cinema posts, and the baseline issue
    ended up with 3–6 items against the score's 8 — a length gap the judge then rewarded.
    """
    by_topic: dict[str, list[Candidate]] = {}
    for c in sorted(candidates, key=lambda c: (-(c.views or 0), c.post_id)):
        by_topic.setdefault(c.topic, []).append(c)
    queues = [by_topic[t] for t in sorted(by_topic, key=lambda t: -(by_topic[t][0].views or 0))]
    out: list[Candidate] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out
