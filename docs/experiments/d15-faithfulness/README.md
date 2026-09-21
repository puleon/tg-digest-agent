# D15 — Faithfulness: does the answer say only what its sources say?

**Question.** The agent writes answers over retrieved posts and the digest crew writes one-line
reasons over the posts it picked. What share of their factual claims is not supported by the
posts they cite — and how much of that is outright contradicted? (SPEC §8.3.)

## Protocol

Atomic-claim checking, two prompts on the local text model (Qwen3.6-35B-A3B):

1. `extract_claims.v1` splits the generated text into self-contained factual claims
   (pronouns and references resolved; opinions, hedges and meta statements dropped; a
   `[post N]` citation stays with the claim it follows). At most 20 claims per text.
2. `verify_claim.v1` checks one claim against the source posts (text, OCR, caption) →
   `supported` (a post states it, paraphrase and image text count), `contradicted` (a post
   states the opposite: a different number, date, name, outcome) or `unsupported` (no post says
   it, even if it is true elsewhere). The verdict names the deciding post.

**Metric**: unsupported share = (contradicted + unsupported) / claims; contradicted is reported
separately — it is the dangerous part. A human-check column in `claims.csv` allows a spot
audit of the verifier itself.

**Inputs**: 12 agent answers over the D8 query set (every third query, so all topics and kinds
are represented; the agent's own citations define the sources it is checked against) and one
digest issue (each item's title + reason against its single source post).
`scripts/faithfulness_eval.py answers|digest|score`.

## Results

_Pending: the runs are queued behind the injection and chaos evaluations._
