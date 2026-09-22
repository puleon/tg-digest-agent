# D11–12 — Interest profile: does the score beat "most viewed" on the owner's own votes?

**Question.** SPEC §6.6 asks for an interest score that ranks a week's posts for one reader
better than the obvious baseline (top by views), measured on the reader's own reactions.
With a cold start of 36 onboarding votes, is v1 better than views, and by how much?

## Votes

`tgdigest profile onboard` drew 36 posts — 12 per topic, ≤ 2 per channel, no ads — into a
sheet; the owner rated them like / dislike / skip
([`results/onboarding_labels.csv`](results/onboarding_labels.csv)): **12 likes, 24 dislikes,
no skips**. By topic: scifi 6/12 liked, cinema 5/12, humor 1/12. (Two of the humor dislikes
were @luka_ebkov posts; that channel was removed from the corpus on 2026-09-22 — see the
re-run below.)

## Profile (`profile/model.py`, `tgdigest profile build`)

- Topic weights, Laplace-smoothed from the like counts: **scifi 0.47, cinema 0.40, humor 0.13**.
- Channel affinity: Bayesian, prior = the user's like rate (0.33) with 4 pseudo-votes, so a
  channel with one like out of one is not a favourite yet; 22 channels seen.
- Interest centroids: k-means over the BGE-M3 vectors of the liked posts, k = 2 at 12 likes.
- Score v1 = 0.45 · nearest-centroid cosine + 0.15 · topic + 0.15 · channel + 0.15 ·
  channel-relative engagement + 0.10 · novelty, − 0.5 for ads, − 0.3 for quality < 2.5.

## Leave-one-out (`scripts/profile_eval.py --user 0`)

For each vote, the profile is rebuilt from the other 35, the held-out post is scored, and the
36 held-out scores are ranked; the baseline ranks the same posts by views. AUC = the
probability that a liked post outranks a disliked one.

| ranking | AUC (liked > disliked) | precision@10 |
|---|---|---|
| **interest score v1** | **0.701** | **0.400** |
| baseline: views | 0.427 | 0.200 |
| random | 0.500 | 0.333 |

**Re-run after @luka_ebkov left the corpus** (2026-09-22, at the owner's request): 34 votes,
12 likes, the two dislikes on that channel gone — **AUC 0.606 / precision@10 0.400** against
views 0.466 / 0.200. The direction holds and the margin shrinks: those two posts were easy
negatives the score ranked low, and dropping them takes the easy part of the problem away.
Both numbers are on samples far too small for a confidence interval worth printing; what
survives both is "better than views, which is worse than random for this reader".

Views are *worse than random* for this reader: the most viewed posts in the corpus are the
humor channels', and the owner disliked 11 of 12 humor posts. The score's 0.70 comes mostly
from the topic and centroid terms learning that; with 36 votes the confidence interval is
wide (roughly ± 0.1), so the honest reading is "the direction is right, the size is not yet
known". The learned v2 ranker of the SPEC is deliberately not built on this much data
(§2 buffer rule); the features are in place for the day the bot's 👍/👎 accumulate.

What the score looks like in use (`tgdigest profile top --user 0`): the top 15 of the last
week are all scifi — trailers on @mirf_ru, @starlighthousekeeping's posts on the «Трудно быть
богом» series, @nplusone — with similarity 0.6–0.7 to the nearest centroid. The digest's
Planner reserves a slot per topic before the score is applied, so an issue still covers
cinema and humor; the score only decides *which* cinema and humor posts.
