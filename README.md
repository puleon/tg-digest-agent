# tg-digest-agent

[![ci](https://github.com/puleon/tg-digest-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/puleon/tg-digest-agent/actions/workflows/ci.yml)

Agentic search and a personalized daily digest over public Telegram channels in three topics —
**science fiction**, **smart humor** and **cinema** — built end to end on **local, CPU-only models**
(AMD Ryzen 9 9950X, 96 GB RAM, no GPU).

The bot is the by-product. The deliverable is the measurement: every component ships with a
numeric quality metric, every experiment is a Langfuse dataset run, and negative results are
reported as such. See [`SPEC.md`](SPEC.md) (Russian) for the full specification and
[`PROGRESS.md`](PROGRESS.md) for the day-by-day engineering log.

## Why these topics are technically interesting

- Two of three domains are mostly **visual** → a VLM/OCR branch is mandatory, not a bonus.
- Humor is hard to find by text → an honest **semantic search** problem where BM25 fails.
- Memes and film stills are reposted en masse → **deduplication** is critical and measurable.
- Films and books are entities with external databases → natural **tool use and grounding**.

## Architecture

```mermaid
flowchart TB
    TG[Telegram MTProto] --> C[Collector · Telethon]
    C --> I[Ingest Agent<br/>classify → VLM/OCR → enrich → dedup]
    C --> PG[(Postgres 16<br/>raw + meta)]
    I --> PG
    I --> Q[(Qdrant<br/>dense + sparse)]
    PG <--> R[Retrieval Layer<br/>hybrid → rerank → query rewrite]
    Q <--> R
    R --> O[LangGraph Orchestrator<br/>router · self-correcting retrieval · tools<br/>digest crew: Planner → Curator → Editor → Critic]
    O --> API[FastAPI]
    O --> BOT[Telegram Bot · 👍/👎 feedback]
    O -.traces, datasets, scores.-> LF[Langfuse]
    LLM[llama-server · GGUF · router mode<br/>fast MoE · heavy MoE on demand] -.OpenAI-compatible API.-> I
    LLM -.-> O
```

## Stack

| Layer | Choice | Why |
|---|---|---|
| Collection | Telethon (MTProto, user account) | Bot API cannot read arbitrary public channels |
| Storage | PostgreSQL 16 + SQLAlchemy 2 | Relational links between posts, clusters, feedback |
| Vectors | Qdrant | Self-hosted, payload filters, hybrid search |
| Embeddings / rerank | BGE-M3 / bge-reranker-v2-m3 | Dense + sparse in one model, strong on Russian |
| Model serving | llama.cpp `llama-server` (router mode, GGUF) | OpenAI-compatible API, prefix caching, on-demand model load/unload; CPU-friendly |
| Fast tier (mass ops + VLM/OCR) | multimodal MoE, ~3B active: Qwen3.6-35B-A3B, Gemma 4 26B-A4B ([D1 benchmark](docs/experiments/d1-llm-benchmark/README.md)) | Memory-bandwidth-bound CPU favors few active params; one weight set for text and images |
| Heavy tier (synthesis, judge) | gpt-oss-120b class, loaded on demand | Quality where it matters; does not fit alongside the rest |
| Orchestration | LangGraph | Explicit state graph, checkpoints, interrupts |
| Observability / eval | Langfuse (self-hosted) | Traces, datasets, experiments, scores |
| Interface | FastAPI + aiogram 3 | |
| Infra | Docker Compose, Makefile, uv | `make up` brings everything up |

## Quickstart

```bash
cp .env.example .env            # fill in secrets and MODELS_DIR
make install                    # uv venv + pre-commit hooks
make up                         # postgres, qdrant, llama-server, langfuse — waits until healthy
make test                       # unit tests
```

Services bind to `127.0.0.1` only. When they run on a remote box, `make tunnel` forwards the UIs
(Langfuse `:3000`, Qdrant `:6333`, LLM `:8080`) and `make remote T=<target>` syncs the tree and
runs a make target there.

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

## Evaluation plan (summary)

Retrieval ablation (BM25 / dense / hybrid / +rerank / +rewrite, and the separate contribution of
OCR and VLM captions on visual topics) · dedup precision/recall on hand-labeled clusters ·
LLM-as-judge validated against human labels (Cohen's κ, position and verbosity bias) · digest
faithfulness via atomic claims · indirect prompt-injection attack success rate before/after
defenses · chaos tests for graceful degradation · agent tool-selection accuracy and step budgets.

Results land in this README as they are produced. **Status: day 1 of 18 — foundation.**

| Experiment | Result |
|---|---|
| [D1 · CPU serving benchmark](docs/experiments/d1-llm-benchmark/README.md) | Qwen3.6-35B-A3B Q4_K_M: 260 tok/s prefill, 19 tok/s generation, 9–28 s per image; Gemma 4 26B-A4B: 204 / 17 / 10.6 s with verbatim Cyrillic OCR at 262 image tokens; gpt-oss-120b MXFP4 (heavy tier): 142 / 17 tok/s at 63 GiB resident. Docker image ≈ native build. SMT and MTP speculative decoding both slower — off. |

## License

MIT
