# Progress log

One entry per plan day (SPEC §9). Format: what was done · metrics · what failed · what moved.

## D1 — 2026-09-18 — Foundation

**Done**
- Repository scaffold: `src/tgdigest` package (settings via pydantic-settings, structlog,
  health checks, Typer CLI), `pyproject.toml` (uv, ruff, mypy strict, pytest), Makefile,
  pre-commit, GitHub Actions (lint + types + unit tests + gitleaks), `.env.example`, MIT license.
- `docker-compose.yml`: Postgres 16 (shared instance, separate `langfuse` DB), Qdrant 1.19,
  llama-server 0.4.1 in router mode (`docker/llm/models.ini`, on-demand load/unload,
  `--models-max 1`, idle sleep), Langfuse 4.38 with ClickHouse / Redis / MinIO and headless
  bootstrap of org/project/keys. Everything bound to 127.0.0.1; UIs over `make tunnel`.
- Server inventory (Ubuntu 24.04, 9950X 16C/32T with AVX-512 VNNI/BF16, 96 GB, 1.5 TB NVMe
  free, Docker 29, Ollama 0.32 already running). Native llama.cpp build (commit 4fea119,
  AVX-512 + VNNI). Candidate GGUFs downloading: Qwen3.6-35B-A3B Q4_K_M (+mmproj, +MTP draft),
  Gemma 4 26B-A4B UD-Q4_K_M (+mmproj), gpt-oss-120b MXFP4.
- Benchmark harness `scripts/bench_llm.py` (prefill tok/s, generation tok/s, load time,
  image latency; llama-server timings or wall-clock).

**Decisions / deviations from SPEC**
- SPEC §4 was written against vLLM + Qwen3-8B + Qwen2.5-VL-7B + an API model; it now says
  llama.cpp + local-only (agreed on 2026-09-18, see §2.8). Newer multimodal MoE models
  (Qwen3.6-35B-A3B, Gemma 4 26B-A4B) can serve both the mass-ops and the VLM tier with one
  weight set — the D1 benchmark decides.
- The collector CLI is `python -m tgdigest.collector …` (one package) rather than the
  spec's literal `python -m collector …`.
- The server is a workstation with a GNOME session: realistic RAM budget for us is ~80–85 GB.
- Server NIC negotiated 100 Mbit/s → model downloads run at ~10 MB/s (~3 h for ~105 GB).

**Metrics** — serving benchmark, full table and method in
[`docs/experiments/d1-llm-benchmark/`](docs/experiments/d1-llm-benchmark/README.md):

| fast tier (CPU, 16 threads) | prefill tok/s | gen tok/s | s / image (caption+OCR) |
|---|---|---|---|
| Qwen3.6-35B-A3B Q4_K_M, llama-server docker | 223 (260 native, `-tb 16`) | 19.0 | 9.4 (28 with OCR-grade `image-min-tokens 1024`) |
| Gemma 4 26B-A4B UD-Q4_K_M, llama-server docker | 204 | 16.9 | 14.4 (10.6 with thinking off) |
| Gemma 4 26B-A4B, Ollama 0.32 | 225 | 26.3 | 5.2 (empty answer: thinking) |
| **heavy:** gpt-oss-120b MXFP4 (5.1B active), llama-server docker | 142 | 17.2 | — (62.7 GiB resident, 29 s load) |

- Docker image ≈ native build (≤ 3 %) → services stay fully in compose.
- SMT threads hurt (−12…−18 %); MTP speculative decoding hurts (−17 %) — both off.
- OCR probe: Gemma reads Cyrillic verbatim at 262 image tokens; Qwen needs ≥ 1024 (2.6× slower).
- Infra is up on the box: Postgres 16.15, Qdrant 1.19.1, Langfuse 4.38 (headless-bootstrapped
  project + keys), `tgdigest health` green for all but the LLM until models are mounted.

**What did not work / surprises**
- Langfuse's reference compose pulls MinIO from `cgr.dev/chainguard`, which now returns 403 →
  switched to `quay.io/minio/minio` (last community release, 2025-09-07).
- llama-server router mode drops an idle keep-alive socket after long requests → client-side
  "server disconnected" on the next call (SDK retries cover it; benchmark uses `Connection: close`).
