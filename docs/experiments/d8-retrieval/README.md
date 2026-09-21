# D8 — Retrieval: what finds the posts, and what OCR and captions add

**Question.** Which retriever configuration serves the agent best on this corpus, and — the
central question of the project (SPEC §6.4) — how much do OCR and VLM captions add to search
over visual topics, where the post text alone often says nothing? (SPEC §8.1, §9 D8.)

**Setup.** Index of 18 987 posts (17 426 eligible after the product filters: no ads,
duplicate clusters collapsed to their representative), four indexing variants per post
(`text`, `text_ocr`, `text_ocr_caption`, `full` = + link summaries), each as a BGE-M3 dense
vector and a BGE-M3 sparse vector; `bge-reranker-v2-m3` over the top 50; BM25 (no stemming)
as the lexical baseline. Enrichment covered 9 923 posts at index time (OCR on 6 716,
captions on 7 810) — humor fully, cinema and scifi for the last three months.

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

_Pending: the judge is running over the pool; the calibration subset is drawn from its
grades._

## Files

`queries.yaml`, `results/` (runs.jsonl, pool.csv, judge grades, calibration labels) — copied
from `data/eval/retrieval/` on the box when the runs are complete.
