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

## Chaos — five scenarios × 20 runs

_Pending: runs queued behind the pool judge._