- `rsync --delete` from the laptop wiped server-side benchmark outputs once → experiment outputs
  now live in `docs/experiments/*/results/`, excluded from the push and pulled with `make pull-results`.
- Ollama runs Gemma 4 55 % faster at generation than llama.cpp with the unsloth quant — open question.

**Open** — channel list and Telegram credentials from the owner before D2 can be closed.

## D2 — 2026-09-18 (in progress) — Collector

**Done**
- Schema for all of SPEC §5 as SQLAlchemy 2 models (`src/tgdigest/db/models.py`) + Alembic
  (async env, migration `0001`) applied on the box; `alembic check` reports no drift. One row per
  Telegram message, albums linked by `grouped_id`; `(channel_id, tg_message_id)` unique — the
  idempotency guarantee lives in the database, not in the code path. Collector cursor
  (`last_message_id`, `last_synced_at`) on `channels`.
- Collector (`tgdigest collector …` / `python -m tgdigest.collector …`): YAML corpus → channels
  upsert; sequential incremental sync (`min_id` after the first pass, `offset_date` for the
  first 183 days); commits per batch so a crash or FloodWait resumes from the cursor;
  exponential FloodWait backoff on top of Telethon's own sleeps; photos and image documents
  downloaded, video/animation thumbnails only, deduplicated by Telegram media id across
  channels; `forward_from_*` kept as free dedup ground truth (SPEC §6.1).
- Tests: 25 unit (fake Telegram source + SQLite: idempotent re-runs, incremental cursor,
  FloodWait resume without duplicates, media dedup across channels, thumbnails, failure
  isolation, albums/forwards, YAML normalization, Telethon → row mapping on real TL objects)
  + 1 Postgres integration test (`make test-int`).

