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

## Results — 12 answers, 132 claims ([`results/answers.jsonl`](results/answers.jsonl), [`results/claims.csv`](results/claims.csv))

| answer | topic · mode | claims | supported | contradicted | unsupported | share | agent tokens | s |
|---|---|---|---|---|---|---|---|---|
| c01 трейлер «Одиссеи» | cinema · search | 8 | 8 | 0 | 0 | 0.00 | 1 813 + 691 | 136 |
| c04 сборы «Человека-паука» | cinema · search | 20 | 19 | 1 | 0 | 0.05 | 3 170 + 947 | 222 |
| c07 рецензии на «Мумию» | cinema · research | 8 | 8 | 0 | 0 | 0.00 | 4 198 + 1 048 | 228 |
| c10 фильмы с Мэттом Дэймоном | cinema · search | 5 | 5 | 0 | 0 | 0.00 | 3 799 + 1 348 | 170 |
| c13 что нового у Ридли Скотта | cinema · news | 18 | 16 | 2 | 0 | 0.11 | 5 039 + 1 290 | 349 |
| s03 сериал «Трудно быть богом» | scifi · search | 20 | 20 | 0 | 0 | 0.00 | 2 429 + 792 | 195 |
| s06 органоиды мозга | scifi · research | 7 | 7 | 0 | 0 | 0.00 | 2 098 + 651 | 200 |
| s09 трейлер «Колец власти» | scifi · search | 5 | 5 | 0 | 0 | 0.00 | 3 456 + 1 439 | 343 |
| s12 розыгрыши «Мира фантастики» | scifi · search | 19 | 19 | 0 | 0 | 0.00 | 2 502 + 810 | 330 |
| h02 кот перед зеркалом | humor · search | 6 | 6 | 0 | 0 | 0.00 | 4 953 + 1 783 | 481 |
| h05 бросить курить электронку | humor · search | 9 | 7 | 2 | 0 | 0.22 | 4 225 + 1 533 | 361 |
| h08 розыгрыш футболки | humor · search | 7 | 5 | 0 | 2 | 0.29 | 2 115 + 672 | 159 |
| **all** | | **132** | **125** | **5** | **2** | **0.053** | 3 316 + 1 084 | 265 |

Seconds are wall-clock for the agent run plus the check, on the box while the ingest pass
and the injection evaluation shared the model; the agent's own steps took 80–450 s of that.
The check itself cost 1 400–25 000 prompt tokens per answer (one verification call per
claim, each carrying the source posts) — the long answers with 18–20 claims are the expensive
ones.

### Audit of the verifier (by the assistant, not the owner)

All 7 non-supported claims and a random 25 of the 125 supported ones were read against their
deciding posts:

- **25 / 25 supported verdicts hold.** Several are dates («пост 5458 был опубликован 24 июля
  2026 года») or picture descriptions from the caption — the source block carries both.
- **Of the 7 flagged claims, 5 are real errors in the answers**: two day-numbering slips in
  the vape diary («на вторые сутки … сильное ломание» — the post says the first day), two
  dates of posts the answer named without citing them (untraceable → unsupported, correctly),
  and one figure attached to the wrong citation (the "seven days" fact is in post 5595, the
  answer cites 5585 which says "six days for $1 billion" — the verifier calls it contradicted;
  unsupported would be the finer label).
- **The other 2 are a source conflict the answer handled explicitly**: post 2958 says
  «Covenant with Death» is about the First World War, post 5201 says the Second; the answer
  says so («в одном из постов ошибочно указана Вторая мировая») and picks the first. The
  verifier checks each claim against its deciding post and marks the WWI claims contradicted
  by 5201. That is the verifier being literal, not the answer being wrong.

So the honest range is **3.8 % (5/132) to 5.3 % (7/132) of claims not supported by the cited
posts**, and the three real contradictions are slips a reader would notice (a day count,
a figure under the wrong citation) rather than invented facts. Humor answers are the weakest
(4 of 22 claims): they describe memes, and the description leans on the caption's reading of
the picture.

### Digest issue — 10 items, 32 claims

The owner's issue of 2026-09-21 (`faithfulness_eval.py digest --user 0`): each item's title +
one-line reason against its single source post.

| | claims | supported | contradicted | unsupported | share |
|---|---|---|---|---|---|
| 10 items | 32 | 24 | 1 | 7 | **0.250** |

Five times the answers' share — and the audit says most of it is the verifier being literal
with *editorial* phrasing rather than the digest being wrong:

- «Ценители жанра узнают о проблемах „Я — легенда 2“» → unsupported, although post 3458 says
  «„Я — легенда 2“ явно испытывает трудности»; the same for the «До последнего грамма»
  premiere in the same post. The claim is about what readers will learn; the verifier looks
  for a post that says readers learn it.
- «Существует игра или задание „Угадайка по кадру с пирогом“», «зрители могут проверить свою
  эрудицию», «кадр описывается как атмосферный осенний» — three claims extracted from one
  reason for a guess-the-film post whose caption shows the pie and whose text says «для такого
  времени года подходит идеально»: paraphrase, not invention.
- «Читатели узнают себя в ситуации, описанной в меме» — the Editor's flourish, unverifiable
  by construction.
- Real: «Читатели сравнивают новый тизер … с фильмом „Посредник“» — the *author* compares,
  not readers (contradicted, correctly); «„Посредник“ является культовым» rests on the Editor's
  adjective.

On a strict reading **2 of 32 (6 %)** digest claims are not grounded; the measured 25 % is the
price of an Editor who writes "what the reader gets" instead of "what the post says" and an
extractor that turns that framing into claims. Both are prompt changes (`digest_editor.v2`,
`extract_claims.v2`), not built in v1 — recorded here so the next number is comparable.
