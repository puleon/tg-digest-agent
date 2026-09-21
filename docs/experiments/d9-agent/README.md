# D9/D10 — Search agent: router accuracy and graceful degradation

**Question.** Does the router send requests to the right mode (search / news / research) with
the right filters, and does the agent degrade correctly when its tools fail? (SPEC §8.5,
§8.6.)

## Router — 30 requests, `route_query.v1`, Qwen3.6-35B-A3B

[`routes.yaml`](routes.yaml) was written before the prompt was tuned on it: 12 search, 10
news, 8 research; expected topic, period and spoiler flag per request.

| metric | value |
|---|---|
| mode accuracy | **0.933** (28/30) |
| topic | 0.867 (26/30) |
| period detected when implied | 0.967 |
| spoiler flag | 1.000 |
| cost | 363 tokens, 12.7 s per request at concurrency 2 while the pool judge shared the model |

Confusion (rows expected, columns predicted): search 11 / 0 / 1, news 1 / 9 / 0,
research 0 / 0 / 8. The two mode misses are defensible readings: «что писали про органоиды
мозга» went to research (a question about coverage), «свежие мемы за сегодня» to search with
`days = 1` (the period was kept, the mode was not). Topic misses: two research questions
left the topic empty (the router prefers no filter when unsure — the intended behaviour),
«Колесо времени» went to cinema (a series adapted from books).

## Research tasks — 20 multi-step scenarios (SPEC §8.6)

[`research.yaml`](research.yaml): 20 questions that need the research route (search → grade →
Wikipedia + a fetched page → synthesis), each with **fact groups checked against the corpus
before the run** — a correct answer must contain at least one substring of every group
(«Батиста» *and* one of «Хёрст / травма / бицепс» for the God of War recast). Success =
the run answered without a caveat, cited at least one post, and every group is present. The
report also records whether the router actually took the research route, whether an external
source reached the synthesis, and steps / tokens / seconds per task — the harness-efficiency
metric of §8.6. `scripts/agent_eval.py research`.

**Run A — 20 tasks, web tools unreachable** ([`results/research_no_web.jsonl`](results/research_no_web.jsonl)).
The first run went out through the collector's SOCKS tunnel, which is only up during a
collection: every `web_search` failed with `ConnectError` and every run degraded to posts
only. Nobody planned this chaos test, so it is reported as one:

| metric | value |
|---|---|
| success (facts present, cited, no caveat) | **0.95** (19/20) |
| routed to research | 0.95 (19/20; `r13` went to search, broadened once, still succeeded) |
| external source in the synthesis | 0.00 — `web_search:unavailable` recorded in 19/19 |
| crashes | 0 |
| steps / prompt tokens / completion tokens / seconds per task (all 20) | 7.9 / 3 313 / 933 / 236 |
| … per successful task | 7.7 / 3 276 / 910 / 237 |

The one miss (`r11`, Marvel's Wolverine) answered what critics wrote and left out the release
date. Six tasks needed the full `rewrite_query` loop (12 steps, ≈ 6 000 tokens), one
broadened its filters once, thirteen finished in the minimal six steps. Runs took 90–465 s because the injection evaluation and the
pool judge shared the model; `r01` also logged one `search_index:timeout` (the reranker under
that load) and recovered on the next query.

The configuration bug is fixed (`AGENT_PROXY`, separate from the collector's `WEB_PROXY`,
direct by default); **run B** with Wikipedia reachable is in progress — it will show what
the external step adds on a task set whose facts are all in the corpus, and what it costs.

## Chaos — five injected failures × 20 runs (SPEC §8.5)

SPEC names six scenarios. Five are injected into the real agent by wrapping one tool
(`scripts/agent_eval.py chaos`): `search_timeout` (the search tool sleeps past its 2-second
timeout), `search_down` (Qdrant "connection refused"), `search_empty` (no hits), `web_down`
(HTTP 500 from Wikipedia and the page fetch) and `budget` (one iteration allowed). Each runs
20 times over ten fixed requests. Correct degradation is graded by predicate: an answer
exists; no posts are cited when nothing was retrieved; a limitation is stated; nothing
external is cited when the web tools failed; the budget case ends in an explicit caveat.
A crash is a failure.

The sixth scenario — **invalid JSON from the VLM** — belongs to the ingest graph, not the
agent, and is measured on the real pass rather than injected: of 6 740 humor posts in one run,
24 ended in `vision:failed` (invalid JSON twice, or a timeout) and 4 in `vision:unreadable`;
all 28 degraded to metadata-only enrichment, none failed the post (`tests/ingest/test_graph.py`
pins the same paths with a fake model).

**Results — 100 runs** ([`results/chaos.jsonl`](results/chaos.jsonl)):

| scenario | runs | correct degradation | crashes | mean s |
|---|---|---|---|---|
| search_timeout | 20 | 1.00 (20/20) | 0 | 32 |
| search_down | 20 | 1.00 (20/20) | 0 | 13 |
| search_empty | 20 | 1.00 (20/20) | 0 | 13 |
| web_down | 20 | 1.00 (20/20) | 0 | 70 |
| budget | 20 | 1.00 (20/20) | 0 | 43 |

Every search failure ended in the explicit caveat with no citations (60/60); every budget
run ended in a caveat (20/20); in `web_down`, 6 runs were routed to research, hit the 500s,
recorded `web_search:unavailable` and answered from posts, and 14 never needed the web tools
(the router sent them to search or news — those runs test nothing about the web and are
counted as correct because nothing went wrong). A clean sheet here is not a surprise and
should not read as one: the degradation paths are code, not model behaviour — the harness
confirms they hold under real timeouts through the real tool layer with the real model, and
that no failure escapes as an exception. The model-dependent part is `web_down`'s six
research runs and the honesty of the caveats, which the faithfulness harness (D15) checks
separately.
