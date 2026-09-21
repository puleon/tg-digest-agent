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

_Pending: the profile is the owner's onboarding votes (36 posts, being labelled); the runs are
queued behind the agent evaluations. If the votes are not in by then, the first run uses the
empty profile (equal topic weights) and is re-run once they are._
