# D5 — Enrichment: distilling the LLM classifiers, link summaries, entity grounding

**Question.** SPEC §6.2 starts the four post classifiers (topic, ad, spoiler, quality) as a
few-shot LLM and asks, once 500–800 teacher labels exist, to distil them into a light model
and measure student/teacher agreement. Does a logistic regression over BGE-M3 embeddings
reproduce the LLM's labels well enough to replace it — and for which labels? (SPEC §9 D5.)
The same day covers the two slow enrichment passes: external-link summaries and entity
grounding, whose failure classes have to be explicit.

## Distillation

**Teacher.** Qwen3.6-35B-A3B, `classify_post.v1` with 8 hand-written examples, one call per
post over the normalized text + OCR + VLM caption (the ingest graph, D3). Labels: `topic ∈
{scifi, humor, cinema, other}` — *the post's own topic, not the channel's* — `is_ad`,
`is_spoiler`, `quality 1–5`.

**Student.** BGE-M3 dense embedding (1 024-d, CPU) of the same input, first 2 000 characters,
then `LogisticRegression(C=2, class_weight="balanced")` per target
(`ingest/distill.py`, `scripts/distill_classifiers.py`). Data: the 3 000 most recent
teacher-labelled posts minus 84 album members with nothing readable = **2 916 posts**, split
70/30 stratified by topic (seed 42): 2 041 train, 875 test. The window is humor (all six
months) and cinema (last three): cinema 961, humor 941, other 987, scifi 27. Embedding took
325 s (9 posts/s at 16 threads).

**Results** ([`results/distill.json`](results/distill.json); agreement with the teacher on
the test split):

| target | accuracy | macro-F1 | Cohen's κ | majority baseline | verdict |
|---|---|---|---|---|---|
| topic | **0.864** | 0.768 | **0.799** | 0.338 | usable |
| is_ad | 0.850 | 0.693 | 0.398 | 0.906 | not a replacement |
| quality | 0.544 | 0.385 | 0.333 | 0.576 | not a replacement |
| is_spoiler | — | — | — | — | 1 positive in 2 916: nothing to learn |

Progression with data: on the first 320 teacher labels (D5, before the full pass) the same
student scored κ 0.53 / 0.27 / 0.19 for topic / ad / quality; at 2 916 labels topic
crossed the "substantial agreement" line, the other two did not move much.

Per class on the test split (rows = teacher, columns = student):

| topic | cinema | humor | other | scifi | precision / recall |
|---|---|---|---|---|---|
| cinema (289) | **253** | 6 | 22 | 8 | 0.86 / 0.88 |
| humor (282) | 8 | **252** | 21 | 1 | 0.92 / 0.89 |
| other (296) | 31 | 17 | **245** | 3 | 0.85 / 0.83 |
| scifi (8) | 1 | 0 | 1 | **6** | 0.33 / 0.75 |

| is_ad | not ad | ad | precision / recall |
|---|---|---|---|
| not ad (793) | **685** | 108 | 0.97 / 0.86 |
| ad (82) | 23 | **59** | 0.35 / 0.72 |

Quality: the student recovers half of the teacher's 3s (251/504) and scatters the rest into
2 and 4 (79 and 130); the 1s and 2s are confused with each other (precision 0.27 / 0.32).

**Reading.**

- **Topic** is a semantic property of the text, exactly what a sentence embedding carries,
  and the student reproduces the teacher at κ 0.80. Where it disagrees is where the teacher
  itself is soft: the teacher calls 47 % of the posts in humor channels `other` (4 836 of
  10 265 — a cute animal picture is not a joke by its own definition), and `other` is where
  the student's errors go in both directions: 22 + 31 confusions with cinema, 21 + 17 with
  humor, against 14 between cinema and humor themselves. The eight scifi posts in the split
  are too few to say anything.
- **is_ad** looks acceptable by accuracy (0.85) and is not: the test split has 9.4 % ads, so
  saying "not an ad" scores 0.906. The balanced student trades precision for recall —
  it flags 167 posts, 59 of them the teacher's ads (precision 0.35, recall 0.72) — and κ
  stays at 0.40. Ads are recognized by *lexical* cues — «реклама», `erid`, a promo code, a
  price, «подписывайся» — which an embedding of a 2 000-character post smooths away.
  A useful student would need those cues as explicit features; not done in v1.
- **quality** is the teacher's least consistent label (59 % of all posts are a 3; the scale is
  ordinal and the examples define it loosely), and a student cannot learn a distinction its
  teacher does not make: κ 0.33, accuracy below the majority baseline.
- **is_spoiler**: the teacher flags 15 spoilers in 12 333 posts; the recent window has one.
  The label is real (the flag drives the `exclude_spoilers` filter) but too rare to distil.

**Decision.** The LLM stays the classifier in v1 for every label; the student models are
saved (`data/models/student_*.joblib`) and not wired in. The topic student is the candidate
for the degraded path — the ingest graph's "LLM unavailable" branch currently stores
metadata only — and for a cheap pre-filter when the corpus grows; ad detection needs
lexical features before distillation is worth repeating. A negative result on two of the
three labels is the honest outcome of the SPEC's plan: the plan assumed the teacher's labels
are learnable, and for `quality` they are barely more consistent than the student.

## External links and entity grounding

_Pending: the passes over the three-month cinema window run after the evaluation queue
(`ingest entities --topic cinema --since 2026-06-21`, `ingest links --since 2026-06-21
--direct`); this section gets the coverage and failure-class counts when they finish._
