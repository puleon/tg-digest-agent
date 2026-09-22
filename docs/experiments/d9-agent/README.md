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

Four runs, one task set, the same agent except for the external step
([`results/research_*.jsonl`](results/)):

| run | what changed | success | routed research | external source used | Wikipedia empty | page failed | steps | tokens / task | s / task |
|---|---|---|---|---|---|---|---|---|---|
| A | web tools unreachable (the collector's tunnel proxy, down) | **19/20** | 19/20 | 0/20 | — | — | 7.9 | 4 246 | 236 |
| B | direct web; Wikipedia searched by the whole request | **19/20** | 19/20 | 9/20 | 10 | 4 | 8.3 | 4 605 | 438 |
| C | lookup by the rewrite step's `keywords` — which prompt v1 never asked for | **19/20** | 19/20 | 9/20 | 10 | 4 | 7.9 | 4 474 | 321 |
| D | `rewrite_query.v2` asks for the names; Wikipedia articles fetched as text via the API | **20/20** | 19/20 | **18/20** | 1 | 0 | 7.6 | 4 612 | 247 |

The same task fails in every run (`r11`, Marvel's Wolverine: the answer has the reviews and
drops the release date); every other task is answered from the posts with citations. Seconds
are wall-clock on the shared box and not comparable between runs (A ran next to the injection
evaluation and the pool judge, B and C next to the ingest and entity passes).

What the external step actually did, run by run:

- **A** never reached the web: every research run recorded `web_search:unavailable` and
  synthesized from the posts. Nobody planned this chaos test; it is reported as one, and it
  says the corpus carried the facts on its own.
- **B** reached Wikipedia, and Wikipedia search on a conversational Russian request («кто в
  итоге сыграет Кратоса в сериале по God of War и почему заменили актёра») returned nothing
  for 10 of 20; of the 9 hits, 4 pages failed to fetch — long articles exceed the 1 MB page
  limit of `fetch_url`, so the tool returned `too_large`. Five tasks saw an external source.
- **C** was meant to search by the names the rewrite step extracts, and was identical to B to
  the task, because `rewrite_query.v1` never asked for keywords and the model left the list
  empty — the lookup fell back to the request. A fix that was not a fix, kept as measured.
- **D** asks for the names explicitly (`rewrite_query.v2`) and reads Wikipedia articles
  through the extracts API: an external source reached the synthesis in **18 of 20** tasks
  (one empty search — «Человек-паук: Новый день» has no article yet — and `r13`, which the
  router sends to search every time), zero page failures, and the one task that had failed
  in A–C passed (`r11` now names the release date, which the Wikipedia article carries).
  Steps and tokens are unchanged (7.6 steps, 4 612 tokens); the cost of the external step is
  the fetch, not model calls.

The success column says something on its own: on questions whose answers are in the corpus
the external step is verification, not retrieval — it changes what the answer can *confirm*
(one extra fact in 20 tasks), not whether the answer is found. The faithfulness harness (D15)
is where verification shows up; the three configuration bugs D fixed were invisible to the
success rate and visible only because the run records what each tool returned.

Runs are recorded on the `research-tasks` dataset in Langfuse (`A · web unreachable`,
`B · lookup by request`, `C · keywords (v1 prompt, empty)`, `D · keywords v2 + extracts`) with
success, routed_research, steps, tokens and seconds per task.
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
