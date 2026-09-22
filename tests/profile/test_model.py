from __future__ import annotations

import numpy as np

from tgdigest.profile.model import (
    Candidate,
    Profile,
    Vote,
    baseline_rank,
    build_profile,
    centroid_count,
    engagement_score,
    kmeans,
    rank,
    score,
)


def _vec(seed: int, dim: int = 8) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim)
    return list(map(float, v / np.linalg.norm(v)))


def test_profile_smooths_topics_and_channels_and_clusters_likes() -> None:
    votes = [Vote(i, "like", "humor", 1) for i in range(1, 9)] + [
        Vote(9, "dislike", "cinema", 2),
        Vote(10, "like", "cinema", 2),
        Vote(11, "skip", "scifi", 3),
    ]
    emb = {i: _vec(i % 2) for i in range(1, 12)}  # two distinct directions among the likes
    p = build_profile(votes, emb, negative_prefs=["реклама", ""])
    assert p.votes == 10 and p.like_rate == 0.9
    assert p.topic_weights["humor"] > p.topic_weights["cinema"] > p.topic_weights["scifi"] > 0
    assert abs(sum(p.topic_weights.values()) - 1) < 1e-6
    # channel 2: one like, one dislike → pulled toward the prior (0.9), not 0.5 exactly
    assert 0.5 < p.channel_affinity[2] < 0.9 and p.channel_affinity[1] > p.channel_affinity[2]
    assert len(p.interest_centroids) == centroid_count(9) == 2
    assert p.negative_prefs == ["реклама"]
    assert Profile.from_json(p.to_json()).channel_affinity == p.channel_affinity


def test_centroid_count_and_kmeans_are_deterministic() -> None:
    assert [centroid_count(n) for n in (0, 1, 5, 6, 13, 14, 60)] == [0, 1, 1, 2, 2, 3, 5]
    x = np.asarray([_vec(0), _vec(0), _vec(1), _vec(1)], dtype=np.float32)
    a, b = kmeans(x, 2), kmeans(x, 2)
    assert np.allclose(a, b) and a.shape == (2, 8)


def test_score_components_and_penalties() -> None:
    profile = Profile(
        topic_weights={"humor": 0.6, "cinema": 0.2, "scifi": 0.2},
        channel_affinity={1: 0.9},
        interest_centroids=[_vec(0)],
        negative_prefs=["политика", "@spam"],
        like_rate=0.5,
    )

    def cand(**kw: object) -> Candidate:
        base: dict[str, object] = {
            "post_id": 1,
            "topic": "humor",
            "channel_id": 1,
            "channel": "memes",
            "text": "кот",
            "views": 1000,
            "channel_median_views": 1000,
            "is_ad": False,
            "quality": 4,
            "embedding": _vec(0),
        }
        base.update(kw)
        return Candidate(**base)  # type: ignore[arg-type]

    best = score(cand(), profile)
    assert best["similarity"] > 0.99 and best["topic"] == 0.9 and best["channel"] == 0.9
    assert best["engagement"] == 0.5 and best["novelty"] == 1.0
    assert score(cand(embedding=_vec(1)), profile)["total"] < best["total"]
    assert score(cand(is_ad=True), profile)["total"] == round(best["total"] - 0.5, 4)
    assert score(cand(quality=1), profile)["total"] == round(best["total"] - 0.3, 4)
    assert score(cand(seen_before=True), profile)["total"] == round(best["total"] - 0.1, 4)
    assert score(cand(text="опять политика"), profile) == {"total": -1.0, "blocked": 1.0}
    assert score(cand(channel="spam"), profile)["total"] == -1.0
    assert (
        score(cand(topic="scifi", channel_id=9), profile)["channel"] == 0.5
    )  # unknown → like rate


def test_engagement_is_relative_to_the_channel() -> None:
    assert engagement_score(None, 100) == 0.5 and engagement_score(100, None) == 0.5
    assert engagement_score(100, 100) == 0.5
    assert engagement_score(1000, 100) > 0.9 and engagement_score(10, 100) < 0.1


def test_rank_and_baseline_disagree_on_purpose() -> None:
    profile = Profile({"humor": 0.8, "cinema": 0.1, "scifi": 0.1}, {}, [_vec(0)], [], 0.5)
    quiet_match = Candidate(1, "humor", 1, "a", "кот", 10, 100, False, 4, _vec(0))
    loud_ad = Candidate(2, "cinema", 2, "b", "купи", 100000, 100, True, 2, _vec(1))
    assert [c.post_id for c, _ in rank([quiet_match, loud_ad], profile)] == [1, 2]
    assert [c.post_id for c in baseline_rank([quiet_match, loud_ad])] == [2, 1]


def test_baseline_interleaves_topics_by_views() -> None:
    def cand(pid: int, topic: str, views: int) -> Candidate:
        return Candidate(pid, topic, 1, "c", "t", views, 100, False, 3, _vec(pid))

    ranked = baseline_rank(
        [cand(1, "humor", 900), cand(2, "humor", 800), cand(3, "cinema", 50), cand(4, "cinema", 40)]
    )
    # views within a topic, topics interleaved (the loudest topic first): cinema is not starved
    assert [c.post_id for c in ranked] == [1, 3, 2, 4]
