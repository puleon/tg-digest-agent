from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tgdigest.dedup.cluster import PostRow, UnionFind, build_clusters
from tgdigest.dedup.signatures import Signature

T0 = datetime(2026, 5, 1, tzinfo=UTC)


def _row(i: int, ch: int = 1, post_id: int | None = None, days: int = 0, **kw: object) -> PostRow:
    return PostRow(
        id=i,
        post_id=post_id or i,
        channel_id=ch,
        topic="humor",
        posted_at=T0 + timedelta(days=days),
        tg_message_id=i,
        **kw,  # type: ignore[arg-type]
    )


def _sig(
    i: int, text: str | None = None, file: str | None = None, ph: int | None = None
) -> Signature:
    return Signature(i, text, text, file, ph)


def test_union_find_merges_transitively() -> None:
    uf = UnionFind()
    uf.union(1, 2)
    uf.union(2, 3)
    assert uf.find(1) == uf.find(3) and uf.find(4) != uf.find(1)


def test_stages_link_posts_and_representative_is_earliest() -> None:
    rows = [
        _row(1, days=0),
        _row(2, ch=2, days=1),  # same text as 1
        _row(3, ch=3, days=2),  # same file as 1
        _row(4, ch=4, days=3),  # phash within threshold of 1
        _row(5, ch=5, days=4),  # far phash: alone
        _row(6, ch=6, days=5, quality=5.0),
    ]
    sigs = {
        1: _sig(1, "t", "f", 0b1111_0000),
        2: _sig(2, "t"),
        3: _sig(3, None, "f"),
        4: _sig(4, None, None, 0b1111_0011),
        5: _sig(5, None, None, 0b0000_0000_1111_1111_0000_1111),
        6: _sig(6, "other text hash"),
    }
    res = build_clusters(rows, sigs, phash_threshold=2)
    assert len(res.clusters) == 1
    c = res.clusters[0]
    assert c.members == [1, 2, 3, 4] and c.representative == 1 and c.size == 4
    assert res.stage_counts == {"text": 1, "file": 1, "phash": 1}


def test_forwards_link_to_the_original_and_to_each_other() -> None:
    rows = [
        _row(10, ch=1, days=0),  # the original: channel 1, message 10
        _row(20, ch=2, days=1, forward_from_channel=1, forward_from_msg_id=10),
        _row(30, ch=3, days=2, forward_from_channel=1, forward_from_msg_id=10),
        _row(40, ch=4, days=3, forward_from_channel=99, forward_from_msg_id=7),  # external src
        _row(50, ch=5, days=4, forward_from_channel=99, forward_from_msg_id=7),
    ]
    res = build_clusters(rows, {}, phash_threshold=0)
    members = sorted(c.members for c in res.clusters)
    assert members == [[10, 20, 30], [40, 50]]
    assert res.stage_counts == {"forward": 3}


def test_album_members_collapse_into_one_post() -> None:
    rows = [
        _row(1, post_id=1),
        _row(2, post_id=1),  # album member of post 1
        _row(3, ch=2),
    ]
    sigs = {2: _sig(2, None, "f"), 3: _sig(3, None, "f")}  # member 2 shares a file with post 3
    res = build_clusters(rows, sigs)
    assert len(res.clusters) == 1 and res.clusters[0].members == [1, 3]
    # an edge inside one album is not a duplicate
    res2 = build_clusters(rows, {1: _sig(1, None, "g"), 2: _sig(2, None, "g")})
    assert res2.clusters == []


def test_embedding_pairs_respect_the_window() -> None:
    rows = [_row(1, days=0), _row(2, ch=2, days=3), _row(3, ch=3, days=30)]
    res = build_clusters(rows, {}, embedding_pairs=[(1, 2, 0.97), (1, 3, 0.99)])
    assert [c.members for c in res.clusters] == [[1, 2]]
    assert res.stage_counts == {"embedding": 1}


def test_frequent_signatures_are_treated_as_placeholders() -> None:
    rows = [_row(i, ch=i, days=i % 5) for i in range(1, 16)]
    sigs = {i: _sig(i, None, "placeholder", 0xABCD) for i in range(1, 15)}  # 14 posts share both
    sigs[15] = _sig(15, None, "unique-file", 0xABCD ^ 0b11)  # near the placeholder hash
    assert build_clusters(rows, sigs, max_signature_frequency=12).clusters == []
    assert build_clusters(rows, sigs, max_signature_frequency=20).clusters[0].size == 15
