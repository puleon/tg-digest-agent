"""M6 — does the interest score beat the baseline on the user's own labels? (SPEC §6.6)

Leave-one-out over the user's like/dislike votes: for each vote, build the profile from the
other votes, score the held-out post, and compare the ranking of all held-out posts by their
scores against the baseline ranking by views. Reports precision@k and AUC (probability that a
liked post outranks a disliked one) for both. With ~36 onboarding votes the numbers are noisy;
the point is the direction, recorded whichever way it goes.

uv run python scripts/profile_eval.py --user 0
"""

from __future__ import annotations

import argparse
import asyncio
from itertools import product

from sqlalchemy import select

from tgdigest.config import get_settings
from tgdigest.db.base import make_engine, make_session_factory
from tgdigest.db.models import Channel, Enrichment, Post
from tgdigest.profile.model import Candidate, build_profile, score
from tgdigest.profile.runner import channel_median_views, embeddings_for, load_votes
from tgdigest.retrieval.embeddings import BGEM3Embedder
from tgdigest.retrieval.index import PostIndex


def auc(pos: list[float], neg: list[float]) -> float:
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p, n in product(pos, neg))
    return wins / (len(pos) * len(neg))


def precision_at(ranked: list[tuple[int, bool]], k: int) -> float:
    head = ranked[:k]
    return sum(1 for _, liked in head if liked) / max(1, len(head))


async def main(user: int, k: int) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    from qdrant_client import QdrantClient

    index = PostIndex(QdrantClient(url=settings.qdrant_url), BGEM3Embedder())
    try:
        async with make_session_factory(engine)() as session:
            votes = [
                v
                for v in await load_votes(session, user)
                if v.signal in ("like", "dislike", "save")
            ]
            if len(votes) < 6:
                print(f"user {user}: {len(votes)} judged votes — nothing to evaluate")
                return
            ids = [v.post_id for v in votes]
            rows = (
                await session.execute(
                    select(Post, Channel.username, Channel.topic, Enrichment)
                    .join(Channel, Channel.id == Post.channel_id)
                    .outerjoin(Enrichment, Enrichment.post_id == Post.id)
                    .where(Post.id.in_(ids))
                )
            ).all()
            since = min(p.posted_at for p, *_ in rows)
            medians = await channel_median_views(session, since)
    finally:
        await engine.dispose()
    embeddings = embeddings_for(index, ids)
    by_id = {p.id: (p, u, t, e) for p, u, t, e in rows}
    liked = {v.post_id: v.signal in ("like", "save") for v in votes}

    scored: list[tuple[int, float, float, bool]] = []  # post, score, views, liked
    for held in votes:
        rest = [v for v in votes if v.post_id != held.post_id]
        profile = build_profile(rest, embeddings)
        p, u, t, e = by_id[held.post_id]
        cand = Candidate(
            post_id=p.id,
            topic=t,
            channel_id=p.channel_id,
            channel=u,
            text=p.text or "",
            views=p.views,
            channel_median_views=medians.get(p.channel_id),
            is_ad=e.is_ad if e else None,
            quality=e.quality_score if e else None,
            embedding=embeddings.get(p.id),
        )
        scored.append((p.id, score(cand, profile)["total"], float(p.views or 0), liked[p.id]))

    pos = [s for _, s, _, good in scored if good]
    neg = [s for _, s, _, good in scored if not good]
    pos_v = [v for _, _, v, good in scored if good]
    neg_v = [v for _, _, v, good in scored if not good]
    by_score = [(i, good) for i, _, _, good in sorted(scored, key=lambda x: -x[1])]
    by_views = [(i, good) for i, _, _, good in sorted(scored, key=lambda x: -x[2])]
    print(
        f"user {user}: {len(scored)} votes ({len(pos)} liked, {len(neg)} disliked), leave-one-out"
    )
    print(f"| ranking | AUC (liked > disliked) | precision@{k} |")
    print("|---|---|---|")
    print(f"| interest score v1 | {auc(pos, neg):.3f} | {precision_at(by_score, k):.3f} |")
    print(f"| baseline: views | {auc(pos_v, neg_v):.3f} | {precision_at(by_views, k):.3f} |")
    print(f"| random | 0.500 | {len(pos) / len(scored):.3f} |")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--user", type=int, default=0)
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()
    asyncio.run(main(args.user, args.k))
