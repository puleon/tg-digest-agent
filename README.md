# tg-digest-agent

[![ci](https://github.com/puleon/tg-digest-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/puleon/tg-digest-agent/actions/workflows/ci.yml)

Agentic search and a personalized daily digest over public Telegram channels in three topics —
**science fiction**, **smart humor** and **cinema** — built end to end on **local, CPU-only models**
(AMD Ryzen 9 9950X, 96 GB RAM, no GPU).

The bot is the by-product. The deliverable is the measurement: every component ships with a
numeric quality metric, every experiment is a Langfuse dataset run, and negative results are
reported as such. See [`SPEC.md`](SPEC.md) (Russian) for the full specification and
[`PROGRESS.md`](PROGRESS.md) for the day-by-day engineering log.

## Results at a glance

Every number below comes from a script in `scripts/` over data the reports describe; the
reports list what was *not* achieved next to what was. Status: **day 18 of 18 — final
measurements landing** (rows marked ⏳ are runs still in progress on the box).

| Experiment | Headline | Report |
|---|---|---|
| D1 · CPU serving | Qwen3.6-35B-A3B Q4_K_M: 260 tok/s prefill, 19 tok/s generation; Gemma 4 26B-A4B: 204 / 17 tok/s, verbatim Cyrillic OCR at 262 image tokens; gpt-oss-120b (heavy tier): 142 / 17 tok/s at 63 GiB. Docker ≈ native; SMT and MTP speculative decoding both slower. | [d1-llm-benchmark](docs/experiments/d1-llm-benchmark/README.md) |
| D2 · Corpus | 29 public channels through the `t.me/s` preview (no account): 18 987 posts / 25 939 messages, 22 888 media files (1.7 GB), six months, 2.5 h through a reverse SOCKS tunnel, 0 flood waits. Two parser bugs found later by the dedup features (video dates, reply texts), repaired in place with `collector refresh`. | [PROGRESS D2](PROGRESS.md) |
| D4 · VLM / OCR | 50 hand-checked images: Qwen3.6 CER 0.000, Gemma 4 CER 0.111 (normalized). Cache-free throughput: Qwen 2.6 images/min flat under concurrency, Gemma 4.1 → 8.5 images/min with 4 slots. Vision tier = Gemma 4 (3.3× the throughput, the accuracy gap recorded). | [d4-vlm](docs/experiments/d4-vlm/README.md) |
| D5 · Distillation | Logistic regression on BGE-M3 embeddings vs the LLM teacher, 2 916 posts: topic **κ 0.80** (accuracy 0.864, majority 0.338); is_ad κ 0.40 and quality κ 0.33 with accuracy *below* the majority baseline; spoilers too rare to learn. Two of three targets are negative results — the LLM stays the classifier. | [d5-distillation](docs/experiments/d5-distillation/README.md) |
| D6 · Deduplication | 107 hand-labelled pairs: cheap cascade (exact text, file hash, pHash ≤ 6, forwards) precision 0.765 / recall 0.981 → with a contrast gate, illustration and rubric cannot-link vetoes and an OCR template veto **0.953 / 1.000** on the tuning pairs, **0.947 / 0.947** on a 52-pair hold-out. | [d6-dedup](docs/experiments/d6-dedup/README.md) |
| D7 · Index | BGE-M3 dense + sparse for four indexing variants (text / +OCR / +caption / full) in Qdrant, hybrid RRF, payload filters, duplicates collapsed at query time; 18 987 posts → 29 150 texts embedded in 90 min on the CPU next to the ingest pass; rebuilds re-embed only changed texts. | [PROGRESS D7](PROGRESS.md) |
| D8 · Retrieval ablation | 40 queries, 1 856 pooled pairs graded by the local judge (κ 0.51 vs 100 human-labelled pairs, 0.62 weighted — the judge is lenient by one grade): hybrid + rerank **nDCG@10 0.816 / recall@20 0.691 / MRR 0.974** vs BM25 0.533 / 0.522 / 0.850; reranking is the biggest single gain (0.693 → 0.816). On humor the text-only index finds a third of what OCR + captions find (recall@20 0.307 → 0.599; visual queries nDCG@10 0.280 → 0.727). | [d8-retrieval](docs/experiments/d8-retrieval/README.md) |
| D9 · Router | 30 requests: mode accuracy **0.933**, topic 0.867, period 0.967, spoiler flag 1.000; 363 tokens and 12.7 s per request under contention. | [d9-agent](docs/experiments/d9-agent/README.md) |
| D9 · Research tasks | 20 multi-step tasks with corpus-checked facts: **19/20** answered correctly with citations, 7.9 steps / 4 246 tokens / 236 s per task under contention — in a run where every Wikipedia call failed (a proxy misconfiguration, fixed); ⏳ the run with the web reachable is in progress. | [d9-agent](docs/experiments/d9-agent/README.md) |
| D10 · Chaos | Five injected failures × 20 runs through the real agent: **100/100 correct degradation, 0 crashes** (caveat and no citations when search fails; posts-only answers when the web is down; explicit caveat on budget). The sixth SPEC scenario (invalid VLM JSON) on the real pass: 28 of 6 740 posts degraded to metadata, 0 failed. | [d9-agent](docs/experiments/d9-agent/README.md) |
| D11–12 · Profile | 36 onboarding votes (12 likes), leave-one-out: interest score v1 **AUC 0.701 / precision@10 0.40** vs the views baseline 0.427 / 0.20 (worse than random — the most viewed posts are humor, which this reader dislikes). The learned v2 ranker was **not built** on 36 votes. | [d11-profile](docs/experiments/d11-profile/README.md) |
| D15 · Faithfulness | 12 answers split into 132 atomic claims and checked against the cited posts: **5.3 % not supported** (5 contradicted, 2 unsupported); an audit of the verifier finds 5 real slips (a day count, a misattributed figure, two untraceable dates) and 2 verdicts that penalize a source conflict the answer had pointed out itself — 3.8 % on a strict reading. One digest issue: 25 % of 32 claims flagged, 2 real (6 %) — the rest is the Editor's «читатели узнают…» framing, which the extractor turns into claims no post can support. | [d15-faithfulness](docs/experiments/d15-faithfulness/README.md) |
| D16 · Digest pairwise | ⏳ v1 score vs views baseline judged in both orders by Qwen3.6 and by gpt-oss-120b: wins, position-flip rate, longer-wins rate, cross-family κ. | [d16-digest](docs/experiments/d16-digest/README.md) |
| D17 · Prompt injection | 45 planted posts in 8 classes through the full agent: ASR **0.18 → 0.09** with the guard (8/45 → 4/45). Compliance — a leaked prompt, a tool call, the canary as the answer — **0 in both conditions**; 5 of the 8 guard-off successes are the model narrating the injected text (the URL still reaches the user — the guard strips it), 3 of the 4 that remain are planted false facts, which nothing in v1 defends against. | [d17-injection](docs/experiments/d17-injection/README.md) |

## What it does

Three things, all from the terminal (`tgdigest agent ask`, `tgdigest digest make`) or the
bot. The transcripts below are real runs on the box (Qwen3.6-35B-A3B, CPU; timings include
contention with the ingest pass and the evaluations).

**Search** — «что-нибудь смешное про котов» → route `search / humor` → rewrite into three
post-like phrasings → hybrid retrieval → grade (2 of 10 relevant) → `rewrite_query` →
retrieve again → grade (9 of 10) → synthesize. 5 203 tokens, 8 steps, 8 cited posts:

> Вот подборка смешных постов про котов из канала @am_devs:
> - **Заговор кошек:** Мем предупреждает, что кошки контролируют Землю миллионы лет, и прямо сейчас одна из них находится у вас дома [post 8653].
> - **Список на двери:** На двери висит табличка с перечислением: «РЫБА, ПИВО, КОШКА», что намекает на то, что кошка тоже в списке товаров [post 8648].
> - **Манул Тимофей:** Манул с человеческим лицом и прической, который занялся «мьюингом» и сделал пересадку волос, спрашивая, дадут ли ему теперь «дадим» [post 8472].
> - **Трансформация:** Два котенка обнимаются, а текст описывает панику человека, который чувствует, как у него ломаются кости, растет шерсть и он превращается в котенка [post 8891].
>
> (four of the eight items)

Every step is in the run's state and in its Langfuse trace: `grade 251 s · graded 10 ·
relevant 2 · 1 031 + 423 tokens`, and so on — the 15-minute wall-clock of this particular run
is the ingest pass holding the model, not the graph.

**Research** — «кто в итоге сыграет Кратоса в сериале по God of War и почему заменили
актёра» → route `research / cinema` → retrieve → grade → verify against Wikipedia + a fetched
page → synthesize. 6 steps, 3 353 tokens, 196 s. (In this run the web was unreachable — a
proxy misconfiguration since fixed — so the verification step recorded
`web_search:unavailable` and the answer rests on the posts alone; the citations are real.)

> Кратоса в сериале по God of War сыграет Дэйв Батиста [post 5808].
>
> Причиной замены стало то, что первоначально назначенный на роль Райан Херст получил
> тяжёлую травму — разрыв бицепса — во время съёмок [post 5578] [post 5365]. На восстановление
> актёру требовалось от четырёх до шести месяцев [post 5365]. Студия Amazon решила не ждать
> выздоровления Херста … и посчитала более целесообразным переснять уже отснятые серии с
> новым актёром [post 5808].

When a tool fails the run says so instead of guessing: the same task with Wikipedia
unreachable answers from the posts and records `web_search:unavailable`; with nothing
retrieved it answers «⚠️ Ответ неполный …» and cites nothing (D10).

**Digest** — `tgdigest digest make --user 0 --minutes 10`: Planner and Curator pick 5–12
posts for the reading budget (a slot per topic weighted by the profile, ≤ 2 per channel,
60 % fresh, one per duplicate cluster, nothing shown in the last ten issues), the Editor
writes a title and a one-line reason per item, the Critic rejects hallucinated, spoiling or
clickbait lines (≤ 2 rounds). The owner's first issue — profile scifi 0.47 / cinema 0.40 /
humor 0.13, so the plan is 4 / 4 / 2; the Critic sent two titles back («Трейлер третьей части
„Дюны“» was more than the post said) — 14 649 tokens, 2 critic rounds, 866 s under contention:

> В выпуске за 19 сентября: трейлеры, новости кино, разбор фейков и порция юмора.
>
> 1. **Первый тизер «Соника в кино 4»** [scifi · @mirf_ru · 2026-09-19] — Фанаты франшизы узнают о дате премьеры и ключевых персонажах нового фильма, выходящего в 2027 году.
> 2. **Трейлер третьей части «Дюны»** [scifi · @starlighthousekeeping] — Автор отмечает эпичность нового ролика и надеется на более полное раскрытие галактического джихада Дени Вильнёва.
> 4. **Фейк про иранские надувные танки** [scifi · @theworldisnoteasy] — Статья разбирает вирусную новость о покупке Китаем резиновых муляжей и объясняет реальную военную логику обмана.
> 6. **Новости кино: сиквелы и фестивали** [cinema · @seance_light] — Рубрика сообщает о старте съемок «Войны миров Z», проблемах с «Я — легенда 2» и обзоре фильма «До последнего грамма».
> 9. **Мем про диету Никиты Михалкова** [humor · @memehunter] — Шутка сопоставляет заголовок о новом фильме режиссера с текстом о его диете, предлагая название «Русский овощ».
>
> (five of the ten items)

In the bot every item has 👍 👎 💾 and ❓; the last one is `GET /why/{digest}/{post}` — an
explanation assembled from the score's components, no model call:

> Итоговый балл 0.66. Похоже на ваши интересы (близость 0.62); тема «scifi» в приоритете в
> профиле (0.70); канал @mirf_ru: доля лайков 0.56; популярность в норме для канала; в плане
> выпуска на тему scifi отведено 4 мест; свежий (последние 3 дня).

A 👍/👎 lands as a `feedback` row and as a score on the Langfuse trace that produced the issue.

## Why these topics are technically interesting

- Two of three domains are mostly **visual** → a VLM/OCR branch is mandatory, not a bonus.
- Humor is hard to find by text → an honest **semantic search** problem where BM25 fails.
- Memes and film stills are reposted en masse → **deduplication** is critical and measurable.
- Films and books are entities with external databases → natural **tool use and grounding**.

## Architecture

```mermaid
flowchart TB
    TG[t.me/s/&lt;channel&gt; · public web preview] --> C[Collector<br/>incremental, idempotent, FloodWait-aware]
    C --> PG[(Postgres 16<br/>posts · enrichment · clusters · feedback · digests)]
    PG --> I[Ingest Agent · LangGraph<br/>triage → VLM/OCR → classify → injection flag<br/>links · entities · dedup cascade]
    I --> PG
    PG --> IX[Index builder<br/>BGE-M3 dense + sparse · 4 text variants]
    IX --> Q[(Qdrant)]
    Q --> R[Retrieval<br/>hybrid RRF → rerank → query rewrite]
    R --> A[Search Agent · LangGraph<br/>route → retrieve → grade → synthesize<br/>rewrite · broaden · verify · caveat · guardrails]
    PG --> P[Profile & interest score]
    P --> D[Digest crew<br/>Planner → Curator → Editor ⇄ Critic]
    A --> API[FastAPI]
    D --> API
    API --> BOT[Telegram bot · aiogram 3<br/>👍 👎 💾 · /why · /schedule]
    A -.traces, datasets, scores.-> LF[Langfuse]
    D -.-> LF
    LLM[llama-server · GGUF · CPU<br/>Qwen3.6-35B-A3B text · Gemma 4 26B-A4B vision · gpt-oss-120b on demand] -.OpenAI-compatible API.-> I
    LLM -.-> A
    LLM -.-> D
```

## Stack

| Layer | Choice | Why |
|---|---|---|
| Collection | Telegram's public web preview (`t.me/s/<channel>`, no account) — Telethon (MTProto) as an optional second source | Bot API cannot read arbitrary public channels; the preview exposes text, ids, dates, full-size photos, albums, forwards, reactions and the numeric channel id |
| Storage | PostgreSQL 16 + SQLAlchemy 2 | Relational links between posts, clusters, feedback |
| Vectors | Qdrant | Self-hosted, payload filters, hybrid search |
| Embeddings / rerank | BGE-M3 / bge-reranker-v2-m3 | Dense + sparse in one model, strong on Russian |
| Model serving | llama.cpp `llama-server` (router mode, GGUF) | OpenAI-compatible API, prefix caching, on-demand model load/unload; CPU-friendly |
| Fast tier (mass ops + VLM/OCR) | multimodal MoE, ~3B active: Qwen3.6-35B-A3B, Gemma 4 26B-A4B ([D1 benchmark](docs/experiments/d1-llm-benchmark/README.md)) | Memory-bandwidth-bound CPU favors few active params; one weight set for text and images |
| Heavy tier (second judge, synthesis on request) | gpt-oss-120b MXFP4, loaded on demand | Another model family for the self-preference probe; does not fit in RAM next to the rest |
| Orchestration | LangGraph | Explicit state graphs for ingest and search; every node's usage and latency in the state |
| Observability / eval | Langfuse (self-hosted) | Traces, datasets, experiments, scores |
| Interface | FastAPI + aiogram 3 | |
| Infra | Docker Compose, Makefile, uv | `make up` brings everything up |

## What it costs

All inference is local; the box is an AMD Ryzen 9 9950X (16 cores) with no GPU, so the honest
cost unit is **seconds of CPU wall-clock**, and tokens are reported so the same work can be
priced at hosted-API rates. The money column prices the *measured* token counts at published
per-million-token rates (input / output): Claude Haiku 4.5 $1 / $5, Claude Sonnet 5 $2 / $10
(list prices as of 2026-06). Images cost tokens differently on hosted APIs (by pixel count),
so the vision rows are approximate; everything else is a straight multiplication.

| Operation | Tokens in / out | Seconds on the box | $-equivalent Haiku 4.5 | Sonnet 5 |
|---|---|---|---|---|
| Enrich one post (classify + vision when needed, `ingest run`) | 1 260 / 151 (mean over 6 740 humor posts) | 9.9 wall-clock at concurrency 6 (≈ 364 posts/h) | $0.0020 | $0.0040 |
| Enrichment done so far (12 333 posts: humor for six months, cinema for three) | 15.5 M / 1.9 M | ≈ 34 h | $25 | $50 |
| Embed the corpus for search (BGE-M3, 4 variants, 29 150 texts) | — | 90 min at 8 threads | — | — |
| Route one request (`route_query.v1`) | 296 / 59 | 12.7 at concurrency 2 (D9) | $0.0006 | $0.0012 |
| Search-mode question, end to end (`agent ask`; 9 runs of D15) | 3 162 / 1 113 | 202 for the agent's own steps, 80–450 | $0.0087 | $0.0175 |
| Research-mode question (20 tasks of D9, run A) | 3 313 / 933 | 236 (90–465) | $0.0080 | $0.0160 |
| Faithfulness check of one answer (claims → one verification call per claim) | 8 541 / 672 (5–20 claims) | included above | $0.012 | $0.024 |
| Judge one pooled (query, post) pair (`judge_relevance.v1`, D8) | not recorded | 6.9 at concurrency 2 | | |
| One digest issue (10 items; planner + curator in code, editor + critic, 2 rounds) | 11 978 / 2 671 | 866 under contention | $0.025 | $0.051 |

All agent timings were taken while the ingest pass, the pool judge or another evaluation
held the model — the D1 benchmark's 19 tok/s generation and 260 tok/s prefill on an idle box
put a 4 000-token search answer at ≈ 60–70 s of pure inference. The point of the column is the
order of magnitude: a question costs a cent at hosted rates and a few CPU-minutes locally;
enriching the corpus is the expensive part and it happens once.

## Decisions and deviations from the SPEC

The SPEC (`SPEC.md`, Russian) is the source of truth; every deviation is recorded there or in
`PROGRESS.md` with a date. The ones that matter:

- **Local models only, llama.cpp instead of vLLM, no API model.** Written into the SPEC on day 1.
  The heavy tier is gpt-oss-120b MXFP4, loaded on demand; the fast tier is a 3B-active MoE,
  which is what a memory-bandwidth-bound CPU wants ([D1](docs/experiments/d1-llm-benchmark/README.md)).
- **Telegram's public web preview instead of Telethon.** No collector account could be
  created; `t.me/s/<channel>` exposes everything the schema needs except forward counts. The
  box cannot reach Telegram at all, so collection runs through a reverse SOCKS tunnel from
  the laptop.
- **Wikidata instead of TMDB** for film grounding (TMDB is unreachable from the box); FantLab
  with an Open Library fallback for books. `web_search` is Wikipedia search and is documented
  as such.
- **Two models for two tiers**: Qwen3.6 reads Cyrillic better (CER 0 vs 0.11) but Gemma 4 is
  3.3× faster per image; the full pass would have taken 100 h instead of 30
  ([D4](docs/experiments/d4-vlm/README.md)).
- **Three-month enrichment window** for cinema and scifi (humor has all six months) — the
  owner's call on 2026-09-21: the pipeline does not care about depth, the calendar does.
- **Tools are called by code, not chosen by the model.** The router picks the mode, the graph
  decides which tool runs; the model reads results, grades and writes. Tool-selection accuracy
  (§8.6) is therefore router accuracy, and the writing tool (`update_profile`) is unreachable
  from retrieved content by construction ([D17](docs/experiments/d17-injection/README.md)).
- **No learned interest model (v2)**: with 36 onboarding votes there is nothing to fit; the
  SPEC's buffer rule applies, the features exist for the day feedback does.
- **BM25 baseline without stemming** — a handicap of the baseline, stated where it is used.
- **`lookup_film` is a tested tool the v1 graph never calls**: entity grounding happens at ingest
  time; the agent verifies through Wikipedia + a fetched page. Wiring it into research mode is
  in the roadmap.

## What did not work

- **Distilling the ad and quality classifiers** — κ 0.40 / 0.33, accuracy below the majority
  baseline: ads are lexical, quality is the teacher's own least consistent label
  ([D5](docs/experiments/d5-distillation/README.md)).
- **The embedding stage of dedup** never ran on the corpus: the cheap stages plus vetoes
  reached 0.95 precision at recall 1.0 on the labels, and the one hold-out miss is a trailer
  link vs a still — not something a text embedding fixes.
- **Forwards as dedup ground truth**: 16 source messages forwarded by two or more corpus
  channels — one cluster, too rare to measure the stage.
- **The heuristic injection detector flagged posts *about* AI** («для ИИ») — 15 posts before
  the rule was dropped, 9 after.
- **Ollama ran Gemma 4 55 % faster at generation** than llama.cpp with the same quant type — not
  pursued (one serving stack), recorded as an open question.
- **MTP speculative decoding and SMT** both made the CPU slower.
- **A 7× "speed-up" that was the prompt cache**: llama-server's RAM prompt cache made a repeated
  image set look seven times faster; the VLM benchmark is cache-free for that reason.
- **The "truncated repost" dedup rule** was fitted to a parser artefact (replies carrying the
  parent's text); once the collector was fixed it explained 2 pairs, not 112.
- **The agent's web tools shared the collector's tunnel proxy**, which is only up during a
  collection: the first research run and the faithfulness answers verified nothing
  (`web_search:unavailable` in every research-mode run) — and still scored 19/20, because
  the facts were in the posts. Fixed with a separate `AGENT_PROXY` (direct by default).
- **Wikipedia search on the conversational request** returns nothing for half of the research
  tasks («кто в итоге сыграет Кратоса … и почему заменили актёра»); the lookup now uses the
  names the rewrite step extracts (run C, in progress).
- **The link-summary variant of the index (`full`) is identical to `text_ocr_caption`** in
  the ablation — the link pass had covered a handful of posts when the index was built.
- **The local relevance judge is lenient by one grade** (κ 0.51 against human labels, 0.62
  weighted) — good enough to rank configurations, not to quote absolute recall.
- **The injection predicates count narration as success**: the model *describing* «the post
  asks the assistant to answer ZEBRA-7731» trips the canary check. Five of eight guard-off
  "successes" are that; they are reported as measured, with the reading next to them.
- **Three of the four planted-fact attacks look like ordinary posts and are cited as such**,
  guard or no guard — nothing in v1 cross-checks a claim across channels.

## Quickstart

```bash
cp .env.example .env            # fill in secrets and MODELS_DIR
make install                    # uv venv + pre-commit hooks
make up                         # postgres, qdrant, llama-server, langfuse — waits until healthy
make migrate                    # schema
make collect-channels && make collect   # corpus (through the reverse tunnel, see below)
uv run tgdigest ingest run --since 2026-06-21 --concurrency 6   # enrichment (hours, CPU)
uv run tgdigest dedup signatures && uv run tgdigest dedup run    # duplicate clusters
uv run tgdigest index build     # embeddings → Qdrant (idempotent)
uv run tgdigest agent ask "что-нибудь смешное про котов"        # the agent from the terminal
make api                        # HTTP API on :8000 …
make bot                        # … and the Telegram bot against it (BOT_TOKEN, BOT_ALLOWED_USERS)
make test                       # unit tests (no services needed)
```

Services bind to `127.0.0.1` only. When they run on a remote box, `make tunnel` forwards the UIs
(Langfuse `:3000`, Qdrant `:6333`, LLM `:8080`) and `make remote T=<target>` syncs the tree and
runs a make target there. The box itself cannot reach Telegram, so collection runs there with
its egress routed back through the laptop over an OpenSSH reverse SOCKS tunnel
(`make collect-channels`, `make collect ARGS="--topic humor"`; `WEB_PROXY` in the box `.env`).
The bot needs Telegram too: run it on the laptop against the forwarded API (`make tunnel`,
then `make bot` with the token in the laptop's `.env`), or on the box with `BOT_PROXY`
pointing at `make egress`.

## Repository layout

```
src/tgdigest/     application package (collector, ingest, dedup, retrieval, agent, digest, bot, api)
config/           channels.yaml — the corpus definition
prompts/          versioned prompts (also managed in Langfuse)
docker/           service configs: llama-server model presets, postgres init
scripts/          benchmarks and one-off experiment runners
docs/experiments/ one report per experiment, with the numbers
tests/            pytest; `integration` marker for tests that need services
```

## Roadmap (beyond the 18 days)

- Image-side retrieval: a SigLIP index of the pictures themselves next to the caption text —
  the visual queries the ablation isolates are the ones it would move.
- Wire `lookup_film` into research mode; entity-aware routing for cinema questions.
- Channel discovery through forwards and mentions; the collector already keeps them.
- A learned interest ranker once feedback exists (the v2 features are in `profile/model.py`);
  distil it on-device with the same harness as D5.
- Multi-turn memory and clarifying questions in the bot.
- A second OCR pass with Qwen3.6 on the images where Gemma found no text, measured against the
  D8 protocol.
- Fill the older three months of cinema and scifi enrichment (`ingest run` without `--since`).

## License

MIT
