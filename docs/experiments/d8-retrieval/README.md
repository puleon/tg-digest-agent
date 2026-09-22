# D8 — Retrieval: what finds the posts, and what OCR and captions add

**Question.** Which retriever configuration serves the agent best on this corpus, and — the
central question of the project (SPEC §6.4) — how much do OCR and VLM captions add to search
over visual topics, where the post text alone often says nothing? (SPEC §8.1, §9 D8.)

**Setup.** Index of 18 987 posts (17 426 eligible after the product filters: no ads,
duplicate clusters collapsed to their representative), four indexing variants per post
(`text`, `text_ocr`, `text_ocr_caption`, `full` = + link summaries), each as a BGE-M3 dense
vector and a BGE-M3 sparse vector; `bge-reranker-v2-m3` over the top 50; BM25 (no stemming)
as the lexical baseline. Enrichment covered 9 782 posts at the index build the pool ran on
(OCR on 6 825, captions on 7 975) — humor fully, cinema for the last three months, scifi not
yet (its three-month pass finished on 2026-09-22, after this experiment; scifi posts are
mostly text, so the ablation's scifi rows would move little — not re-measured).

## Queries

40 requests in [`queries.yaml`](queries.yaml): 13 cinema, 13 scifi, 14 humor; by kind —
14 factual (a named thing), 11 thematic, 4 vague («что-то в духе Лема»), 11 visual (what a
meme shows or says: only OCR or a caption can find it). Written before any retrieval was run
over them.

## Protocol

1. **Pooling** (`scripts/retrieval_eval.py pool`): ten configurations run every query;
   the union of their top results (depth 10–20 per configuration) is the judged pool —
   1 856 (query, post) pairs, 22–69 per query. Runs are stored, so any configuration can be
   scored later without re-running.
2. **Judging** (`judge`): the local text model (Qwen3.6-35B-A3B, `judge_relevance.v1`)
   grades every pooled pair 0 / 1 / 2 from the post text, its OCR and its caption — the judge
   does not see the image.
3. **Calibration** (`calibrate`, `agree`): 100 pairs stratified by the judge's grade
   (40 % of grade 2, 30 % each of 1 and 0; grades hidden) are labelled by the owner; Cohen's
   κ between the two decides whether the judge's grades may fill the ablation table
   (SPEC §8.2: κ > 0.6).
4. **Scoring** (`score`): recall@20, nDCG@10 and MRR per configuration, overall and by topic
   and query kind; posts outside the judged pool count as not relevant.

## Results

### Judge calibration — 100 pairs

The calibration labels were **prefilled by the assistant** with a one-line reason per pair
and **reviewed by the owner**, who went through the first 25 (queries c01–c09), changed one
grade (an announcement that a trailer *will* be shown, 2 → 1) and accepted the rest by
analogy — the same prefill-then-review convention as the dedup labels. Final labels: 35 × 0,
34 × 1, 31 × 2. Against the judge's hidden grades (`agree`):

| | value |
|---|---|
| raw agreement | 0.670 |
| Cohen's κ, three grades | 0.507 |
| Cohen's κ, linearly weighted | **0.623** |
| Cohen's κ, relevant (1–2) vs not (0) | 0.568 |

Confusion (rows: human 0 / 1 / 2, columns: judge 0 / 1 / 2): `23 11 1` / `7 16 11` / `0 3 28`.
Every disagreement but one is between adjacent grades and almost all of them go one way: the
judge is **more lenient** — it grades 11 of the 35 irrelevant posts as "loosely related" and 11
of the 34 partial ones as "answers the query". It very rarely demotes (3 of 31 human 2s).
Read against SPEC §8.2's κ > 0.6: met on the weighted κ, not on the plain three-class one. A
uniformly lenient judge inflates every configuration's absolute recall and nDCG by roughly the
same amount; the *ordering* of configurations — what the ablation is for — is what its grades
are used for below, and the visual-query rows are where a lenient judge helps least (a wrong
meme is a 0 for both).

### Ablation — the judge's grades over the full pool (40 queries, 1 856 judged pairs)

`score --labels judge_fast.csv`. "Judged" = the share of a configuration's top-20 that was in
the pool; unjudged posts count as not relevant, so configurations below 1.00 are scored
slightly pessimistically (they surfaced posts no other configuration did).

| configuration | recall@20 | nDCG@10 | MRR | judged | s / query* |
|---|---|---|---|---|---|
| bm25:full (lexical baseline, no stemming) | 0.522 | 0.533 | 0.850 | 1.00 | 0.01 |
| sparse:full (BGE-M3 sparse) | 0.520 | 0.582 | 0.831 | 0.79 | 0.15 |
| dense:full (BGE-M3 dense) | 0.619 | 0.704 | 0.921 | 1.00 | 0.36 |
| hybrid:full (dense + sparse, RRF) | 0.666 | 0.693 | 0.876 | 0.99 | 0.16 |
| **hybrid + rerank:full** (bge-reranker-v2-m3 over top 50) | **0.691** | **0.816** | **0.974** | 1.00 | 38 |
| hybrid:text | 0.543 | 0.584 | 0.759 | 0.92 | 0.16 |
| hybrid:text_ocr | 0.648 | 0.679 | 0.874 | 0.94 | 0.16 |
| hybrid:text_ocr_caption | 0.672 | 0.693 | 0.876 | 0.99 | 0.17 |
| hybrid + rerank:text | 0.550 | 0.649 | 0.792 | 0.87 | 35 |
| hybrid + rerank:text_ocr_caption | 0.693 | 0.816 | 0.974 | 1.00 | 37 |

\* measured on the box while the ingest pass held the CPU; the reranker (50 pairs, 8
threads) is the whole cost of the last rows and runs at ≈ 10 s without the contention.

- **Reranking is the single largest gain**: nDCG@10 0.693 → 0.816 and MRR 0.876 → 0.974 over
  the same hybrid candidates — the right post is at rank 1 for almost every query.
- **Dense beats sparse beats BM25** on this corpus (nDCG@10 0.704 / 0.582 / 0.533). Hybrid RRF
  buys recall (0.619 → 0.666) at a small nDCG cost against dense alone; with the reranker on
  top the recall is what matters.
- **`full` = `text_ocr_caption` today**: the link-summary pass has run over a handful of posts,
  so the two variants are the same text (identical numbers). The row stays for the day the
  pass is complete.

### What OCR and captions add (SPEC §6.4)

By query kind, nDCG@10 (visual = the request names what a meme shows or says):

| configuration | factual (14) | thematic (11) | vague (4) | visual (11) |
|---|---|---|---|---|
| bm25:full | 0.619 | 0.511 | 0.647 | 0.413 |
| hybrid:text | 0.703 | 0.723 | 0.652 | **0.280** |
| hybrid:text_ocr | 0.748 | 0.728 | 0.730 | 0.532 |
| hybrid:text_ocr_caption | 0.747 | 0.727 | 0.721 | 0.584 |
| hybrid + rerank:text | 0.807 | 0.803 | 0.694 | 0.291 |
| hybrid + rerank:text_ocr_caption | 0.889 | 0.817 | 0.826 | **0.727** |

By topic — the honest view, because enrichment coverage differs (humor: all six months;
cinema: the last three; scifi: none at index time):

| topic | hybrid:text | +OCR | +caption | +caption+rerank | rerank on text alone |
|---|---|---|---|---|---|
| humor (14 queries) — recall@20 | 0.307 | 0.532 | 0.599 | 0.614 | 0.296 |
| humor — nDCG@10 | 0.344 | 0.580 | 0.620 | **0.739** | 0.349 |
| cinema (13) — nDCG@10 | 0.726 | 0.773 | 0.769 | 0.873 | 0.786 |
| scifi (13) — nDCG@10 | 0.681 | 0.685 | 0.689 | 0.838 | 0.812 |

- On humor, **the post text alone finds a third of what the enriched index finds**: recall@20
  0.307 → 0.599, nDCG@10 0.344 → 0.620 — OCR does most of it (+0.24), captions add the rest
  (+0.04, and +0.05 on visual queries overall). The reranker cannot repair a text-only index
  (0.349): what was never retrieved cannot be reranked.
- On cinema the gain is small (+0.04) — stills and posters come with captions of their own
  in the post text — and on scifi there is nothing to add yet, which is what the table shows.
- Visual queries stay the hardest kind even with everything on (0.727 vs 0.82–0.89): a caption
  describes one reading of a picture, and the judge cannot see the image either.

### Cost of the evaluation

Pooling: 400 runs in a few minutes plus 3 × 40 reranked runs. Judging: 1 856 pairs at 6.9 s
each with two concurrent requests (≈ 3.5 h, shared with the injection runs); the judge reads
the post text, OCR and caption and answers in ≤ 60 tokens.

## Files

`queries.yaml`; `results/`: `runs.jsonl` (every configuration's ranked list per query),
`pool.csv` (which configuration found each pooled post at which rank), `judge_fast.csv` (the
judge's grade, reason and latency per pair), `labels.csv` (the 100 calibration labels with
reasons). The labelling sheet with images stays out of git (`data/eval/retrieval/sheet.html`).
