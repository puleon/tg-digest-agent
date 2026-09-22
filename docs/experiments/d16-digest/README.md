# D16 — Digest: interest score vs the views baseline, and the judge's biases

**Question.** Does the v1 interest score pick a better issue than "the most viewed posts of the
week" for the same reader and budget? And can the local judge be trusted to say so — how much
does its verdict depend on the order of the two issues, on their length, and on sharing a
model family with the editor? (SPEC §6.6, §8.2.)

## Protocol

`scripts/digest_pairwise.py pairs|judge|compare`:

- **Pairs**: for each of the last 8 weeks and a fixed reading budget, two issues are composed
  for the same user from the same candidate window — one ranked by the v1 score
  (`profile/model.py`: centroid similarity 0.45, topic 0.15, channel affinity 0.15, engagement
  0.15, novelty 0.10; −0.5 for ads, −0.3 for low quality), one by views. Both go through the same
  Planner/Curator constraints and the same Editor ⇄ Critic loop, and neither is stored as the
  user's issue.
- **Judge**: `pairwise_digest.v1` sees the reader's profile and both issues and must pick a
  winner (no ties). Every pair is judged **twice, in both orders**. A verdict that flips with the
  order is inconsistent and counts as a position-bias event; consistent verdicts decide wins.
  Verbosity bias = how often the longer issue wins among consistent verdicts. Self-preference
  probe: the same pairs are judged by Qwen3.6-35B-A3B (the editor's family) and by gpt-oss-120b
  (another family); agreement is reported raw and as Cohen's κ.

## Results

### Round 1 — global views baseline ([`results/round1/`](results/round1/))

8 pairs (the weeks ending 2026-08-03 … 2026-09-21, budget 8 minutes), the owner's profile
(scifi 0.47 / cinema 0.40 / humor 0.13). Both issues of a pair went through the same Curator
and the same Editor ⇄ Critic; composing a pair took 10–30 min on the loaded box.

| judge | pairs | consistent | v1 wins | views wins | position flips | longer side wins | s / verdict |
|---|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B (fast) | 8 | 6 | **5** | 1 | 0.25 | 6/6 = 1.00 | — |
| gpt-oss-120b (heavy, another family) | 8 | 6 | **5** | 1 | 0.25 | 6/6 = 1.00 | 24 |
| agreement on the 5 pairs both judged consistently | | | | | | raw 1.00, κ 1.00 | |

Read literally: the interest score wins 5 : 1 under both judges, the judges agree perfectly,
position bias is one pair in four. Read carefully, the round does not show that yet: **the
longer issue won every consistent verdict, and the longer issue was almost always v1.** The
baseline ranked the whole week by views, and the humor channels' view counts are ten times the
cinema channels', so the Curator's cinema slots found nothing in the top of the list: five of
the eight baseline issues came out with 3–6 items against v1's 8 (782 vs 1 596 characters in
week 1, 669 vs 1 797 in week 3). The prompt tells the judge that length is not a merit; the
verdicts say otherwise. In the three pairs of comparable length (8 vs 8, 8 vs 8, 8 vs 6) the
fast judge scores 1 : 1 with a flip and the heavy judge 0 : 1 with two flips — no signal.

That is a bug in the baseline, not a finding about the score: `baseline_rank` now ranks views
*within each topic* and interleaves topics, so both issues fill the same plan. Round 2 on that
baseline is the comparison that counts; round 1 stays here as the measured verbosity bias —
the judge's, and the harness's for letting the lengths differ.

The first heavy-judge attempt returned eight empty verdicts: gpt-oss reasons before it
answers and a 120-token budget was spent on the reasoning (`finish=length`, empty content).
The client now gives heavy-tier calls `reasoning_effort=low` and room for the reasoning; the
second attempt judged 8/8 at 24 s per verdict.

### Round 2 — per-topic views baseline ([`results/round2/`](results/round2/))

The same 8 weeks with `baseline_rank` ranking views within each topic: the issues are now
the same size (8 : 8 in five pairs, 7 : 8, 8 : 6, 8 : 7) and differ by a few hundred
characters of Editor text at most; the two issues share 0–2 posts.

| judge | pairs | consistent | v1 wins | views wins | position flips | longer side wins | s / verdict |
|---|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B (fast) | 8 | 5 | 3 | 2 | **0.38** | 5/5 = 1.00 | 16 |
| gpt-oss-120b (heavy) | 8 | 4 | 3 | 1 | **0.50** | 4/4 = 1.00 | 27 |
| both consistent | 2 | | 1 | 1 | | | |

With the length gap gone, the preference goes with it: 3 : 2 and 3 : 1, half the pairs
flipping with the order of presentation, and — still — every consistent verdict for the
longer text, now when "longer" means 1 717 vs 1 260 characters or 1 552 vs 1 532. Nine
consistent verdicts, nine for the longer side, under two model families: that is not chance
(2⁻⁹), it is what these judges do with two issues of similar quality.

**What this measures and what it does not.** The protocol works — the biases it was built to
detect are visible and quantified: position bias 0.25–0.50, verbosity bias 1.00, and the
cross-family agreement is undefined because the two judges rarely agree with themselves.
What it cannot do on 8 pairs is separate the interest score from "top by views within each
topic": the Curator's constraints (a slot per topic, ≤ 2 per channel, 60 % fresh, one per
cluster) make both issues look alike to a judge reading titles and one-line reasons, and a
text profile («scifi 0.47, cinema 0.40, humor 0.13») does not tell a model what this reader
actually enjoys. The evidence that the score ranks *this reader's* posts better than views is
the leave-one-out on the owner's own votes ([D11](../d11-profile/README.md): AUC 0.70 vs
0.43) — a different question, answered by the reader, not by a judge.

The right judge for the digest is the owner: the 8 pairs are laid out side by side with the
order randomized and the mapping hidden (`data/eval/pairwise/sheet.html`, verdicts into
`human.csv`); that row gets added here when it is filled in. Until then the honest headline
is: **inconclusive at n = 8 with LLM judges — and the judges' biases are the finding.**