**2026-09-19 — source change.** No collector account could be created, so the primary source
is now Telegram's public web preview (`t.me/s/<channel>`): `WebPreviewSource` implements the
same `TelegramSource` protocol (parser on selectolax, `?before=` pagination, polite pacing,
429/5xx → the sync's FloodWait backoff) — sync, cursor, media store and schema unchanged.
All 29 channels expose the preview. Parsing is pinned by four captured pages
(`tests/collector/fixtures/`): albums expand to their member ids with the caption on the first,
forwards resolve to `<username>/<msg_id>`, "431K" → 431000, the numeric channel id comes from
`data-view`. What the preview lacks: forward counts; views rounded on big channels.
- The box cannot open TCP connections to Telegram's ranges (149.154.0.0/16) while the laptop
  can → collection runs on the box through an OpenSSH reverse SOCKS tunnel from the laptop
  (`ssh -R 1080`, `WEB_PROXY=socks5h://127.0.0.1:1080`, `make collect`). t.me answers in 0.5 s
  through it. Consequence: incremental collection needs the laptop online (minutes per day).
- Corpus: 29 channels (scifi 11, humor 8, cinema 10), all resolved with titles and subscriber
  counts. Media pacing: 0.8 s between t.me pages, 0.15 s between CDN downloads.

**Done when — met.** Three channels (@horrorfantastcom, @memehunter, @kinolikbez) collected
for six months; the re-run fetched 0 / inserted 0. Full collection of all 29 channels
(2026-03-20 … 2026-09-19) took ≈ 2.5 h through the tunnel, 0 flood waits, 3 media failures:

| | rows (messages) | posts (album = 1) | with text | with media | forwards |
|---|---|---|---|---|---|
| scifi | 9 683 | 6 522 | 6 423 | 8 794 | 267 |
| humor | 10 299 | 9 035 | 4 722 | 8 652 | 566 |
| cinema | 5 998 | 3 471 | 3 431 | 5 442 | 710 |
| **total** | **25 980** | **19 028** | 14 576 | 22 888 (1.7 GB: 19.8k photos, 3.1k animation thumbs) | 1 543 |

DoD line "≥ 25 channels, ≥ 15 000 posts" is met. Forward ground truth for dedup is thinner
than hoped: 60 forwards come from corpus channels, 166 source messages were forwarded more
than once, only 16 of them by two or more corpus channels — the hand-labelled clusters of D6
will carry the metric. `link_forwards` maps preview forward names to corpus channel ids.

## D3 — 2026-09-18 (skeleton, ahead of the data) — Ingest Agent

**Done**
- Ingest agent as a LangGraph state machine (`src/tgdigest/ingest/graph.py`):
  triage → describe_image (only for posts that need it: visual topics or short text with media)
  → classify → detect_injection. Every step degrades explicitly (`degraded` list) instead of
  failing the post: missing file, unreadable image, invalid JSON twice, timeout.
- Vision branch (SPEC §6.2 step 2): strict JSON `{ocr_text, caption, has_text, is_readable}`,
  full prompt → simplified prompt → metadata only. `ocr_text` comes first in the schema so a
  truncated answer still carries the searchable part.
- Classifier (step 3): few-shot prompt `prompts/classify_post.v1.md` + examples YAML →
  `{topic, is_ad, is_spoiler, quality}` via grammar-constrained JSON. v1 examples are synthetic
  placeholders until real posts exist.
- Injection heuristic v0 (step 6): strong/weak phrase lists (RU/EN) over text + OCR → `injection_flag`.
- Text normalization + `dedup_key` for the exact-hash dedup stage (D6).
- Runner: selects posts lacking enrichment for the current `model_version`
  (`<fast>+<vlm>|classify_post.v1|vlm_describe.v1`), album members inherit the group caption,
  upsert into `enrichment` — re-running is a no-op, `--force` recomputes in place.
- Tests: 24 (graph routing and degradation paths with a fake LLM, runner idempotency on SQLite,
  normalization, injection).
- Live smoke on the box (`scripts/ingest_smoke.py`): both VLM candidates transcribe the sample
  meme verbatim and return valid JSON first try; ad / spoiler / injection cases labelled as
  intended. Cost per text post ≈ 860 prompt tokens (few-shot prefix is cached) ≈ 2 s; per image
  post ≈ 25 s with Qwen3.6 (≈1200 image tokens) — Gemma would be ≈ 12 s.

**Decision** — `fast` and `vlm` tiers must be the *same* model while the router runs with
`--models-max 1`: two different models mean two swaps per post (observed: +20 s). D4 compares
"Qwen3.6 for both" against "Gemma 4 for both" on the same 200 posts.

**2026-09-19 — first real run** (`tgdigest ingest run --topic … --limit …`, 130 posts from
the three D2 channels, Qwen3.6 for both tiers, concurrency 2):

| topic (channel) | posts | wall time / post | prompt tokens / post | degraded |
|---|---|---|---|---|
| scifi (@horrorfantastcom, text + albums) | 60 | ≈ 2.5 s | 1158 (few-shot prefix cached) | 0 |
| cinema (@kinolikbez, vision on images) | 40 | ≈ 15 s | 1892 | 3 × vision:unreadable |
| humor (@memehunter, vision on every image) | 30 | ≈ 24 s | 2015 | 0 |

- Labels follow the post, not the channel: 77 % of @horrorfantastcom posts → `scifi`, the rest
  `other` (event invitations, comics) and one `humor`; 11 of 30 @memehunter memes → `cinema`
  (they are movie memes — a real ambiguity the labeled set on D5 has to settle).
- Ads flagged: 4/60 scifi, 11/40 cinema, 2/30 humor — spot checks agree (event promo, cross-posts).
- OCR/captions on real memes read correctly (posters, handwritten corrections, TV logos).
- No JSON failures, no repairs needed: grammar-constrained output held on 260 model calls.

## D4 — 2026-09-19/20 — VLM branch on real data

**Done** — report in [`docs/experiments/d4-vlm/`](docs/experiments/d4-vlm/README.md).
- OCR sample: 50 images (25 humor, 25 cinema), 36 with text, hand-checked gold
  (`scripts/ocr_eval.py`, `scripts/ocr_sheet.py`). **Qwen3.6 CER 0.000, Gemma 4 CER 0.111**
  (normalized; 22/36 exact). Gemma's misses are dense English screenshots and tiny captions;
  Russian meme text is verbatim on both.
- Throughput on real images, cache-free (`scripts/vlm_bench.py`): Qwen 2.6 images/min and
  flat under concurrency (compute-bound: vision encoder + 1 200-token prefill); Gemma 4.1 →
  8.5 images/min with 4 slots and a 250-token budget (decode-bound, batching shares weight reads).
- Found and documented a measurement trap: llama-server's RAM prompt cache made a repeated
  image set look 7× faster.
- Router now keeps both models resident (`--models-max 2`, 29 GiB): different models for the
  text and vision tiers without per-post swaps.

**Decision** — vision tier = Gemma 4 (≈ 30 h full pass vs ≈ 100 h), text tier = Qwen3.6. The
accuracy gap is real and recorded; a Qwen second pass on images where Gemma finds no text is
queued as a measured follow-up for the D8 retrieval ablation.

**Started** — full ingest pass (humor → cinema → scifi, concurrency 4) in the background.

## D5 — 2026-09-19/20 (in progress, waiting for the full pass) — Enrichment

**Done**
- `ingest links`: fetch → extract → summarize external links with explicit error classes
  (`video`, `http_xxx`, `timeout`, `not_html`, `too_large`, `paywall_or_stub`, `empty`,
  `irrelevant`) so the digest can say *why* a link has no summary.
- `ingest entities`: LLM extraction → grounding of films in Wikidata (post-year prior: a 2026
  post mentioning «Мумия» is the 2026 film, not 1932) and books in FantLab with an Open Library
  fallback; results cached in `entity_cache`. TMDB is unreachable from the box — Wikidata is
  the film source, recorded as a SPEC deviation.
- Distillation harness (`scripts/distill_classifiers.py`): BGE-M3 (CPU) + logistic regression
  against the teacher labels, agreement in Cohen's κ. Preliminary on 320 posts: topic κ 0.53,
  is_ad κ 0.27, quality κ 0.19 — too few labels to conclude; re-run at ≥ 500–800 teacher labels
  once the full pass has covered more posts.

**Running** — full ingest pass (humor → cinema → scifi), ≈ 450 messages/h at concurrency 6;
humor 56 % done by the evening of 2026-09-20.

## D6 — 2026-09-20/21 — Deduplication

**Done** — report in [`docs/experiments/d6-dedup/`](docs/experiments/d6-dedup/README.md).
- Cascade (`tgdigest dedup signatures|run`): exact text, identical file, DCT pHash ≤ 6,
  forwards; union-find with the album as the unit; idempotent wholesale rebuild.
- Labels: 107 pairs (67 from clusters, stratified by stage; 40 near misses at pHash 7–12 /
  TF-IDF ≥ 0.5). Prefilled with reasons, reviewed by the owner (3 corrections, then "by
  analogy") — the convention is recorded in the report.
- Baseline **precision 0.765 / recall 0.981**. Every error had a visible cause in the
  per-pair features (`scripts/dedup_labels.py features`), which gave four rules: contrast gate
  (std < 8 → no hash), illustration veto as a cannot-link constraint (same media under
  different stories: > 7 days or cross-channel article-length texts), rubric-caption veto,
  truncated-repost edge. After tuning **0.932 / 1.000** on the (corrected) tuning pairs;
  774 clusters / 1 721 posts / max 8. Ablation: the illustration veto alone is worth 13 points.
- Out of sample: hold-out of 52 fresh pairs (`--exclude` the tuning set) **0.947 / 0.947**;
  30 pairs that only the vetoes keep apart — 29 correctly apart, one debatable (pre-order vs
  on-sale of one magazine issue, 14 days). Owner's review of both samples pending.

**Found on the way** — two collector bugs, both invisible until the features table put
"same-day" posts months apart or showed "verbatim reposts" a day later: video/GIF posts
carried the collection time (3 519 rows) and replies carried the quoted parent's text (464
rows). Twelve of the 53 owner-confirmed duplicates were the second artefact; the "truncated
repost" rule had been fitted to it (112 merges → 2). Parser fixed with fixtures; rows repaired
in place by the new `collector refresh` (dates, texts, counters; stale enrichment dropped).

**Not done / next** — embedding stage of the cascade after the D7 index (the one hold-out
miss is exactly its case: a trailer as a bare link vs a still); OCR-text veto for pHash
matches of meme templates once the VLM pass is complete; forwards are too rare in this corpus
(1 cluster) to measure that stage.

## D7 — 2026-09-21 — Retrieval layer

**Done**
- Qdrant collection `posts`: one point per post (album = one post) with four indexing
  variants — `text`, `text_ocr`, `text_ocr_caption`, `full` (+ link summaries) — each as a
  BGE-M3 dense vector (1 024-d, cosine) and a BGE-M3 sparse vector, so the §6.4 ablation
  searches exactly the configuration it measures. Payload: topic/channel/label, `posted_at`
  for ranges, `is_ad` / `is_spoiler` / `quality`, `cluster_id` + `is_representative` (dedup
  collapsed at query time), source texts (for reranking and judging), `media_path`.
- Idempotent builds: `content_hash` over the texts (change → re-embed) and `meta_hash` over
  labels/clusters (change → payload rewrite only), so a dedup rebuild or a relabel does not
  cost embeddings.
- Hybrid search = dense + sparse fused by RRF inside Qdrant; filters; multi-query RRF;
  BM25 baseline (no stemming — recorded as a handicap of the baseline, not of the system).
- `tgdigest index build | search | stats`; 21 retrieval tests on an in-memory Qdrant with a
  deterministic fake embedder.
- Cost on the box (CPU, 8 threads at `nice 19` while the ingest pass runs): 18 987 posts →
  28 876 unique texts embedded in 90 min (≈ 5.3 texts/s); 9 586 posts carried enrichment at
  build time (OCR on 6 716, captions on 7 810).

**Decision** — history depth for v1 is three months: humor is enriched for the full six,
cinema/scifi run `ingest run --since 2026-06-21` (newest first), the older months can be
filled in later without touching anything else.

## D8 — 2026-09-21 (in progress) — Reranker, query rewriting, retrieval evaluation

**Done**
- `bge-reranker-v2-m3` over the head of the candidate list (passage = the variant's text
  recomposed from the payload sources, so the ablation reranks what it retrieved); 50 pairs in
  ≈ 10 s on 8 CPU threads under load.
- Query rewriting (`prompts/rewrite_query.v1.md`): conversational request → 1–3 post-like
  phrasings, fused by RRF; degrades to the original query when the model fails.
- Evaluation harness `scripts/retrieval_eval.py`: 40 queries (13 cinema, 13 scifi, 14 humor;
  factual / thematic / vague / visual) in `docs/experiments/d8-retrieval/queries.yaml`;
  `pool` runs 10 configurations (BM25 / dense / sparse / hybrid / +rerank on `full`, and the
  `text` / `+OCR` / `+OCR+caption` hybrid variants) and writes a blind labelling sheet;
  `judge` grades the pool with a local model; `agree` reports Cohen's κ against the human
  labels; `score` prints recall@20 / nDCG@10 / MRR per configuration and per query kind.

**Pending** — pooling on the rebuilt index, relevance labels (prefill + owner's review), the
judge calibration, the ablation table.

## D9 — 2026-09-21 — Search agent

**Done**
- Tool layer (`agent/tools.py`): every tool has a pydantic argument schema, a timeout and an
  explicit error kind (`invalid_args` / `not_found` / `unavailable` / `timeout` /
  `needs_confirmation` / `failed`); a failing tool returns a result, never an exception; the
  only writing tool (`update_profile`) runs after a confirmation callback.
- Tools (`agent/toolset.py`): `search_index` (hybrid + rerank, product filters: no ads,
  duplicates collapsed), `get_post` (text, media, OCR, captions, labels, entities, cluster
  neighbours, t.me link), `list_channels`, `web_search` (Wikipedia ru/en — named for the SPEC
  interface, documented as what it is), `fetch_url`, `lookup_film` (Wikidata),
  `get_profile` / `update_profile`.
- LangGraph agent (`agent/graph.py`): route (search / news / research, topic, period,
  spoiler filter) → rewrite → retrieve → grade → synthesize | rewrite_query (≤ 2) | broaden
  (drop filters once) | verify_external (research) | answer_with_caveat (6 iterations or
  40k tokens). Every step is recorded with latency and usage; every failure degrades the run.
- Langfuse: one trace per run with a child observation per step (usage, model, in/out);
  `tgdigest prompts push` registers the prompt files (label `v<N>`).
- `tgdigest agent ask "…" [--tier heavy] [--no-rewrite] [--no-rerank] [--json]`.
- Tests: 18 (tool contract, toolset over SQLite + in-memory Qdrant + mocked HTTP, graph paths
  with a scripted LLM, tracing with a recording tracer).

## D10 — 2026-09-21 — Self-correction, failures, budgets, research mode

Folded into D9's graph: rewrite_query (≤ 2) when few hits are relevant, broaden once when
none are, research mode verifies against Wikipedia + a fetched page before synthesis,
budgets of 6 iterations / 40k tokens end in an explicit caveat, every tool failure is a
recorded degradation. Injection quarantine and output checks were added on D17 (below).
`scripts/agent_eval.py`: router accuracy over 30 requests, chaos runs (5 failure
scenarios × 20) graded for correct degradation — both run when the LLM is free of the ingest
pass.

## D11–D12 — 2026-09-21 — Profile and interest score

- Onboarding sheet (`tgdigest profile onboard`): 36 posts, 12 per topic, ≤ 2 per channel,
  ads excluded — waiting for the owner's like/dislike/skip.
- Profile from votes: Laplace-smoothed topic weights, Bayesian channel affinity (prior = the
  user's like rate, 4 pseudo-votes), k-means interest centroids over the liked posts'
  BGE-M3 vectors (k grows with likes: 1 → 2 at six, up to 5), explicit exclusions.
- Score v1 (linear): 0.45 nearest-centroid cosine + 0.15 topic + 0.15 channel + 0.15
  channel-relative engagement + 0.10 novelty, −0.5 ads, −0.3 low quality. Baseline: top by
  views. `scripts/profile_eval.py` does leave-one-out AUC / precision@k on the owner's votes.
- **v2 (learned ranker) not built**: with ~36 onboarding votes there is nothing to learn
  from; the SPEC's buffer rule applies. The features are there for the day feedback exists.

## D13 — 2026-09-21 — Digest crew

Planner and Curator are code (5–12 items by reading budget, a slot per topic then profile
weights, ≤ 2 per channel, 60 % fresh / 40 % timeless, one post per duplicate cluster,
nothing from the last ten issues); Editor and Critic are prompts (`digest_editor.v1`,
`digest_critic.v1`: hallucination / spoiler / clickbait) with at most two rounds and
`critic_iterations` stored per issue. Scheduling lives in the bot (`/schedule <hour>`).

## D14 — 2026-09-21 — Service

FastAPI (`tgdigest api`): /search, /digest, /why/{digest}/{post} (explanation from the
score components — no LLM), /feedback (row + Langfuse score on the originating trace),
/profile, /users/scheduled, /post, /health, /metrics (Prometheus text). aiogram 3 bot
(`tgdigest bot`): text → search with numbered links and 👍/👎/💾 per cited post, /digest with
a keyboard per item and ❓ → /why, /profile, /schedule; a scheduler loop sends the daily
issue. The bot is a thin HTTP client of the API because the box cannot reach Telegram: it
runs on a machine that can (API through `make tunnel`) or on the box through `BOT_PROXY`.

## D15–D17 — 2026-09-21 (harnesses built, runs pending the LLM) — Evaluation

- LLM-as-judge for retrieval (`judge_relevance.v1`) running over the 1 856 pooled pairs; a
  100-pair calibration subset (stratified by the judge's grade, grades hidden) goes to the
  owner; Cohen's κ decides whether the judge's grades may fill the ablation table.
- Faithfulness by atomic claims (`extract_claims.v1` → `verify_claim.v1`) for answers and
  digest items; a human-check column in `claims.csv`.
- Pairwise digest judge (`pairwise_digest.v1`) in both orders → position-flip rate,
  longer-side win rate; the same pairs through gpt-oss-120b (another family) for the
  self-preference probe; v1 interest score vs the views baseline is the comparison.
- Injection: 45 planted posts in 8 classes with deterministic success predicates; the
  ingest heuristic gained generic "instructions about the answer" patterns (coverage 31/45
  on the set — in-sample — at a 0.04 % flag rate on the real corpus, 9 posts); quarantine of
  flagged posts and pages, output leak/URL checks. ASR guard off/on to be measured.
- Chaos: search timeout / down / empty, web tools down, step budget — 20 runs each, graded.
- Langfuse: 15 prompt files registered (`tgdigest prompts push`), the three test sets are
  datasets (`push-datasets`), scripts record scored runs with `--langfuse`.
